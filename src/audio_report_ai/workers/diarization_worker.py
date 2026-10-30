from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path

from ..schemas.diarization import DiarizationResult, DiarizationTurn
from ..services.diarization_model import missing_diarization_files

LOGGER = logging.getLogger("audio_report_ai.diarization_worker")


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
    _atomic_write_json(
        status_path,
        {
            "status": status,
            "progress": max(0.0, min(1.0, float(progress))),
            **fields,
        },
    )


def _select_annotation(output: object) -> object:
    for attribute in ("exclusive_speaker_diarization", "speaker_diarization"):
        annotation = getattr(output, attribute, None)
        if annotation is not None:
            return annotation
    return output


def _iter_tracks(annotation: object) -> list[tuple[object, object]]:
    if hasattr(annotation, "itertracks"):
        return [(turn, label) for turn, _, label in annotation.itertracks(yield_label=True)]
    tracks: list[tuple[object, object]] = []
    for item in annotation:
        if not isinstance(item, tuple) or len(item) != 2:
            raise WorkerError(
                "diarization_invalid_result",
                "The local diarization model returned an unreadable result.",
            )
        tracks.append((item[0], item[1]))
    return tracks


def _build_turns(annotation: object, duration_seconds: float) -> list[DiarizationTurn]:
    speaker_by_label: dict[str, str] = {}
    turns: list[DiarizationTurn] = []
    for track, raw_label in _iter_tracks(annotation):
        label = str(raw_label)
        speaker_id = speaker_by_label.setdefault(
            label,
            f"SPEAKER_{len(speaker_by_label):02d}",
        )
        try:
            start = float(track.start)
            end = float(track.end)
        except (AttributeError, TypeError, ValueError) as exc:
            raise WorkerError(
                "diarization_invalid_result",
                "The local diarization model returned invalid timestamps.",
            ) from exc
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = max(0.0, min(duration_seconds, start))
        end = max(0.0, min(duration_seconds, end))
        if end <= start:
            continue
        turns.append(DiarizationTurn(speaker_id=speaker_id, start=start, end=end))
    return turns


def _load_pipeline(model_dir: Path, device: str) -> object:
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise WorkerError(
            "diarization_dependency_missing",
            (
                "Local speaker diarization dependencies are unavailable. "
                "Install the optional diarization dependencies."
            ),
        ) from exc

    pipeline = Pipeline.from_pretrained(str(model_dir))
    if device != "cpu":
        try:
            import torch
        except ImportError as exc:
            raise WorkerError(
                "diarization_dependency_missing",
                "PyTorch is required for speaker diarization.",
            ) from exc
        pipeline.to(torch.device(device))
    return pipeline


def diarize(
    audio_path: Path,
    model_dir: Path,
    model_name: str,
    device: str,
    session_id: str,
    status_path: Path,
) -> DiarizationResult:
    if not audio_path.is_file():
        raise WorkerError("audio_missing", "The normalised audio file does not exist.")
    if not model_dir.is_dir():
        raise WorkerError(
            "diarization_model_missing",
            "The local speaker diarization model is not installed.",
        )
    missing = missing_diarization_files(model_dir)
    if missing:
        raise WorkerError(
            "diarization_model_incomplete",
            "The local speaker diarization model is incomplete.",
        )

    duration = _audio_duration(audio_path)
    attempts = ["cpu"] if device == "cpu" else ["cuda", "cpu"]
    last_error: Exception | None = None
    for attempt_device in attempts:
        _write_status(
            status_path,
            "loading_model",
            0.02,
            device=attempt_device,
        )
        try:
            pipeline = _load_pipeline(model_dir, attempt_device)
            _write_status(status_path, "diarizing", 0.1, device=attempt_device)
            output = pipeline(str(audio_path))
            annotation = _select_annotation(output)
            turns = _build_turns(annotation, duration)
            return DiarizationResult(
                session_id=session_id,
                model=model_name,
                device=attempt_device,
                created_at=_utc_now(),
                duration_seconds=duration,
                turns=turns,
            )
        except WorkerError:
            raise
        except Exception as exc:
            last_error = exc
            LOGGER.warning(
                "diarization_attempt_failed device=%s error_type=%s",
                attempt_device,
                type(exc).__name__,
            )

    raise WorkerError(
        "diarization_failed",
        "Local speaker diarization failed. Check the audio and local model installation.",
    ) from last_error


def run_worker(args: argparse.Namespace) -> DiarizationResult:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    status_path = Path(args.status)
    result_path = Path(args.result)
    _write_status(status_path, "starting", 0.0)
    result = diarize(
        audio_path=Path(args.audio),
        model_dir=Path(args.model_dir),
        model_name=args.model_name,
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
        turn_count=len(result.turns),
    )
    LOGGER.info(
        "diarization_worker_complete turns=%d speakers=%d device=%s duration_seconds=%.3f",
        len(result.turns),
        len({turn.speaker_id for turn in result.turns}),
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
    parser = argparse.ArgumentParser(description="Local pyannote diarization worker.")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-name", required=True)
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
            "diarization_failed",
            "Local speaker diarization failed.",
        )
        LOGGER.error("diarization_worker_failed error_type=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
