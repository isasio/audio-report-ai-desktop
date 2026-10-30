from __future__ import annotations

import logging
import os
import subprocess
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

from ..errors import AudioNormalizationError
from ..paths import AppPaths
from .sessions import SessionService

NORMALISED_FILENAME = "normalised.wav"
TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2
NORMALISE_TIMEOUT_SECONDS = 15 * 60


@dataclass(frozen=True)
class NormalisationPlan:
    session_id: str
    source: Path
    destination: Path
    temporary: Path
    had_existing_output: bool


class AudioNormalizer:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        ffmpeg_path: str = "ffmpeg",
        timeout_seconds: float = NORMALISE_TIMEOUT_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._ffmpeg_path = ffmpeg_path
        self._timeout_seconds = timeout_seconds
        self._logger = logger or logging.getLogger(__name__)

    def plan(
        self,
        session_id: str,
        source_path: str | Path | None = None,
    ) -> NormalisationPlan:
        session = self._sessions.get_session(session_id)
        if session is None:
            raise AudioNormalizationError(
                "session_not_found",
                "The selected session no longer exists.",
            )
        if source_path is None:
            if not session.original_audio_path:
                raise AudioNormalizationError(
                    "audio_not_imported",
                    "Import audio into this session before normalising it.",
                )
            source = Path(session.original_audio_path)
        else:
            try:
                source = Path(source_path).expanduser().resolve()
            except (OSError, RuntimeError) as exc:
                raise AudioNormalizationError(
                    "audio_source_missing",
                    "The source audio path is not usable.",
                ) from exc

        if not source.is_file():
            raise AudioNormalizationError(
                "audio_source_missing",
                "The source audio file does not exist.",
            )

        audio_root = self._paths.audio_dir.resolve()
        destination_dir = (self._paths.audio_dir / session.id).resolve()
        if not destination_dir.is_relative_to(audio_root):
            raise AudioNormalizationError(
                "audio_normalisation_failed",
                "The session storage path is not valid.",
            )
        destination = destination_dir / NORMALISED_FILENAME
        had_existing_output = destination.exists()

        try:
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(destination_dir, 0o700)
        except OSError as exc:
            raise AudioNormalizationError(
                "audio_normalisation_failed",
                "Could not prepare the normalised audio directory.",
            ) from exc

        return NormalisationPlan(
            session_id=session.id,
            source=source,
            destination=destination,
            temporary=destination_dir / f".{NORMALISED_FILENAME}.{uuid.uuid4().hex}.tmp.wav",
            had_existing_output=had_existing_output,
        )

    def command(self, plan: NormalisationPlan) -> list[str]:
        return [
            self._ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(plan.source),
            "-map",
            "0:a:0",
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(plan.temporary),
        ]

    def complete(self, plan: NormalisationPlan) -> Path:
        try:
            try:
                os.chmod(plan.temporary, 0o600)
            except OSError as exc:
                raise AudioNormalizationError(
                    "audio_normalisation_failed",
                    "Could not secure the normalised audio file.",
                ) from exc

            duration = _validate_normalised_wav(plan.temporary)
            try:
                os.replace(plan.temporary, plan.destination)
                os.chmod(plan.destination, 0o600)
            except OSError as exc:
                raise AudioNormalizationError(
                    "audio_normalisation_failed",
                    "Could not store the normalised audio file.",
                ) from exc

            try:
                self._sessions.mark_normalised(plan.session_id)
            except Exception:
                if not plan.had_existing_output:
                    try:
                        plan.destination.unlink()
                    except OSError:
                        pass
                raise

            self._logger.info(
                "audio_normalised session_id=%s duration_seconds=%.3f",
                plan.session_id,
                duration,
            )
            return plan.destination
        finally:
            self.abort(plan)

    def abort(self, plan: NormalisationPlan) -> None:
        try:
            plan.temporary.unlink(missing_ok=True)
        except OSError:
            pass

    def normalise(
        self,
        session_id: str,
        source_path: str | Path | None = None,
    ) -> Path:
        plan = self.plan(session_id, source_path)
        try:
            try:
                completed = subprocess.run(
                    self.command(plan),
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise AudioNormalizationError(
                    "ffmpeg_missing",
                    "FFmpeg is not available. Run ./install.sh to check system dependencies.",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise AudioNormalizationError(
                    "audio_normalisation_timeout",
                    "Timed out while normalising the audio.",
                ) from exc
            except OSError as exc:
                raise AudioNormalizationError(
                    "audio_normalisation_failed",
                    "Could not run FFmpeg to normalise the audio.",
                ) from exc

            if completed.returncode != 0:
                raise AudioNormalizationError(
                    "audio_normalisation_failed",
                    "FFmpeg could not normalise the selected audio.",
                )

            return self.complete(plan)
        finally:
            self.abort(plan)


def _validate_normalised_wav(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_rate = handle.getframerate()
            sample_width = handle.getsampwidth()
            compression = handle.getcomptype()
            frame_count = handle.getnframes()
    except (EOFError, OSError, wave.Error) as exc:
        raise AudioNormalizationError(
            "normalised_audio_invalid",
            "FFmpeg produced an unreadable WAV file.",
        ) from exc

    if (
        channels != TARGET_CHANNELS
        or sample_rate != TARGET_SAMPLE_RATE
        or sample_width != TARGET_SAMPLE_WIDTH
        or compression != "NONE"
        or frame_count <= 0
    ):
        raise AudioNormalizationError(
            "normalised_audio_invalid",
            "FFmpeg did not produce mono 16 kHz 16-bit PCM audio.",
        )

    duration = frame_count / sample_rate
    if duration <= 0:
        raise AudioNormalizationError(
            "normalised_audio_invalid",
            "The normalised audio has no usable duration.",
        )
    return duration
