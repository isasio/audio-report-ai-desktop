from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..config import AppConfig
from ..errors import AppError
from ..paths import AppPaths
from .audio_health import AudioHealth, check_audio_health
from .diarization_model import (
    DIARIZATION_MODEL_LICENSE,
    DIARIZATION_MODEL_TERMS_URL,
    DiarizationModelLocator,
)
from .local_llm_client import LocalLLMClient
from .model_locator import ModelLocator
from .report_models import format_model_presets

CheckStatus = Literal["ok", "warning", "error"]
CORE_PATHS = "data_paths"
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
SPEECH_MODEL = "speech_model"
OLLAMA_SERVICE = "ollama_service"
REPORT_MODEL = "report_model"
DIARIZATION_MODEL = "diarization_model"
MICROPHONE = "microphone"
_CORE_KEYS = frozenset({CORE_PATHS, FFMPEG, FFPROBE})


@dataclass(frozen=True)
class RuntimeComponent:
    key: str
    label: str
    status: CheckStatus
    detail: str
    setup: str | None = None


@dataclass(frozen=True)
class RuntimeCheckResult:
    components: tuple[RuntimeComponent, ...]

    def component(self, key: str) -> RuntimeComponent | None:
        for component in self.components:
            if component.key == key:
                return component
        return None

    def _ready(self, *keys: str) -> bool:
        return all(
            (component := self.component(key)) is not None and component.status == "ok"
            for key in keys
        )

    @property
    def core_ready(self) -> bool:
        return self._ready(*_CORE_KEYS)

    @property
    def processing_ready(self) -> bool:
        return self.core_ready and self._ready(SPEECH_MODEL)

    @property
    def report_ready(self) -> bool:
        return self.processing_ready and self._ready(OLLAMA_SERVICE, REPORT_MODEL)

    @property
    def strict_ready(self) -> bool:
        return self.report_ready

    @property
    def has_warnings(self) -> bool:
        return any(component.status == "warning" for component in self.components)


def _component(
    key: str,
    label: str,
    status: CheckStatus,
    detail: str,
    setup: str | None = None,
) -> RuntimeComponent:
    return RuntimeComponent(
        key=key,
        label=label,
        status=status,
        detail=detail,
        setup=setup,
    )


def check_runtime(
    paths: AppPaths,
    config: AppConfig,
    *,
    audio_health: AudioHealth | None = None,
    model_locator: ModelLocator | None = None,
    llm_client: LocalLLMClient | None = None,
    diarization_locator: DiarizationModelLocator | None = None,
) -> RuntimeCheckResult:
    """Check local readiness without contacting anything except loopback Ollama."""

    health = audio_health or check_audio_health()
    locator = model_locator or ModelLocator(paths, config.whisper_model)
    client = llm_client or LocalLLMClient(config.report_model, timeout_seconds=2.0)
    diarization = diarization_locator or DiarizationModelLocator(
        paths,
        config.diarization_model,
    )
    components: list[RuntimeComponent] = []

    directories = (
        paths.data_dir,
        paths.audio_dir,
        paths.transcripts_dir,
        paths.reports_dir,
        paths.exports_dir,
        paths.models_dir,
        paths.config_dir,
        paths.cache_dir,
        paths.state_dir,
    )
    unusable = [
        str(directory)
        for directory in directories
        if not directory.is_dir() or not os.access(directory, os.W_OK)
    ]
    if unusable:
        components.append(
            _component(
                CORE_PATHS,
                "Data directories",
                "error",
                f"Not writable: {', '.join(unusable)}",
                "Run ./install.sh or check permissions under the XDG directories.",
            )
        )
    else:
        components.append(
            _component(CORE_PATHS, "Data directories", "ok", "Writable with 0700 permissions.")
        )

    if health.ffmpeg_path:
        components.append(_component(FFMPEG, "FFmpeg", "ok", str(health.ffmpeg_path)))
    else:
        components.append(
            _component(
                FFMPEG,
                "FFmpeg",
                "error",
                "FFmpeg is not installed or not on PATH.",
                "Run ./install.sh and install the reported system packages.",
            )
        )
    if health.ffprobe_path:
        components.append(_component(FFPROBE, "FFprobe", "ok", str(health.ffprobe_path)))
    else:
        components.append(
            _component(
                FFPROBE,
                "FFprobe",
                "error",
                "FFprobe is not installed or not on PATH.",
                "Run ./install.sh and install the reported system packages.",
            )
        )

    try:
        model_dir = locator.locate()
    except AppError as exc:
        components.append(
            _component(
                SPEECH_MODEL,
                f"Speech model ({config.whisper_model})",
                "warning",
                exc.message,
                (f"uv run python -m audio_report_ai --setup-models --model {config.whisper_model}"),
            )
        )
    else:
        components.append(
            _component(
                SPEECH_MODEL,
                f"Speech model ({config.whisper_model})",
                "ok",
                f"Local weights: {model_dir}",
            )
        )

    try:
        client.list_models()
    except AppError as exc:
        components.append(
            _component(
                OLLAMA_SERVICE,
                "Ollama service",
                "warning",
                exc.message,
                "Start Ollama; it must listen only on 127.0.0.1:11434.",
            )
        )
        components.append(
            _component(
                REPORT_MODEL,
                f"Report model ({config.report_model})",
                "warning",
                "Not checked because the local Ollama service is unavailable.",
                (
                    "uv run python -m audio_report_ai --setup-report-model "
                    f"--report-model {config.report_model}"
                ),
            )
        )
    else:
        components.append(
            _component(OLLAMA_SERVICE, "Ollama service", "ok", "Loopback service reachable.")
        )
        try:
            client.verify_model()
        except AppError as exc:
            components.append(
                _component(
                    REPORT_MODEL,
                    f"Report model ({config.report_model})",
                    "warning",
                    exc.message,
                    (
                        "uv run python -m audio_report_ai --setup-report-model "
                        f"--report-model {config.report_model}"
                    ),
                )
            )
        else:
            components.append(
                _component(
                    REPORT_MODEL,
                    f"Report model ({config.report_model})",
                    "ok",
                    "Installed in local Ollama storage.",
                )
            )

    try:
        diarization_dir = diarization.locate()
    except AppError as exc:
        components.append(
            _component(
                DIARIZATION_MODEL,
                "Speaker diarization (optional)",
                "warning",
                exc.message,
                (
                    "uv sync --frozen --extra diarization, accept the model terms, "
                    "then run --setup-diarization with HF_TOKEN."
                ),
            )
        )
    else:
        components.append(
            _component(
                DIARIZATION_MODEL,
                "Speaker diarization (optional)",
                "ok",
                f"Local weights: {diarization_dir}",
            )
        )

    if health.recording_ready and health.input_device is not None:
        components.append(
            _component(
                MICROPHONE,
                "Microphone (optional)",
                "ok",
                health.input_device.display,
            )
        )
    else:
        components.append(
            _component(
                MICROPHONE,
                "Microphone (optional)",
                "warning",
                "No usable input device was found. Audio import still works.",
                "Connect a microphone and rerun --check-audio.",
            )
        )

    return RuntimeCheckResult(components=tuple(components))


