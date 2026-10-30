from __future__ import annotations

import ipaddress
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import uuid
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from docx import Document

from ..app import build_context
from ..config import AppConfig, save_config
from ..errors import AppError
from ..paths import AppPaths, ensure_directories, resolve_paths
from ..schemas.transcript import Transcript, TranscriptSegment
from .local_llm_client import LocalLLMClient
from .report_generator import generate_report
from .reports import REPORT_STATUS_APPROVED

SMOKE_SESSION_TITLE = "Offline smoke test (synthetic)"


class OutboundNetworkBlocked(OSError):
    pass


@dataclass(frozen=True)
class SmokeStep:
    name: str
    detail: str


@dataclass(frozen=True)
class SmokeResult:
    session_id: str
    report_id: str
    report_version: int
    export_path: Path
    segment_count: int
    steps: tuple[SmokeStep, ...]
    mode: str = "deterministic"


def isolated_paths(
    root: str | Path,
    *,
    models_dir: str | Path | None = None,
) -> AppPaths:
    base = Path(root).expanduser().resolve()
    data_dir = base / "data"
    config_dir = base / "config"
    cache_dir = base / "cache"
    state_dir = base / "state"
    logs_dir = state_dir / "logs"
    resolved_models = (
        Path(models_dir).expanduser().resolve() if models_dir is not None else data_dir / "models"
    )
    return AppPaths(
        data_dir=data_dir,
        audio_dir=data_dir / "audio",
        transcripts_dir=data_dir / "transcripts",
        reports_dir=data_dir / "reports",
        exports_dir=data_dir / "exports",
        models_dir=resolved_models,
        config_dir=config_dir,
        cache_dir=cache_dir,
        jobs_dir=cache_dir / "jobs",
        state_dir=state_dir,
        log_dir=logs_dir,
        database_path=data_dir / "database.sqlite3",
        config_path=config_dir / "config.json",
        log_path=logs_dir / "app.log",
    )


def _is_loopback_host(host: object) -> bool:
    if not isinstance(host, str):
        return False
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@contextmanager
def loopback_only_network() -> Iterator[None]:
    """Fail fast if this process attempts a non-loopback TCP connection.

    This guard covers the current process, including urllib and any other
    library that uses ``socket.connect``. Child processes must be monitored
    separately (for example with strace).
    """

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(sock: socket.socket, address: object) -> None:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            host = address[0] if isinstance(address, tuple) and address else None
            if not _is_loopback_host(host):
                raise OutboundNetworkBlocked(
                    f"Blocked non-loopback connection attempt to {host!r}."
                )
        return original_connect(sock, address)  # type: ignore[arg-type]

    def guarded_connect_ex(sock: socket.socket, address: object) -> int:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            host = address[0] if isinstance(address, tuple) and address else None
            if not _is_loopback_host(host):
                raise OutboundNetworkBlocked(
                    f"Blocked non-loopback connection attempt to {host!r}."
                )
        return original_connect_ex(sock, address)  # type: ignore[arg-type]

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]


