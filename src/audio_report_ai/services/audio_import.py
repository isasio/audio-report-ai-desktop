from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..database.repositories import Session
from ..errors import AudioImportError
from ..paths import AppPaths
from .sessions import SessionService

SUPPORTED_AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg"})
MAX_IMPORT_BYTES = 2 * 1024**3
MAX_IMPORT_DURATION_SECONDS = 8 * 60 * 60
PROBE_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class AudioProbe:
    duration_seconds: float
    format_name: str
    codec_name: str | None
    sample_rate: int | None
    channels: int | None


class AudioImportService:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        ffprobe_path: str = "ffprobe",
        max_bytes: int = MAX_IMPORT_BYTES,
        max_duration_seconds: float = MAX_IMPORT_DURATION_SECONDS,
        probe_timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._ffprobe_path = ffprobe_path
        self._max_bytes = max_bytes
        self._max_duration_seconds = max_duration_seconds
        self._probe_timeout_seconds = probe_timeout_seconds
        self._logger = logger or logging.getLogger(__name__)

    def import_audio(self, session_id: str, source_path: str | Path) -> Session:
        session = self._sessions.get_session(session_id)
        if session is None:
            raise AudioImportError("session_not_found", "The selected session no longer exists.")
        if session.original_audio_path:
            raise AudioImportError(
                "audio_already_imported",
                "This session already has audio. Delete the session to import different audio.",
            )

        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise AudioImportError("audio_not_found", "The selected audio file does not exist.")

        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS))
            raise AudioImportError(
                "audio_unsupported_format",
                f"Unsupported audio format. Supported formats: {supported}.",
            )

        try:
            size = source.stat().st_size
        except OSError as exc:
            raise AudioImportError(
                "audio_unreadable",
                "The selected audio file cannot be read.",
            ) from exc

        if size == 0:
            raise AudioImportError("audio_empty", "The selected audio file is empty.")
        if size > self._max_bytes:
            limit_mb = self._max_bytes // (1024 * 1024)
            raise AudioImportError(
                "audio_too_large",
                f"The selected audio file exceeds the {limit_mb} MB limit.",
            )

        probe = self._probe(source)
        if probe.duration_seconds > self._max_duration_seconds:
            limit_hours = self._max_duration_seconds / 3600
            raise AudioImportError(
                "audio_too_long",
                f"The selected audio file exceeds the {limit_hours:g} hour limit.",
            )

        audio_root = self._paths.audio_dir.resolve()
        destination_dir = (self._paths.audio_dir / session.id).resolve()
        if not destination_dir.is_relative_to(audio_root):
            raise AudioImportError(
                "audio_import_failed",
                "The session storage path is not valid.",
            )
        destination = destination_dir / f"original{suffix}"
        try:
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(destination_dir, 0o700)
            if any(destination_dir.glob("original.*")):
                raise AudioImportError(
                    "audio_already_imported",
                    "This session already has stored audio.",
                )
            temporary = destination_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"
            try:
                shutil.copyfile(source, temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
        except AudioImportError:
            raise
        except OSError as exc:
            raise AudioImportError(
                "audio_copy_failed",
                "Could not store the imported audio in the application data directory.",
            ) from exc

        try:
            updated = self._sessions.attach_audio(
                session.id,
                str(destination),
                probe.duration_seconds,
            )
        except Exception:
            try:
                destination.unlink()
            except OSError:
                pass
            raise

        self._logger.info(
            "audio_imported session_id=%s duration_seconds=%.3f format=%s",
            updated.id,
            probe.duration_seconds,
            probe.format_name,
        )
        return updated

    def _probe(self, source: Path) -> AudioProbe:
        command = [
            self._ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name,sample_rate,channels,duration",
            "-of",
            "json",
            str(source),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._probe_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AudioImportError(
                "ffprobe_missing",
                "FFprobe is not available. Run ./install.sh to check system dependencies.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioImportError(
                "audio_probe_timeout",
                "Timed out while reading the selected audio file.",
            ) from exc
        except OSError as exc:
            raise AudioImportError(
                "audio_probe_failed",
                "Could not run FFprobe to validate the selected audio file.",
            ) from exc

        if completed.returncode != 0:
            raise AudioImportError(
                "audio_invalid",
                "The selected file could not be read as audio.",
            )

        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise AudioImportError(
                "audio_invalid",
                "FFprobe returned an unreadable audio description.",
            ) from exc

        if not isinstance(payload, dict):
            raise AudioImportError(
                "audio_invalid",
                "FFprobe returned an unreadable audio description.",
            )
        streams = payload.get("streams")
        if not isinstance(streams, list) or not streams:
            raise AudioImportError(
                "audio_no_audio_stream",
                "The selected file does not contain an audio stream.",
            )
        stream = streams[0]
        if not isinstance(stream, dict) or stream.get("codec_type") != "audio":
            raise AudioImportError(
                "audio_no_audio_stream",
                "The selected file does not contain an audio stream.",
            )

        format_info = payload.get("format")
        format_name = "unknown"
        duration_value = None
        if isinstance(format_info, dict):
            format_name_value = format_info.get("format_name")
            if isinstance(format_name_value, str) and format_name_value:
                format_name = format_name_value
            duration_value = format_info.get("duration")

        duration = _parse_positive_float(duration_value)
        if duration is None:
            duration = _parse_positive_float(stream.get("duration"))
        if duration is None:
            raise AudioImportError(
                "audio_invalid_duration",
                "The selected audio file has no usable duration.",
            )

        codec_name = stream.get("codec_name")
        return AudioProbe(
            duration_seconds=duration,
            format_name=format_name,
            codec_name=codec_name if isinstance(codec_name, str) else None,
            sample_rate=_parse_optional_int(stream.get("sample_rate")),
            channels=_parse_optional_int(stream.get("channels")),
        )


def _parse_positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _parse_optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
