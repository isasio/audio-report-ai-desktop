from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

from ..database.repositories import Session
from ..errors import AudioRecordingError
from ..paths import AppPaths
from .sessions import SessionService

RECORDING_FILENAME = "original.wav"
RECORDING_CHANNELS = 1
RECORDING_SAMPLE_WIDTH = 2
DEFAULT_BLOCK_SIZE = 2048


class MicrophoneRecorder:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        sample_rate: int | None = None,
        block_size: int = DEFAULT_BLOCK_SIZE,
        device: str | int | None = None,
        stream_factory: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._sample_rate = sample_rate
        self._block_size = block_size
        self._device = device
        self._stream_factory = stream_factory
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._active = False
        self._session_id: str | None = None
        self._started_at: float | None = None
        self._temporary: Path | None = None
        self._destination: Path | None = None
        self._wave: wave.Wave_write | None = None
        self._queue: queue.SimpleQueue[bytes | None] | None = None
        self._writer: threading.Thread | None = None
        self._stream: Any | None = None
        self._writer_error: str | None = None

    @property
    def is_recording(self) -> bool:
        return self._active

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def elapsed_seconds(self) -> float:
        if not self._active or self._started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def start(self, session_id: str) -> None:
        with self._lock:
            if self._active:
                raise AudioRecordingError(
                    "recording_already_active",
                    "A recording is already in progress.",
                )
            session = self._sessions.get_session(session_id)
            if session is None:
                raise AudioRecordingError(
                    "session_not_found",
                    "The selected session no longer exists.",
                )
            if session.original_audio_path:
                raise AudioRecordingError(
                    "audio_already_imported",
                    "This session already has audio. Delete the session to record new audio.",
                )

            audio_root = self._paths.audio_dir.resolve()
            destination_dir = (self._paths.audio_dir / session.id).resolve()
            if not destination_dir.is_relative_to(audio_root):
                raise AudioRecordingError(
                    "recording_failed",
                    "The session storage path is not valid.",
                )
            destination = destination_dir / RECORDING_FILENAME
            if destination.exists() or any(destination_dir.glob("original.*")):
                raise AudioRecordingError(
                    "audio_already_imported",
                    "This session already has stored audio.",
                )

            sample_rate = self._resolve_sample_rate()
            temporary = destination_dir / f".{RECORDING_FILENAME}.{uuid.uuid4().hex}.tmp"
            try:
                destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(destination_dir, 0o700)
                wave_file = wave.open(str(temporary), "wb")
                wave_file.setnchannels(RECORDING_CHANNELS)
                wave_file.setsampwidth(RECORDING_SAMPLE_WIDTH)
                wave_file.setframerate(sample_rate)
            except (OSError, wave.Error) as exc:
                _unlink(temporary)
                raise AudioRecordingError(
                    "recording_failed",
                    "Could not prepare the microphone recording.",
                ) from exc

            frame_queue: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
            writer = threading.Thread(
                target=self._write_frames,
                args=(frame_queue, wave_file),
                name="audio-recorder-writer",
                daemon=True,
            )
            self._temporary = temporary
            self._destination = destination
            self._wave = wave_file
            self._queue = frame_queue
            self._writer = writer
            self._session_id = session.id
            self._started_at = time.monotonic()
            self._writer_error = None
            writer.start()

            stream: Any | None = None
            try:
                stream = self._open_stream(sample_rate)
                self._stream = stream
                stream.start()
            except Exception as exc:
                self._writer = None
                self._queue = None
                self._wave = None
                self._temporary = None
                self._destination = None
                self._session_id = None
                self._started_at = None
                if stream is not None:
                    _close_stream(stream)
                frame_queue.put(None)
                writer.join(timeout=2.0)
                try:
                    wave_file.close()
                except (OSError, wave.Error):
                    pass
                _unlink(temporary)
                if isinstance(exc, ImportError):
                    raise AudioRecordingError(
                        "sounddevice_missing",
                        "Microphone recording requires sounddevice. Re-run ./install.sh.",
                    ) from exc
                raise AudioRecordingError(
                    "microphone_unavailable",
                    (
                        "Could not start microphone recording. "
                        "Check that a microphone is connected and not in use."
                    ),
                ) from exc

            self._active = True
            self._logger.info("audio_recording_started session_id=%s", session.id)

    def stop(self) -> Session:
        session = self._finish(discard=False)
        if session is None:
            raise AudioRecordingError(
                "recording_not_active",
                "There is no active recording to stop.",
            )
        return session

    def cancel(self) -> None:
        try:
            self._finish(discard=True)
        except AudioRecordingError:
            self._logger.warning("audio_recording_cancel_failed")

    def _finish(self, discard: bool) -> Session | None:
        with self._lock:
            if not self._active:
                if discard:
                    return None
                raise AudioRecordingError(
                    "recording_not_active",
                    "There is no active recording to stop.",
                )
            self._active = False
            session_id = self._session_id or ""
            temporary = self._temporary
            destination = self._destination
            wave_file = self._wave
            frame_queue = self._queue
            writer = self._writer
            stream = self._stream
            self._stream = None
            self._queue = None
            self._wave = None
            self._temporary = None
            self._destination = None
            self._session_id = None
            self._started_at = None

            stop_error: Exception | None = None
            if stream is not None:
                try:
                    stream.stop()
                except Exception as exc:
                    stop_error = exc
                finally:
                    _close_stream(stream)
            if frame_queue is not None:
                frame_queue.put(None)
            if writer is not None:
                writer.join(timeout=5.0)
            if wave_file is not None:
                try:
                    wave_file.close()
                except (OSError, wave.Error) as exc:
                    stop_error = stop_error or exc

            if discard:
                _unlink(temporary)
                self._logger.info("audio_recording_cancelled session_id=%s", session_id)
                return None
            if (
                temporary is None
                or destination is None
                or writer is None
                or writer.is_alive()
                or self._writer_error
                or stop_error is not None
            ):
                _unlink(temporary)
                raise AudioRecordingError(
                    "recording_failed",
                    "Could not finish the microphone recording.",
                )
            try:
                duration = _recording_duration(temporary)
            except (OSError, wave.Error) as exc:
                _unlink(temporary)
                raise AudioRecordingError(
                    "recording_invalid",
                    "The microphone recording did not contain valid audio.",
                ) from exc
            if duration <= 0:
                _unlink(temporary)
                raise AudioRecordingError(
                    "recording_empty",
                    "The microphone recording did not contain any audio.",
                )

            try:
                os.chmod(temporary, 0o600)
                if destination.exists():
                    raise AudioRecordingError(
                        "audio_already_imported",
                        "This session already has stored audio.",
                    )
                os.replace(temporary, destination)
                os.chmod(destination, 0o600)
            except AudioRecordingError:
                _unlink(temporary)
                raise
            except OSError as exc:
                _unlink(temporary)
                raise AudioRecordingError(
                    "recording_failed",
                    "Could not store the microphone recording.",
                ) from exc

            try:
                updated = self._sessions.attach_audio(
                    session_id,
                    str(destination),
                    duration,
                )
            except Exception:
                _unlink(destination)
                raise
            self._logger.info(
                "audio_recorded session_id=%s duration_seconds=%.3f",
                updated.id,
                duration,
            )
            return updated

    def _resolve_sample_rate(self) -> int:
        if self._sample_rate is not None:
            if self._sample_rate <= 0:
                raise AudioRecordingError(
                    "recording_failed",
                    "The microphone sample rate is not valid.",
                )
            return self._sample_rate
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise AudioRecordingError(
                "sounddevice_missing",
                "Microphone recording requires sounddevice. Re-run ./install.sh.",
            ) from exc
        try:
            info = sd.query_devices(self._device, "input")
            sample_rate = int(round(float(info["default_samplerate"])))
            if int(info["max_input_channels"]) < 1:
                raise ValueError("No input channels.")
        except Exception as exc:
            raise AudioRecordingError(
                "microphone_unavailable",
                (
                    "No usable microphone input device was found. "
                    "Connect a microphone and check the system audio settings."
                ),
            ) from exc
        if sample_rate <= 0:
            raise AudioRecordingError(
                "microphone_unavailable",
                "The microphone did not report a usable sample rate.",
            )
        return sample_rate

    def _open_stream(self, sample_rate: int) -> Any:
        if self._stream_factory is not None:
            return self._stream_factory(
                samplerate=sample_rate,
                channels=RECORDING_CHANNELS,
                dtype="int16",
                blocksize=self._block_size,
                device=self._device,
                callback=self._on_audio,
            )
        import sounddevice as sd

        return sd.InputStream(
            samplerate=sample_rate,
            channels=RECORDING_CHANNELS,
            dtype="int16",
            blocksize=self._block_size,
            device=self._device,
            callback=self._on_audio,
        )

    def _on_audio(self, indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
        frame_queue = self._queue
        if frame_queue is None:
            return
        try:
            frame_queue.put(indata.tobytes())
        except Exception:
            pass

    def _write_frames(
        self,
        frame_queue: queue.SimpleQueue[bytes | None],
        wave_file: wave.Wave_write,
    ) -> None:
        while True:
            data = frame_queue.get()
            if data is None:
                return
            try:
                wave_file.writeframes(data)
            except Exception as exc:
                self._writer_error = f"{type(exc).__name__}: {exc}"
                return


def _recording_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != RECORDING_CHANNELS:
            raise wave.Error("Unexpected recording channel count.")
        if handle.getsampwidth() != RECORDING_SAMPLE_WIDTH:
            raise wave.Error("Unexpected recording sample width.")
        frames = handle.getnframes()
        rate = handle.getframerate()
    if rate <= 0:
        raise wave.Error("Invalid recording sample rate.")
    return frames / rate


def _close_stream(stream: Any) -> None:
    try:
        stream.close()
    except Exception:
        pass


def _unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
