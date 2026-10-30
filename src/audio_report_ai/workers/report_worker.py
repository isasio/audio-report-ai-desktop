from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

from ..errors import AppError
from ..schemas.report_generation import ReportGenerationSource
from ..services.local_llm_client import LocalLLMClient
from ..services.report_generator import generate_report

LOGGER = logging.getLogger("audio_report_ai.report_worker")


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


def run_worker(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    result_path = Path(args.result)
    status_path = Path(args.status)
    _write_status(status_path, "starting", 0.0)
    try:
        source = ReportGenerationSource.model_validate_json(input_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise AppError(
            "report_input_invalid",
            "The prepared report input is missing or invalid.",
        ) from exc

    client = LocalLLMClient(args.model)
    _write_status(status_path, "verifying_model", 0.02, model=args.model)
    client.verify_model()
    _write_status(status_path, "generating", 0.05, model=args.model)

    def progress(message: str, fraction: float) -> None:
        _write_status(
            status_path,
            "generating",
            0.05 + (0.9 * max(0.0, min(1.0, fraction))),
            model=args.model,
            message=message,
        )

    report = generate_report(
        client,
        source,
        progress,
        language=args.language,
        length=args.length,
        profile=args.profile,
    )
    _atomic_write_json(result_path, report.model_dump(mode="json"))
    _write_status(
        status_path,
        "completed",
        1.0,
        model=args.model,
        review_flag_count=len(report.items_requiring_review),
    )
    LOGGER.info(
        "report_worker_complete session_id=%s review_flags=%d",
        source.session_id,
        len(report.items_requiring_review),
    )


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
    parser = argparse.ArgumentParser(description="Local report generation worker.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--length", required=True)
    parser.add_argument("--profile", required=True)
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
    except AppError as exc:
        _safe_failure_status(Path(args.status), exc.code, exc.message)
        print(f"Error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:
        _safe_failure_status(
            Path(args.status),
            "report_failed",
            "Local report generation failed.",
        )
        LOGGER.error("report_worker_failed error_type=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
