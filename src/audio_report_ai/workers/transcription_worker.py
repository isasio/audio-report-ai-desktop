from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path

from ..schemas.transcript import Transcript, TranscriptSegment
from ..services.model_locator import missing_model_files

LOGGER = logging.getLogger("audio_report_ai.worker")


class WorkerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _audio_duration(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as handle:
            frame_count = handle.getnframes()
            sample_rate = handle.getframerate()
    except (EOFError, OSError, wave.Error) as exc:
        raise WorkerError(
            "audio_invalid",
            "The normalised audio could not be read.",
        ) from exc
    if frame_count <= 0 or sample_rate <= 0:
        raise WorkerError("audio_invalid", "The normalised audio has no usable duration.")
    return frame_count / sample_rate


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _write_status(
    status_path: Path,
    status: str,
    progress: float,
    **fields: object,
) -> None:
    payload = {
        "status": status,
        "progress": max(0.0, min(1.0, float(progress))),
        **fields,
    }
    _atomic_write_json(status_path, payload)


def _load_model(model_dir: Path, device: str, compute_type: str) -> object:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WorkerError(
            "transcription_dependency_missing",
            "Local transcription dependencies are unavailable. Run ./install.sh.",
        ) from exc
    return WhisperModel(
        str(model_dir),
        device=device,
        compute_type=compute_type,
        local_files_only=True,
    )


def _transcribe_with_model(
    model: object,
    audio_path: Path,
    language: str,
    duration: float,
    status_path: Path,
) -> tuple[list[TranscriptSegment], object]:
    language_argument = None if language == "auto" else language
    segments, info = model.transcribe(
        str(audio_path),
        language=language_argument,
        task="transcribe",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,
        condition_on_previous_text=True,
    )

    records: list[TranscriptSegment] = []
    last_progress = 0.0
    for raw_segment in segments:
        text = str(raw_segment.text).strip()
        if not text:
            continue
        start = max(0.0, float(raw_segment.start))
        end = max(start, float(raw_segment.end))
        records.append(
            TranscriptSegment(
                id=str(uuid.uuid4()),
                index=len(records),
                start=start,
                end=end,
                text=text,
                avg_logprob=getattr(raw_segment, "avg_logprob", None),
                no_speech_prob=getattr(raw_segment, "no_speech_prob", None),
                compression_ratio=getattr(raw_segment, "compression_ratio", None),
            )
        )
        progress = min(0.99, 0.1 + 0.89 * (end / duration))
        if progress - last_progress >= 0.01:
            _write_status(status_path, "transcribing", progress)
            last_progress = progress

    return records, info


def transcribe(
    audio_path: Path,
    model_dir: Path,
    model_name: str,
    language: str,
    device: str,
    session_id: str,
    status_path: Path,
) -> Transcript:
    if not audio_path.is_file():
        raise WorkerError("audio_missing", "The normalised audio file does not exist.")
    if not model_dir.is_dir():
        raise WorkerError(
            "model_missing",
            "The local transcription model is not installed. Run the model setup command.",
        )
    missing = missing_model_files(model_dir)
    if missing:
        raise WorkerError(
            "model_incomplete",
            "The local transcription model is incomplete. Re-run the model setup command.",
        )

    duration = _audio_duration(audio_path)
    attempts = [("cpu", "int8")] if device == "cpu" else [("cuda", "int8_float16"), ("cpu", "int8")]

    last_error: Exception | None = None
    for attempt_device, compute_type in attempts:
        _write_status(
            status_path,
            "loading_model",
            0.02,
            device=attempt_device,
        )
        try:
            model = _load_model(model_dir, attempt_device, compute_type)
            _write_status(
                status_path,
                "transcribing",
                0.05,
                device=attempt_device,
            )
            records, info = _transcribe_with_model(
                model,
                audio_path,
                language,
                duration,
                status_path,
            )
            detected_language = getattr(info, "language", None)
            language_probability = getattr(info, "language_probability", None)
            return Transcript(
                session_id=session_id,
                language=detected_language or (None if language == "auto" else language),
                language_probability=language_probability,
                duration_seconds=duration,
                model=model_name,
                device=attempt_device,
                compute_type=compute_type,
                created_at=_utc_now(),
                segments=records,
            )
        except WorkerError:
            raise
        except Exception as exc:
            last_error = exc
            LOGGER.warning(
                "transcription_attempt_failed device=%s error_type=%s",
                attempt_device,
                type(exc).__name__,
            )

    raise WorkerError(
        "transcription_failed",
        "Local transcription failed. Check the audio and local model installation.",
    ) from last_error


def run_worker(args: argparse.Namespace) -> Transcript:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    status_path = Path(args.status)
    result_path = Path(args.result)
    _write_status(status_path, "starting", 0.0)
    result = transcribe(
        audio_path=Path(args.audio),
        model_dir=Path(args.model_dir),
        model_name=args.model_name,
        language=args.language,
        device=args.device,
        session_id=args.session_id,
        status_path=status_path,
    )
    _atomic_write_json(result_path, result.model_dump(mode="json"))
    _write_status(
        status_path,
        "completed",
        1.0,
        device=result.device,
        segment_count=len(result.segments),
    )
    LOGGER.info(
        "transcription_worker_complete segments=%d device=%s duration_seconds=%.3f",
        len(result.segments),
        result.device,
        result.duration_seconds,
    )
    return result


def _safe_failure_status(status_path: Path, code: str, message: str) -> None:
    try:
        _write_status(
            status_path,
            "failed",
            0.0,
            error_code=code,
            error_message=message,
        )
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local faster-whisper worker.")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--status", required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s %(message)s",
    )
    try:
        run_worker(args)
        return 0
    except WorkerError as exc:
        _safe_failure_status(Path(args.status), exc.code, exc.message)
        print(f"Error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:
        _safe_failure_status(
            Path(args.status),
            "transcription_failed",
            "Local transcription failed.",
        )
        LOGGER.error("transcription_worker_failed error_type=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