def _write_tone_wav(path: Path, duration_seconds: float = 2.4) -> Path:
    sample_rate = 16_000
    frame_count = int(duration_seconds * sample_rate)
    frames = bytearray()
    for index in range(frame_count):
        # A quiet 440 Hz tone keeps the fixture valid without real speech.
        value = int(3000 * (1 if (index // 40) % 2 == 0 else -1))
        frames.extend(value.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return path


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def _report_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "title": "Synthetic offline smoke report",
        "date": None,
        "participants": [
            {
                "name": "Ana",
                "role": "Host",
                "supporting_segment_ids": ["smoke-seg-0"],
            }
        ],
        "executive_summary": "The synthetic team approved the October launch.",
        "main_topics": [
            {
                "title": "October launch",
                "summary": "The synthetic team approved the October launch.",
                "supporting_segment_ids": ["smoke-seg-0"],
            }
        ],
        "important_points": [],
        "decisions": [
            {
                "description": "Approved the October launch.",
                "supporting_segment_ids": ["smoke-seg-0"],
            }
        ],
        "action_items": [
            {
                "description": "Prepare the release checklist.",
                "responsible_person": "Luis",
                "deadline": "viernes",
                "supporting_segment_ids": ["smoke-seg-1"],
            }
        ],
        "unresolved_questions": [],
        "conclusion": "The synthetic smoke path completed locally.",
        "items_requiring_review": [],
    }


def _fake_transcript(session_id: str, duration: float) -> Transcript:
    return Transcript(
        session_id=session_id,
        language="es",
        language_probability=1.0,
        duration_seconds=duration,
        model="synthetic-smoke",
        device="cpu",
        compute_type="int8",
        created_at="2026-01-01T00:00:00Z",
        segments=[
            TranscriptSegment(
                id="smoke-seg-0",
                index=0,
                start=0.0,
                end=min(1.0, duration),
                text="Ana: Aprobamos el lanzamiento de octubre.",
                speaker_id="SPEAKER_00",
            ),
            TranscriptSegment(
                id="smoke-seg-1",
                index=1,
                start=min(1.0, duration),
                end=max(min(2.0, duration), 0.1),
                text="Luis: Prepararé la lista antes del viernes.",
                speaker_id="SPEAKER_01",
            ),
        ],
    )


def run_offline_smoke(root: str | Path | None = None) -> SmokeResult:
    """Run the import → normalise → report → export path without models.

    The harness uses synthetic audio and a deterministic report payload. It
    exercises the real database, transcript, validation, approval, and DOCX
    code, plus the loopback-only network guard. It does not claim to test ASR
    or LLM quality; use ``run_live_smoke`` for local model inference.
    """

    if root is None:
        with tempfile.TemporaryDirectory(prefix="audio-report-ai-smoke-") as temporary:
            return run_offline_smoke(Path(temporary))

    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    paths = isolated_paths(base)
    ensure_directories(paths)
    config = AppConfig(whisper_device="cpu")
    save_config(config, paths)

    steps: list[SmokeStep] = []
    with loopback_only_network():
        context = build_context(paths, config)
        try:
            source_audio = _write_tone_wav(base / "synthetic-smoke.wav")
            session = context.session_service.create_session(SMOKE_SESSION_TITLE)
            context.audio_import_service.import_audio(session.id, source_audio)
            steps.append(SmokeStep("import", "Synthetic WAV validated and copied."))

            normalised = context.audio_normalizer.normalise(session.id)
            duration = _wav_duration(normalised)
            steps.append(
                SmokeStep("normalise", f"Mono 16 kHz PCM WAV validated ({duration:.2f} s).")
            )

            transcript = _fake_transcript(session.id, duration)
            context.transcription_service.persist_result(session.id, transcript)
            context.transcription_service.save_correction(
                session.id,
                "smoke-seg-1",
                "Luis: Prepararé la lista de verificación antes del viernes.",
            )
            steps.append(
                SmokeStep(
                    "transcript",
                    f"{len(transcript.segments)} synthetic segments and correction stored.",
                )
            )

            source = context.report_service.build_source(session.id)
            if source.segments[1].text != (
                "Luis: Prepararé la lista de verificación antes del viernes."
            ):
                raise AppError(
                    "offline_smoke_failed",
                    "The corrected transcript was not used for report generation.",
                )
            report = context.report_service.save_generated_report(
                session.id,
                _report_payload(),
            )
            approved = context.report_service.approve_report(report.id)
            if approved.status != REPORT_STATUS_APPROVED:
                raise AppError(
                    "offline_smoke_failed",
                    "Report approval did not freeze the report.",
                )
            steps.append(SmokeStep("report", f"Version {approved.version} validated and approved."))

            export = context.export_service.export_report(session.id, report.id)
            if not export.path.is_file():
                raise AppError(
                    "offline_smoke_failed",
                    "The DOCX export was not created.",
                )
            Document(str(export.path))
            steps.append(SmokeStep("export", "DOCX rendered and reopened."))
            steps.append(SmokeStep("network", "Loopback-only guard active for this process."))
            session_id = session.id
            report_id = report.id
            report_version = report.version
            export_path = export.path
            segment_count = len(transcript.segments)
        finally:
            context.close()

        reopened_step = _reopen_and_verify(
            paths,
            session_id=session_id,
            report_id=report_id,
            export_path=export_path,
        )
        steps.append(reopened_step)

    return SmokeResult(
        session_id=session_id,
        report_id=report_id,
        report_version=report_version,
        export_path=export_path,
        segment_count=segment_count,
        steps=tuple(steps),
    )


def _reopen_and_verify(
    paths: AppPaths,
    *,
    session_id: str,
    report_id: str,
    export_path: Path,
) -> SmokeStep:
    reopened = build_context(paths)
    try:
        stored_session = reopened.session_service.get_session(session_id)
        reports = reopened.report_service.list_reports(session_id)
        if stored_session is None or stored_session.status != "report_approved":
            raise AppError(
                "offline_smoke_failed",
                "The approved session did not survive reopening the database.",
            )
        if not any(item.id == report_id for item in reports):
            raise AppError(
                "offline_smoke_failed",
                "The report did not survive reopening the database.",
            )
        if not export_path.is_file():
            raise AppError(
                "offline_smoke_failed",
                "The exported DOCX disappeared after reopening.",
            )
        mode = stat.S_IMODE(export_path.stat().st_mode)
        if mode != 0o600:
            raise AppError(
                "offline_smoke_failed",
                f"The exported DOCX has permissions {mode:o}, expected 600.",
            )
        return SmokeStep("reopen", "Database, report, and export verified after reopen.")
    finally:
        reopened.close()


def run_live_smoke(
    audio_path: str | Path,
    root: str | Path | None = None,
    *,
    whisper_model: str | None = None,
    report_model: str | None = None,
    language: str | None = None,
    device: str | None = None,
    report_language: str | None = None,
    report_length: str | None = None,
    report_profile: str | None = None,
    custom_instructions: str | None = None,
    models_dir: str | Path | None = None,
    worker_command: list[str] | None = None,
    llm_client: LocalLLMClient | None = None,
    transcription_timeout_seconds: float = 4 * 60 * 60,
) -> SmokeResult:
    """Run the same local pipeline with real ASR and Ollama models.

    This is an explicit smoke test, not part of normal startup or pytest. It
    requires installed local model weights and a running loopback Ollama
    service. Audio is imported into a temporary isolated data tree.
    """

    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise AppError(
            "live_smoke_audio_missing",
            f"Smoke-test audio does not exist: {source}",
        )
    if root is None:
        with tempfile.TemporaryDirectory(prefix="audio-report-ai-live-smoke-") as temporary:
            return run_live_smoke(
                source,
                Path(temporary),
                whisper_model=whisper_model,
                report_model=report_model,
                language=language,
                device=device,
                report_language=report_language,
                report_length=report_length,
                report_profile=report_profile,
                custom_instructions=custom_instructions,
                models_dir=models_dir,
                worker_command=worker_command,
                llm_client=llm_client,
                transcription_timeout_seconds=transcription_timeout_seconds,
            )

    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    paths = isolated_paths(
        base,
        models_dir=models_dir if models_dir is not None else resolve_paths().models_dir,
    )
    ensure_directories(paths)
    config = AppConfig(
        whisper_model=whisper_model or AppConfig().whisper_model,
        report_model=report_model or AppConfig().report_model,
        report_language=report_language or AppConfig().report_language,
        report_length=report_length or AppConfig().report_length,
        report_profile=report_profile or AppConfig().report_profile,
        language=language or AppConfig().language,
        whisper_device=device or AppConfig().whisper_device,
    )
    save_config(config, paths)

    steps: list[SmokeStep] = []
    with loopback_only_network():
        context = build_context(paths, config)
        try:
            session = context.session_service.create_session("Live offline smoke test")
            context.audio_import_service.import_audio(session.id, source)
            steps.append(SmokeStep("import", "Input audio validated and copied."))
            normalised = context.audio_normalizer.normalise(session.id)
            steps.append(SmokeStep("normalise", "Mono 16 kHz PCM WAV prepared."))

            model_dir = context.model_locator.locate()
            job_dir = paths.jobs_dir / f"live-smoke-{uuid.uuid4().hex}"
            job_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            status_path = job_dir / "status.json"
            result_path = job_dir / "result.json"
            command = [
                *(
                    worker_command
                    or [
                        sys.executable,
                        "-m",
                        "audio_report_ai.workers.transcription_worker",
                    ]
                ),
                "--audio",
                str(normalised),
                "--model-dir",
                str(model_dir),
                "--model-name",
                config.whisper_model,
                "--language",
                config.language,
                "--device",
                config.whisper_device,
                "--session-id",
                session.id,
                "--result",
                str(result_path),
                "--status",
                str(status_path),
            ]
            environment = os.environ.copy()
            environment.update(
                {
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_DATASETS_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "PYTHONNOUSERSITE": "1",
                }
            )
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=transcription_timeout_seconds,
                    check=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                raise AppError(
                    "live_smoke_transcription_timeout",
                    "Local transcription timed out during the live smoke test.",
                ) from exc
            except OSError as exc:
                raise AppError(
                    "live_smoke_transcription_failed",
                    "Could not start the local transcription worker.",
                ) from exc
            status = _read_json_object(status_path)
            if completed.returncode != 0 or status.get("status") == "failed":
                code = status.get("error_code")
                detail = f" ({code})" if isinstance(code, str) and code else ""
                raise AppError(
                    "live_smoke_transcription_failed",
                    f"Local transcription failed during the live smoke test{detail}.",
                )
            try:
                transcript = Transcript.model_validate_json(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AppError(
                    "live_smoke_transcription_invalid",
                    "The local transcription worker returned invalid output.",
                ) from exc
            if not transcript.segments:
                raise AppError(
                    "live_smoke_transcription_empty",
                    (
                        "Local transcription returned no speech segments. "
                        "Use a short, audible speech recording for the live smoke test."
                    ),
                )
            context.transcription_service.persist_result(session.id, transcript)
            steps.append(
                SmokeStep(
                    "transcribe",
                    f"{len(transcript.segments)} segments stored from local ASR.",
                )
            )

            source_data = context.report_service.build_source(session.id)
            if custom_instructions and custom_instructions.strip():
                source_data = source_data.model_copy(
                    update={"custom_instructions": custom_instructions.strip()}
                )
            client = llm_client or LocalLLMClient(config.report_model)
            if llm_client is None:
                client.verify_model()
            generated = generate_report(
                client,
                source_data,
                language=config.report_language,
                length=config.report_length,
                profile=config.report_profile,
            )
            saved = context.report_service.save_generated_report(
                session.id,
                generated.model_dump(mode="json"),
                profile=config.report_profile,
            )
            approved = context.report_service.approve_report(saved.id)
            steps.append(
                SmokeStep(
                    "report",
                    f"Version {approved.version} generated by local Ollama and approved.",
                )
            )
            export = context.export_service.export_report(session.id, saved.id)
            Document(str(export.path))
            steps.append(SmokeStep("export", "DOCX rendered and reopened."))
            steps.append(
                SmokeStep(
                    "network",
                    "Loopback-only guard active; model worker used offline env vars.",
                )
            )
            session_id = session.id
            report_id = saved.id
            report_version = saved.version
            export_path = export.path
            segment_count = len(transcript.segments)
        finally:
            context.close()

        steps.append(
            _reopen_and_verify(
                paths,
                session_id=session_id,
                report_id=report_id,
                export_path=export_path,
            )
        )

    return SmokeResult(
        session_id=session_id,
        report_id=report_id,
        report_version=report_version,
        export_path=export_path,
        segment_count=segment_count,
        steps=tuple(steps),
        mode="live",
    )


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def format_smoke_result(result: SmokeResult) -> str:
    label = (
        "Live local-model smoke test passed."
        if result.mode == "live"
        else ("Offline smoke test passed.")
    )
    lines = [label]
    lines.extend(f"  [{step.name}] {step.detail}" for step in result.steps)
    lines.append(
        f"  Report v{result.report_version} with {result.segment_count} "
        f"{'transcribed' if result.mode == 'live' else 'synthetic'} segments; "
        "export verified."
    )
    if result.mode != "live":
        lines.append(
            "This harness uses synthetic audio and a deterministic report payload; "
            "it does not prove ASR or LLM quality. Use --live-smoke-test AUDIO for "
            "real local models."
        )
    else:
        lines.append(
            "Verify that an external network monitor observed only loopback "
            "127.0.0.1:11434 traffic for the LLM."
        )
    return "\n".join(lines)
