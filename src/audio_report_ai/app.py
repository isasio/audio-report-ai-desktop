from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from .config import AppConfig, load_config
from .database.connection import connect
from .database.migrations import migrate
from .database.repositories import (
    JobRepository,
    ReportRepository,
    SegmentRepository,
    SessionRepository,
    SpeakerRoleRepository,
)
from .logging_setup import configure_logging
from .paths import AppPaths, ensure_directories, resolve_paths
from .services.audio_import import AudioImportService
from .services.audio_normalizer import AudioNormalizer
from .services.audio_recorder import MicrophoneRecorder
from .services.diarization import DiarizationService
from .services.diarization_model import DiarizationModelLocator
from .services.export_service import ExportService
from .services.job_coordinator import JobCoordinator
from .services.local_llm_client import LocalLLMClient
from .services.model_locator import ModelLocator
from .services.report_coordinator import ReportCoordinator
from .services.reports import ReportService
from .services.session_deletion import SessionDeletionService
from .services.sessions import SessionService
from .services.speakers import SpeakerService
from .services.transcription import TranscriptionService


@dataclass
class AppContext:
    paths: AppPaths
    config: AppConfig
    connection: sqlite3.Connection
    session_service: SessionService
    audio_import_service: AudioImportService
    audio_normalizer: AudioNormalizer
    microphone_recorder: MicrophoneRecorder
    transcription_service: TranscriptionService
    speaker_service: SpeakerService
    diarization_service: DiarizationService
    model_locator: ModelLocator
    diarization_locator: DiarizationModelLocator
    llm_client: LocalLLMClient
    report_service: ReportService
    export_service: ExportService
    session_deletion_service: SessionDeletionService
    report_coordinator: ReportCoordinator
    job_coordinator: JobCoordinator
    logger: logging.Logger

    def close(self) -> None:
        self.report_coordinator.shutdown()
        self.job_coordinator.shutdown()
        self.connection.close()


def build_context(
    paths: AppPaths | None = None,
    config: AppConfig | None = None,
) -> AppContext:
    """Build the composition root.

    ``paths`` and ``config`` are injectable so the offline smoke test and
    tests can run against an isolated temporary tree without touching the
    real XDG directories. Production callers use the defaults.
    """

    paths = paths or resolve_paths()
    ensure_directories(paths)
    config = config or load_config(paths)
    logger = configure_logging(paths, config.log_level)
    connection = connect(paths.database_path)
    try:
        migrate(connection)
    except Exception:
        connection.close()
        raise
    repository = SessionRepository(connection)
    segment_repository = SegmentRepository(connection)
    speaker_role_repository = SpeakerRoleRepository(connection)
    job_repository = JobRepository(connection)
    report_repository = ReportRepository(connection)
    service = SessionService(repository, logger)
    audio_import_service = AudioImportService(paths, service, logger=logger)
    audio_normalizer = AudioNormalizer(paths, service, logger=logger)
    microphone_recorder = MicrophoneRecorder(paths, service, logger=logger)
    transcription_service = TranscriptionService(
        paths,
        service,
        segment_repository,
        logger=logger,
    )
    speaker_service = SpeakerService(
        service,
        segment_repository,
        speaker_role_repository,
        logger=logger,
    )
    diarization_service = DiarizationService(
        paths,
        service,
        transcription_service,
        logger=logger,
    )
    model_locator = ModelLocator(paths, config.whisper_model, logger=logger)
    diarization_locator = DiarizationModelLocator(
        paths,
        config.diarization_model,
        logger=logger,
    )
    llm_client = LocalLLMClient(
        config.report_model,
        logger=logger,
    )
    report_service = ReportService(
        paths,
        service,
        segment_repository,
        speaker_role_repository,
        report_repository,
        logger=logger,
    )
    export_service = ExportService(
        paths,
        service,
        segment_repository,
        report_repository,
        logger=logger,
    )
    session_deletion_service = SessionDeletionService(
        paths,
        service,
        job_repository,
        logger=logger,
    )
    report_coordinator = ReportCoordinator(
        paths,
        config,
        service,
        report_service,
        job_repository,
        logger=logger,
    )
    job_coordinator = JobCoordinator(
        paths,
        config,
        service,
        audio_normalizer,
        transcription_service,
        job_repository,
        model_locator,
        diarization_service,
        diarization_locator,
        logger=logger,
    )
    job_coordinator.recover_stale_jobs()
    report_coordinator.recover_stale_sessions()
    logger.info("app_context_ready")
    return AppContext(
        paths=paths,
        config=config,
        connection=connection,
        session_service=service,
        audio_import_service=audio_import_service,
        audio_normalizer=audio_normalizer,
        microphone_recorder=microphone_recorder,
        transcription_service=transcription_service,
        speaker_service=speaker_service,
        diarization_service=diarization_service,
        model_locator=model_locator,
        diarization_locator=diarization_locator,
        llm_client=llm_client,
        report_service=report_service,
        export_service=export_service,
        session_deletion_service=session_deletion_service,
        report_coordinator=report_coordinator,
        job_coordinator=job_coordinator,
        logger=logger,
    )
