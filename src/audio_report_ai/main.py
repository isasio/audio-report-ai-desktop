from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .app import AppContext, build_context
from .config import load_config
from .database.migrations import SCHEMA_VERSION
from .errors import AppError, SelfCheckError
from .paths import resolve_paths
from .services.report_models import REPORT_LANGUAGES, REPORT_LENGTHS
from .services.report_profiles import REPORT_PROFILES


def run_self_check(context: AppContext) -> None:
    directories = (
        context.paths.data_dir,
        context.paths.audio_dir,
        context.paths.transcripts_dir,
        context.paths.reports_dir,
        context.paths.exports_dir,
        context.paths.models_dir,
        context.paths.config_dir,
        context.paths.cache_dir,
        context.paths.jobs_dir,
        context.paths.state_dir,
        context.paths.log_dir,
    )
    for directory in directories:
        if not directory.is_dir():
            raise SelfCheckError(
                "self_check_failed",
                f"Required directory is missing: {directory}",
            )
        if not os.access(directory, os.W_OK):
            raise SelfCheckError(
                "self_check_failed",
                f"Required directory is not writable: {directory}",
            )

    if not context.paths.config_path.is_file():
        raise SelfCheckError(
            "self_check_failed",
            f"Config file is missing: {context.paths.config_path}",
        )

    try:
        row = context.connection.execute("PRAGMA user_version").fetchone()
        version = int(row[0]) if row is not None else -1
        context.connection.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise SelfCheckError("self_check_failed", "Database check failed.") from exc

    if version != SCHEMA_VERSION:
        raise SelfCheckError(
            "self_check_failed",
            f"Database schema version is {version}, expected {SCHEMA_VERSION}.",
        )

    context.logger.info("self_check_passed")


def _run_desktop_command(args: argparse.Namespace) -> int:
    from .services.desktop_integration import (
        install_desktop_entry,
        uninstall_desktop_entry,
    )
    from .services.runtime_check import app_root_from_module

    if args.uninstall_desktop:
        removed = uninstall_desktop_entry()
        if removed:
            for path in removed:
                print(f"Removed: {path}")
        else:
            print("No desktop launcher was installed for this user.")
        return 0

    project_root = Path(args.project_root).expanduser() if args.project_root else None
    if project_root is None:
        project_root = app_root_from_module()
    if project_root is None:
        raise AppError(
            "desktop_project_invalid",
            ("Could not locate the project run.sh. Pass --project-root /path/to/audio-report-ai."),
        )
    result = install_desktop_entry(project_root)
    print(f"Desktop launcher installed: {result.desktop_path}")
    print(f"Icon installed: {result.icon_path}")
    print("It may appear in the application menu after the desktop refreshes.")
    return 0


def _run_evaluation(args: argparse.Namespace) -> int:
    from .services.evaluation import (
        evaluate_manifest,
        format_evaluation_summary,
        write_summary_json,
    )

    summary = evaluate_manifest(args.evaluate_corpus)
    print(format_evaluation_summary(summary))
    if args.evaluation_output:
        output = write_summary_json(args.evaluation_output, summary)
        print(f"\nJSON results written to: {output}")
    return 0


def _run_smoke(args: argparse.Namespace) -> int:
    from .services.offline_smoke import (
        format_smoke_result,
        run_live_smoke,
        run_offline_smoke,
    )

    if args.live_smoke_test:
        runtime_config = load_config(resolve_paths())
        result = run_live_smoke(
            args.live_smoke_test,
            args.smoke_dir,
            whisper_model=args.model or runtime_config.whisper_model,
            report_model=args.report_model or runtime_config.report_model,
            language=runtime_config.language,
            device=runtime_config.whisper_device,
            report_language=args.report_language or runtime_config.report_language,
            report_length=args.report_length or runtime_config.report_length,
            report_profile=args.report_profile or runtime_config.report_profile,
            custom_instructions=args.report_instructions,
        )
    else:
        result = run_offline_smoke(args.smoke_dir)
    print(format_smoke_result(result))
    return 0