def format_runtime_check(result: RuntimeCheckResult) -> str:
    labels = {"ok": "OK", "warning": "WARN", "error": "FAIL"}
    lines = ["Runtime readiness check:"]
    for component in result.components:
        lines.append(f"[{labels[component.status]}] {component.label}: {component.detail}")
        if component.status != "ok" and component.setup:
            lines.append(f"       Fix: {component.setup}")

    lines.append("")
    lines.append(f"Core import/manage: {'ready' if result.core_ready else 'not ready'}")
    lines.append(f"Local transcription: {'ready' if result.processing_ready else 'not ready'}")
    lines.append(f"Local report generation: {'ready' if result.report_ready else 'not ready'}")
    if result.strict_ready:
        lines.append("All required local components are ready.")
    elif result.core_ready:
        lines.append("The app can start, but one or more local models still need setup.")
    else:
        lines.append("Required system components are missing.")
    return "\n".join(lines)


def format_setup_guide(config: AppConfig, paths: AppPaths, version: str) -> str:
    lines = [
        f"Audio Report AI {version} — local setup guide",
        "",
        "The app never downloads models or contacts remote services at runtime.",
        "Run the commands below only when you choose to install local components.",
        "",
        "Local data:",
        f"  Data and models: {paths.data_dir}",
        f"  Configuration:   {paths.config_dir}",
        f"  Logs:            {paths.log_dir}",
        "",
        "1. System and audio check:",
        "   ./install.sh",
        "   uv run python -m audio_report_ai --check-audio",
        "",
        "2. Local speech model:",
        f"   Configured model: {config.whisper_model}",
        "   Small CPU model (~0.5 GB):",
        "     uv run python -m audio_report_ai --setup-models --model small",
        "   Large GPU model (~3.1 GB):",
        "     uv run python -m audio_report_ai --setup-models --model large-v3",
        "   Verify:",
        f"     uv run python -m audio_report_ai --check-models --model {config.whisper_model}",
        "   Model weights are not bundled; the upstream faster-whisper model license applies.",
        "",
        "3. Local report model through Ollama:",
        "   Ollama must listen only on http://127.0.0.1:11434 with cloud disabled.",
        f"   Configured model: {config.report_model}",
        "     uv run python -m audio_report_ai --setup-report-model "
        f"--report-model {config.report_model}",
        "     uv run python -m audio_report_ai --check-ollama "
        f"--report-model {config.report_model}",
        "",
        format_model_presets(),
        "",
        "Report profiles: general conversation and veterinary clinical.",
        "Report types are selected per report or as a default in Settings.",
        "Writing instructions can be saved as reusable modes in the report dialog.",
        "",
        "4. Optional speaker diarization:",
        "   uv sync --frozen --extra diarization",
        f"   License: {DIARIZATION_MODEL_LICENSE}",
        f"   Terms: {DIARIZATION_MODEL_TERMS_URL}",
        "   HF_TOKEN=<token> uv run python -m audio_report_ai --setup-diarization",
        "   Runtime loads the model only from the app-managed local directory.",
        "",
        "5. Verify the full local stack:",
        "   uv run python -m audio_report_ai --runtime-check",
        "   uv run python -m audio_report_ai --runtime-check --strict",
        "   uv run python -m audio_report_ai --offline-smoke-test",
        "",
        "6. Desktop launcher:",
        "   uv run python -m audio_report_ai --install-desktop",
        "   Remove it with: uv run python -m audio_report_ai --uninstall-desktop",
        "",
        "Privacy and reliability:",
        "  - Recordings, transcripts, reports, and exports stay under the data directory.",
        "  - Reports are drafts until a human approves them.",
        "  - Speaker labels and generated reports can be wrong; review every claim.",
        "  - The only expected HTTP endpoint is loopback Ollama at 127.0.0.1:11434.",
    ]
    return "\n".join(lines)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def app_root_from_module() -> Path | None:
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "run.sh").is_file():
        return candidate
    return None
