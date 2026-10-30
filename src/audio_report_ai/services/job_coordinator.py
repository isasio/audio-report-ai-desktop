from __future__ import annotations

import json
import logging
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ..config import AppConfig
from ..database.repositories import JobRepository
from ..errors import AppError, JobError
from ..paths import AppPaths
from ..schemas.diarization import DiarizationResult
from ..schemas.transcript import Transcript
from .audio_normalizer import (
    NORMALISE_TIMEOUT_SECONDS,
    AudioNormalizer,
    NormalisationPlan,
)
from .diarization import DiarizationService
from .diarization_model import DiarizationModelLocator
from .model_locator import ModelLocator
from .sessions import SessionService
from .transcription import TranscriptionService

_JOB_DIR_NAME = "transcription"
_DIARIZATION_DIR_NAME = "diarization"
DIARIZATION_TIMEOUT_SECONDS = 4 * 60 * 60


class JobCoordinator(QObject):
    started = Signal(str)
    progress = Signal(str, float, str)
    completed = Signal(str)
    failed = Signal(str, str, str)
    cancelled = Signal(str)

    def __init__(
        self,
        paths: AppPaths,
        config: AppConfig,
        sessions: SessionService,
        normalizer: AudioNormalizer,
        transcription_service: TranscriptionService,
        jobs: JobRepository,
        model_locator: ModelLocator,
        diarization_service: DiarizationService | None = None,
        diarization_locator: DiarizationModelLocator | None = None,
        logger: logging.Logger | None = None,
        worker_command: list[str] | None = None,
        diarization_worker_command: list[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths = paths
        self._config = config
        self._sessions = sessions
        self._normalizer = normalizer
        self._transcription_service = transcription_service
        self._jobs = jobs
        self._model_locator = model_locator
        self._diarization_service = diarization_service
        self._diarization_locator = diarization_locator
        self._logger = logger or logging.getLogger(__name__)
        self._worker_command = worker_command or [
            sys.executable,
            "-m",
            "audio_report_ai.workers.transcription_worker",
        ]
        self._diarization_worker_command = diarization_worker_command or [
            sys.executable,
            "-m",
            "audio_report_ai.workers.diarization_worker",
        ]
        self._job_id: str | None = None
        self._session_id: str | None = None
        self._previous_status: str | None = None
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._drain_process_output)
        self._process.readyReadStandardError.connect(self._drain_process_output)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        self._finished_slot: Callable[[int, QProcess.ExitStatus], None] | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll_status)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout_fired)
        self._timeout_action: tuple[str, str, str] | None = None
        self._plan: NormalisationPlan | None = None
        self._stage: str | None = None
        self._status_path: Path | None = None
        self._result_path: Path | None = None
        self._model_dir: Path | None = None
        self._model_name: str | None = None
        self._diarization_model_dir: Path | None = None
        self._diarization_model_name: str | None = None
        self._normalised_ready = False
        self._cancelling = False
        self._handled = False
        self._timeout_error: tuple[str, str, str] | None = None
        self._last_progress = -1.0

    @property
    def is_running(self) -> bool:
        return self._job_id is not None

    @property
    def current_job_id(self) -> str | None:
        return self._job_id

    @property
    def current_session_id(self) -> str | None:
        return self._session_id

    @property
    def supports_diarization(self) -> bool:
        return self._diarization_service is not None and self._diarization_locator is not None

    def recover_stale_jobs(self) -> int:
        count = self._jobs.mark_stale_running_as_failed()
        for session in self._sessions.list_sessions():
            if session.status not in {"normalising", "transcribing", "diarizing"}:
                continue
            try:
                normalised = self._normalised_path(session.id).is_file()
            except JobError:
                normalised = False
            has_transcript = self._transcription_service.segment_count(session.id) > 0
            if session.status == "diarizing" and has_transcript:
                status = "transcribed"
            elif normalised:
                status = "normalised"
            elif session.original_audio_path:
                status = "audio_imported"
            else:
                status = "created"
            try:
                self._sessions.restore_status(session.id, status)
            except AppError:
                self._logger.warning(
                    "stale_session_status_update_failed session_id=%s",
                    session.id,
                )
        if count:
            self._logger.info("stale_jobs_recovered count=%d", count)
        return count

    def start_transcription(self, session_id: str) -> str:
        if self._job_id is not None:
            raise JobError(
                "job_busy",
                "A transcription is already running. Wait for it to finish or cancel it.",
            )

        session = self._sessions.get_session(session_id)
        if session is None:
            raise JobError("session_not_found", "The selected session no longer exists.")
        if not session.original_audio_path:
            raise JobError(
                "audio_not_imported",
                "Import audio into this session before transcribing it.",
            )

        job = self._jobs.create(session.id, "transcription")
        self._jobs.mark_running(job.id)
        self._job_id = job.id
        self._session_id = session.id
        self._previous_status = session.status
        self._normalised_ready = False
        self._cancelling = False
        self._handled = False
        self._timeout_error = None
        self._last_progress = -1.0
        self.started.emit(job.id)
        self._set_progress(0.0, "Checking local model")

        try:
            self._model_dir = self._model_locator.locate()
        except AppError as exc:
            self._fail(exc.code, exc.message, session_status=None)
            return job.id
        self._model_name = self._model_locator.model_name

        normalised_path = self._normalised_path(session.id)
        if session.status == "normalised" and normalised_path.is_file():
            self._normalised_ready = True
            self._start_worker()
        else:
            self._start_normalisation()
        return job.id

    def start_diarization(self, session_id: str) -> str:
        if self._job_id is not None:
            raise JobError(
                "job_busy",
                "Processing is already running. Wait for it to finish or cancel it.",
            )
        if not self.supports_diarization:
            raise JobError(
                "diarization_unavailable",
                "Speaker diarization is not available in this window.",
            )

        session = self._sessions.get_session(session_id)
        if session is None:
            raise JobError("session_not_found", "The selected session no longer exists.")
        if self._transcription_service.segment_count(session.id) == 0:
            raise JobError(
                "transcript_missing",
                "Transcribe the session before running speaker diarization.",
            )
        if not self._normalised_path(session.id).is_file():
            raise JobError(
                "normalised_audio_missing",
                "The prepared audio file is missing. Retranscribe the session first.",
            )

        job = self._jobs.create(session.id, "diarization")
        self._jobs.mark_running(job.id)
        self._job_id = job.id
        self._session_id = session.id
        self._previous_status = session.status
        self._stage = "diarization"
        self._normalised_ready = True
        self._cancelling = False
        self._handled = False
        self._timeout_error = None
        self._last_progress = -1.0
        self._status_path = None
        self._result_path = None
        self.started.emit(job.id)
        self._set_progress(0.0, "Checking local diarization model")

        try:
            self._diarization_model_dir = self._diarization_locator.locate()
            self._diarization_model_name = self._diarization_locator.model_name
        except AppError as exc:
            self._fail(exc.code, exc.message, session_status="diarization_failed")
            return job.id

        self._start_diarization_worker()
        return job.id

    def cancel(self) -> bool:
        if self._job_id is None:
            return False
        if self._cancelling:
            return True
        self._cancelling = True
        self._set_progress(self._last_progress if self._last_progress >= 0 else 0.0, "Cancelling")
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            self._schedule_kill(process)
        else:
            self._finish_cancelled()
        return True

    def shutdown(self) -> None:
        if self._job_id is None:
            return
        self.cancel()
        if self._process is not None:
            self._process.waitForFinished(3000)

    def _start_normalisation(self) -> None:
        self._stage = "normalisation"
        self._set_progress(0.0, "Preparing audio")
        try:
            self._plan = self._normalizer.plan(self._session_id or "")
            self._sessions.mark_normalising(self._session_id or "")
        except AppError as exc:
            self._fail(exc.code, exc.message, session_status="normalisation_failed")
            return

        self._start_process(
            self._normalizer.command(self._plan),
            self._on_normalisation_finished,
        )
        self._start_timeout(
            NORMALISE_TIMEOUT_SECONDS,
            "audio_normalisation_timeout",
            "Timed out while normalising the audio.",
            "normalisation_failed",
        )

    def _start_worker(self) -> None:
        self._stage = "transcription"
        job_id = self._job_id or ""
        session_id = self._session_id or ""
        job_dir = self._paths.jobs_dir / job_id / _JOB_DIR_NAME
        try:
            job_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._status_path = job_dir / "status.json"
            self._result_path = job_dir / "result.json"
            self._sessions.mark_transcribing(session_id)
        except (OSError, AppError):
            self._fail(
                "job_directory_failed",
                "Could not prepare the transcription job.",
                session_status="transcription_failed",
            )
            return

        if self._model_dir is None or self._model_name is None:
            self._fail(
                "model_missing",
                "The local transcription model is not installed.",
                session_status="transcription_failed",
            )
            return

        command = [
            *self._worker_command,
            "--audio",
            str(self._normalised_path(session_id)),
            "--model-dir",
            str(self._model_dir),
            "--model-name",
            self._model_name,
            "--language",
            self._config.language,
            "--device",
            self._config.whisper_device,
            "--session-id",
            session_id,
            "--result",
            str(self._result_path),
            "--status",
            str(self._status_path),
        ]
        self._set_progress(0.05, "Loading local model")
        self._start_process(command, self._on_worker_finished)
        self._poll_timer.start()

    def _start_diarization_worker(self) -> None:
        self._stage = "diarization"
        job_id = self._job_id or ""
        session_id = self._session_id or ""
        job_dir = self._paths.jobs_dir / job_id / _DIARIZATION_DIR_NAME
        try:
            job_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._status_path = job_dir / "status.json"
            self._result_path = job_dir / "result.json"
            self._sessions.mark_diarizing(session_id)
        except (OSError, AppError):
            self._fail(
                "job_directory_failed",
                "Could not prepare the diarization job.",
                session_status="diarization_failed",
            )
            return

        if self._diarization_model_dir is None or self._diarization_model_name is None:
            self._fail(
                "diarization_model_missing",
                "The local diarization model is not installed.",
                session_status="diarization_failed",
            )
            return

        command = [
            *self._diarization_worker_command,
            "--audio",
            str(self._normalised_path(session_id)),
            "--model-dir",
            str(self._diarization_model_dir),
            "--model-name",
            self._diarization_model_name,
            "--device",
            self._config.diarization_device,
            "--session-id",
            session_id,
            "--result",
            str(self._result_path),
            "--status",
            str(self._status_path),
        ]
        self._set_progress(0.05, "Loading local diarization model")
        self._start_process(command, self._on_diarization_finished)
        self._poll_timer.start()
        self._start_timeout(
            DIARIZATION_TIMEOUT_SECONDS,
            "diarization_timeout",
            "Timed out while identifying speakers.",
            "diarization_failed",
        )

    def _start_process(
        self,
        command: list[str],
        finished_slot: Callable[[int, QProcess.ExitStatus], None],
    ) -> None:
        self._handled = False
        self._finished_slot = finished_slot
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONNOUSERSITE", "1")
        environment.insert("HF_HUB_OFFLINE", "1")
        environment.insert("TRANSFORMERS_OFFLINE", "1")
        environment.insert("HF_DATASETS_OFFLINE", "1")
        environment.insert("HF_HUB_DISABLE_TELEMETRY", "1")
        self._process.setProcessEnvironment(environment)
        self._process.setWorkingDirectory(str(self._paths.data_dir))
        self._process.setProgram(command[0])
        self._process.setArguments(command[1:])
        self._process.start()

    def _on_process_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if (
            self._finished_slot is None
            or self._job_id is None
            or self._handled
            or self._process.state() != QProcess.ProcessState.NotRunning
        ):
            return
        self._finished_slot(exit_code, exit_status)

    def _drain_process_output(self) -> None:
        self._process.readAllStandardOutput()
        self._process.readAllStandardError()

    def _poll_status(self) -> None:
        if self._status_path is None or self._job_id is None:
            return
        try:
            payload = json.loads(self._status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        status = payload.get("status")
        progress_value = payload.get("progress")
        try:
            progress = float(progress_value)
        except (TypeError, ValueError):
            return
        if self._stage == "diarization":
            if status in {"starting", "loading_model", "diarizing"}:
                mapped = 0.05 + 0.95 * max(0.0, min(1.0, progress))
                message = {
                    "starting": "Starting diarization",
                    "loading_model": "Loading local diarization model",
                    "diarizing": "Identifying speakers",
                }.get(str(status), "Identifying speakers")
                self._set_progress(mapped, message)
            return

        if status in {"starting", "loading_model", "transcribing"}:
            mapped = 0.05 + 0.95 * max(0.0, min(1.0, progress))
            message = {
                "starting": "Starting transcription",
                "loading_model": "Loading local model",
                "transcribing": "Transcribing",
            }.get(str(status), "Transcribing")
            self._set_progress(mapped, message)

    def _on_normalisation_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if self._job_id is None or self._handled:
            return
        self._handled = True
        self._stop_timers()
        if self._timeout_error is not None:
            code, message, session_status = self._timeout_error
            self._abort_plan()
            self._fail(code, message, session_status=session_status)
            return
        if self._cancelling:
            self._abort_plan()
            self._finish_cancelled()
            return
        if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            self._abort_plan()
            self._fail(
                "audio_normalisation_failed",
                "FFmpeg could not normalise the selected audio.",
                session_status="normalisation_failed",
            )
            return
        if self._plan is None:
            self._fail(
                "audio_normalisation_failed",
                "The normalisation plan was lost.",
                session_status="normalisation_failed",
            )
            return
        try:
            self._normalizer.complete(self._plan)
        except AppError as exc:
            self._plan = None
            self._fail(exc.code, exc.message, session_status="normalisation_failed")
            return
        self._plan = None
        self._normalised_ready = True
        self._start_worker()

    def _on_worker_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if self._job_id is None or self._handled:
            return
        self._handled = True
        self._stop_timers()
        if self._cancelling:
            self._finish_cancelled()
            return
        if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            code, message = self._worker_failure()
            self._fail(code, message, session_status="transcription_failed")
            return
        if self._result_path is None:
            self._fail(
                "transcription_invalid_result",
                "The transcription worker did not return a result.",
                session_status="transcription_failed",
            )
            return
        try:
            transcript = Transcript.model_validate_json(
                self._result_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError):
            self._fail(
                "transcription_invalid_result",
                "The transcription worker returned invalid data.",
                session_status="transcription_failed",
            )
            return

        job_id = self._job_id or ""
        session_id = self._session_id or ""
        try:
            self._transcription_service.persist_result(session_id, transcript)
        except AppError as exc:
            self._fail(exc.code, exc.message, session_status="transcription_failed")
            return
        except Exception:
            self._fail(
                "transcript_persist_failed",
                "Could not save the transcript.",
                session_status="transcription_failed",
            )
            return

        self._jobs.mark_completed(job_id)
        self.progress.emit(job_id, 1.0, "Transcription complete")
        self._cleanup_job_dir()
        self._reset_state()
        self.completed.emit(job_id)

    def _on_diarization_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if self._job_id is None or self._handled:
            return
        self._handled = True
        self._stop_timers()
        if self._timeout_error is not None:
            code, message, session_status = self._timeout_error
            self._fail(code, message, session_status=session_status)
            return
        if self._cancelling:
            self._finish_cancelled()
            return
        if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            code, message = self._diarization_failure()
            self._fail(code, message, session_status="diarization_failed")
            return
        if self._result_path is None:
            self._fail(
                "diarization_invalid_result",
                "The diarization worker did not return a result.",
                session_status="diarization_failed",
            )
            return
        try:
            result = DiarizationResult.model_validate_json(
                self._result_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError):
            self._fail(
                "diarization_invalid_result",
                "The diarization worker returned invalid data.",
                session_status="diarization_failed",
            )
            return

        job_id = self._job_id or ""
        session_id = self._session_id or ""
        try:
            if self._diarization_service is None:
                raise AppError(
                    "diarization_unavailable",
                    "Speaker diarization is not available.",
                )
            self._diarization_service.apply_result(session_id, result)
        except AppError as exc:
            self._fail(exc.code, exc.message, session_status="diarization_failed")
            return
        except Exception:
            self._fail(
                "diarization_persist_failed",
                "Could not save the speaker labels.",
                session_status="diarization_failed",
            )
            return

        self._jobs.mark_completed(job_id)
        self.progress.emit(job_id, 1.0, "Diarization complete")
        self._cleanup_job_dir()
        self._reset_state()
        self.completed.emit(job_id)

    def _diarization_failure(self) -> tuple[str, str]:
        if self._status_path is not None:
            try:
                payload = json.loads(self._status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                payload = None
            if isinstance(payload, dict):
                code = payload.get("error_code")
                message = payload.get("error_message")
                if isinstance(code, str) and code:
                    return (
                        code,
                        message
                        if isinstance(message, str) and message
                        else "Local speaker diarization failed.",
                    )
        return (
            "diarization_worker_failed",
            "The local diarization worker stopped unexpectedly.",
        )

    def _worker_failure(self) -> tuple[str, str]:
        if self._status_path is not None:
            try:
                payload = json.loads(self._status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                payload = None
            if isinstance(payload, dict):
                code = payload.get("error_code")
                message = payload.get("error_message")
                if isinstance(code, str) and code:
                    return (
                        code,
                        message
                        if isinstance(message, str) and message
                        else "Local transcription failed.",
                    )
        return (
            "transcription_worker_failed",
            "The local transcription worker stopped unexpectedly.",
        )

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if self._job_id is None or self._handled:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._handled = True
            self._stop_timers()
            self._abort_plan()
            self._fail(
                "worker_start_failed",
                "Could not start the local processing worker.",
                session_status=self._failure_session_status(),
            )
        elif error == QProcess.ProcessError.Crashed and not self._cancelling:
            if self._timeout_error is not None:
                return
            self._handled = True
            self._stop_timers()
            self._abort_plan()
            self._fail(
                "worker_crashed",
                "The local processing worker crashed.",
                session_status=self._failure_session_status(),
            )

    def _failure_session_status(self) -> str:
        if self._stage == "normalisation":
            return "normalisation_failed"
        if self._stage == "diarization":
            return "diarization_failed"
        return "transcription_failed"

    def _on_timeout_fired(self) -> None:
        if self._job_id is None or self._timeout_action is None:
            return
        self._timeout_error = self._timeout_action
        process = self._process
        if process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            self._schedule_kill(process)

    def _schedule_kill(self, process: QProcess) -> None:
        job_id = self._job_id
        QTimer.singleShot(3000, lambda: self._kill_process(process, job_id))

    def _kill_process(self, process: QProcess, job_id: str | None) -> None:
        if self._job_id != job_id or self._process is not process:
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _start_timeout(
        self,
        seconds: float,
        code: str,
        message: str,
        session_status: str,
    ) -> None:
        self._timeout_action = (code, message, session_status)
        self._timeout_timer.start(int(seconds * 1000))

    def _finish_cancelled(self) -> None:
        job_id = self._job_id or ""
        session_id = self._session_id or ""
        self._jobs.mark_cancelled(job_id)
        if self._stage == "diarization":
            if self._previous_status in {"transcribed", "diarized"}:
                status = self._previous_status
            elif self._transcription_service.segment_count(session_id) > 0:
                status = "transcribed"
            elif self._normalised_path(session_id).is_file():
                status = "normalised"
            else:
                status = "audio_imported"
        elif self._normalised_ready or self._normalised_path(session_id).is_file():
            status = "normalised"
        else:
            status = self._previous_status or "audio_imported"
        try:
            self._sessions.restore_status(session_id, status)
        except AppError:
            pass
        self._cleanup_job_dir()
        self._reset_state()
        self.cancelled.emit(job_id)

    def _fail(
        self,
        code: str,
        message: str,
        session_status: str | None,
    ) -> None:
        job_id = self._job_id
        session_id = self._session_id
        if job_id is None or session_id is None:
            return
        self._jobs.mark_failed(job_id, code, message)
        self._abort_plan()
        if session_status == "normalisation_failed":
            try:
                self._sessions.mark_normalisation_failed(session_id, code)
            except AppError:
                pass
        elif session_status == "transcription_failed":
            try:
                self._sessions.mark_transcription_failed(session_id, code)
            except AppError:
                pass
        elif session_status == "diarization_failed":
            try:
                self._sessions.mark_diarization_failed(session_id, code)
            except AppError:
                pass
        self._cleanup_job_dir()
        self._reset_state()
        self.failed.emit(job_id, code, message)

    def _set_progress(self, progress: float, message: str) -> None:
        if self._job_id is None:
            return
        clamped = max(0.0, min(1.0, float(progress)))
        if abs(clamped - self._last_progress) < 0.005 and message == "Transcribing":
            return
        self._last_progress = clamped
        self._jobs.update_progress(self._job_id, clamped)
        self.progress.emit(self._job_id, clamped, message)

    def _normalised_path(self, session_id: str) -> Path:
        root = self._paths.audio_dir.resolve()
        path = (self._paths.audio_dir / session_id / "normalised.wav").resolve()
        if not path.is_relative_to(root):
            raise JobError(
                "audio_path_invalid",
                "The session audio storage path is not valid.",
            )
        return path

    def _cleanup_job_dir(self) -> None:
        if self._job_id is None:
            return
        job_dir = (self._paths.jobs_dir / self._job_id).resolve()
        jobs_root = self._paths.jobs_dir.resolve()
        if job_dir.is_relative_to(jobs_root):
            shutil.rmtree(job_dir, ignore_errors=True)

    def _stop_timers(self) -> None:
        self._poll_timer.stop()
        self._timeout_timer.stop()
        self._timeout_action = None

    def _abort_plan(self) -> None:
        if self._plan is not None:
            self._normalizer.abort(self._plan)
            self._plan = None

    def _reset_state(self) -> None:
        self._stop_timers()
        self._finished_slot = None
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
        self._job_id = None
        self._session_id = None
        self._previous_status = None
        self._plan = None
        self._stage = None
        self._status_path = None
        self._result_path = None
        self._model_dir = None
        self._model_name = None
        self._diarization_model_dir = None
        self._diarization_model_name = None
        self._normalised_ready = False
        self._cancelling = False
        self._handled = False
        self._timeout_error = None
        self._last_progress = -1.0