def _run_runtime_check(context: AppContext, strict: bool) -> int:
    from .services.runtime_check import check_runtime, format_runtime_check

    result = check_runtime(context.paths, context.config)
    print(format_runtime_check(result))
    if strict:
        return 0 if result.strict_ready else 1
    return 0 if result.core_ready else 1


def _run_setup_report_model(context: AppContext, model_name: str) -> int:
    from .services.report_model_setup import ReportModelSetupService
    from .services.report_models import format_model_presets, preset_for

    preset = preset_for(model_name)
    if preset is not None:
        print(f"Report model: {preset.name}")
        print(f"  {preset.label}")
        print(f"  Parameters: {preset.parameters}")
        print(f"  Download size: {preset.download_size}")
        print(f"  Memory guidance: {preset.memory}")
        print(f"  License: {preset.license}")
    else:
        print(f"Report model: {model_name}")
        print(format_model_presets())
    ReportModelSetupService(context.logger).setup(model_name)
    print(f"Report model ready: {model_name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audio-report-ai",
        description="Local audio transcription and report application.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Validate configuration, paths, and database, then exit.",
    )
    parser.add_argument(
        "--runtime-check",
        action="store_true",
        help="Report local audio/model/Ollama readiness, then exit.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="With --runtime-check, fail unless speech and report models are ready.",
    )
    parser.add_argument(
        "--setup-guide",
        action="store_true",
        help="Print the local setup and offline-operation guide.",
    )
    parser.add_argument(
        "--setup-models",
        action="store_true",
        help="Download a faster-whisper model into the local app model directory.",
    )
    parser.add_argument(
        "--check-models",
        action="store_true",
        help="Verify that the configured local faster-whisper model is available.",
    )
    parser.add_argument(
        "--model",
        help="Model name for --setup-models, --check-models, or --live-smoke-test.",
    )
    parser.add_argument(
        "--setup-report-model",
        action="store_true",
        help="Download the configured Ollama report model once.",
    )
    parser.add_argument(
        "--list-report-models",
        action="store_true",
        help="List supported local report models, sizes, and licenses.",
    )
    parser.add_argument(
        "--setup-diarization",
        action="store_true",
        help="Download the optional local speaker-diarization model.",
    )
    parser.add_argument(
        "--check-diarization",
        action="store_true",
        help="Verify that the configured local diarization model is available.",
    )
    parser.add_argument(
        "--diarization-model",
        help="Model name for --setup-diarization or --check-diarization.",
    )
    parser.add_argument(
        "--check-ollama",
        action="store_true",
        help="Verify that the configured local Ollama report model is available.",
    )
    parser.add_argument(
        "--report-model",
        help="Ollama model name for --check-ollama, --setup-report-model, or --live-smoke-test.",
    )
    parser.add_argument(
        "--report-language",
        choices=REPORT_LANGUAGES,
        help="Report language for --live-smoke-test (auto follows the transcript).",
    )
    parser.add_argument(
        "--report-length",
        choices=REPORT_LENGTHS,
        help="Report length for --live-smoke-test.",
    )
    parser.add_argument(
        "--report-profile",
        choices=REPORT_PROFILES,
        help="Report profile for --live-smoke-test.",
    )
    parser.add_argument(
        "--report-instructions",
        help="Custom writing instructions for --live-smoke-test.",
    )
    parser.add_argument(
        "--check-audio",
        action="store_true",
        help="Report FFmpeg, FFprobe, and microphone availability, then exit.",
    )
    parser.add_argument(
        "--offline-smoke-test",
        action="store_true",
        help="Run the deterministic synthetic offline smoke test, then exit.",
    )
    parser.add_argument(
        "--live-smoke-test",
        metavar="AUDIO",
        help=(
            "Run import, real local ASR, Ollama report generation, approval, and "
            "DOCX export with a local audio file, then exit."
        ),
    )
    parser.add_argument(
        "--smoke-dir",
        help="Keep smoke-test data in this directory instead of a temporary directory.",
    )
    parser.add_argument(
        "--evaluate-corpus",
        metavar="MANIFEST",
        help="Evaluate transcript/evidence metrics against a local corpus manifest.",
    )
    parser.add_argument(
        "--evaluation-output",
        metavar="PATH",
        help="Write --evaluate-corpus results as JSON to this path.",
    )
    parser.add_argument(
        "--install-desktop",
        action="store_true",
        help="Install the local desktop launcher and icon for this user.",
    )
    parser.add_argument(
        "--uninstall-desktop",
        action="store_true",
        help="Remove the local desktop launcher and icon.",
    )
    parser.add_argument(
        "--project-root",
        help="Source checkout or extracted release used by --install-desktop.",
    )
    args = parser.parse_args(argv)

    os.umask(0o077)
    context: AppContext | None = None
    try:
        if args.install_desktop or args.uninstall_desktop:
            return _run_desktop_command(args)
        if args.evaluate_corpus:
            return _run_evaluation(args)
        if args.offline_smoke_test or args.live_smoke_test:
            return _run_smoke(args)
        if args.list_report_models:
            from .services.report_models import format_model_presets

            print(format_model_presets())
            return 0

        context = build_context()
        if args.self_check:
            run_self_check(context)
            print("Self-check passed.")
            return 0

        if args.runtime_check:
            return _run_runtime_check(context, args.strict)

        if args.setup_guide:
            from .services.runtime_check import format_setup_guide

            print(format_setup_guide(context.config, context.paths, __version__))
            return 0

        if args.setup_models:
            from .services.model_setup import ModelSetupService

            model_name = args.model or context.config.whisper_model
            ModelSetupService(context.paths, context.logger).setup(model_name)
            print(f"Model ready: {model_name}")
            return 0

        if args.check_models:
            from .services.model_locator import ModelLocator

            model_name = args.model or context.config.whisper_model
            ModelLocator(context.paths, model_name, context.logger).locate()
            print(f"Model available: {model_name}")
            return 0

        if args.setup_report_model:
            model_name = args.report_model or context.config.report_model
            return _run_setup_report_model(context, model_name)

        if args.setup_diarization:
            from .services.diarization_model import (
                DIARIZATION_MODEL_LICENSE,
                DIARIZATION_MODEL_TERMS_URL,
                DiarizationModelSetupService,
            )

            model_name = args.diarization_model or context.config.diarization_model
            print(f"Diarization model: {model_name}")
            print(f"License: {DIARIZATION_MODEL_LICENSE}")
            print(f"Terms/access: {DIARIZATION_MODEL_TERMS_URL}")
            print("Model files: approximately 35 MB; pyannote.audio/PyTorch add more.")
            print("Set HF_TOKEN after accepting the model terms. Runtime stays offline.")
            DiarizationModelSetupService(context.paths, context.logger).setup(model_name)
            print(f"Diarization model ready: {model_name}")
            return 0

        if args.check_diarization:
            from .services.diarization_model import DiarizationModelLocator

            model_name = args.diarization_model or context.config.diarization_model
            DiarizationModelLocator(context.paths, model_name, context.logger).locate()
            print(f"Diarization model available: {model_name}")
            return 0

        if args.check_ollama:
            from .services.local_llm_client import LocalLLMClient

            model_name = args.report_model or context.config.report_model
            client = (
                context.llm_client
                if model_name == context.config.report_model
                else LocalLLMClient(model_name, logger=context.logger)
            )
            client.verify_model()
            print(f"Ollama model available: {model_name}")
            return 0

        if args.check_audio:
            from .services.audio_health import check_audio_health, format_audio_health

            health = check_audio_health()
            print(format_audio_health(health))
            return 0 if health.import_ready else 1

        from PySide6.QtWidgets import QApplication

        from .ui.main_window import MainWindow
        from .ui.playback import QtPlaybackController

        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setApplicationName("Audio Report AI")
        playback = QtPlaybackController()
        window = MainWindow(
            context.session_service,
            context.audio_import_service,
            context.transcription_service,
            context.job_coordinator,
            playback,
            speaker_service=context.speaker_service,
            microphone_recorder=context.microphone_recorder,
            report_service=context.report_service,
            report_coordinator=context.report_coordinator,
            export_service=context.export_service,
            session_deletion_service=context.session_deletion_service,
            config=context.config,
            paths=context.paths,
        )
        window.show()
        return app.exec()
    except AppError as exc:
        print(f"Error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1
    finally:
        if context is not None:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
