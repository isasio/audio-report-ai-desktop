from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..config import AppConfig
from ..database.repositories import JobRepository
from ..errors import AppError, JobError, ReportError
from ..paths import AppPaths
from .local_llm_client import validate_report_model_name
from .report_models import REPORT_LANGUAGES, REPORT_LENGTHS
from .report_profiles import REPORT_PROFILES
from .reports import ReportService
from .sessions import SessionService

REPORT_TIMEOUT_SECONDS = 60 * 60
_JOB_DIR_NAME = "report"


class ReportCoordinator(QObject):
    started = Signal(str)
    progress = Signal(str, float, str)
    completed = Signal(str, str)
    failed = Signal(str, str, str)
    cancelled = Signal(str)

    def __init__(
        self,
        paths: AppPaths,
        config: AppConfig,
        sessions: SessionService,
        reports: ReportService,
        jobs: JobRepository,
        logger: logging.Logger | None = None,
        worker_command: list[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths = paths
        self._config = config
        self._sessions = sessions
        self._reports = reports
        self._jobs = jobs
        self._logger = logger or logging.getLogger(__name__)
        self._worker_command = worker_command or [
            sys.executable,
            "-m",
            "audio_report_ai.workers.report_worker",
        ]
        self._job_id: str | None = None
        self._session_id: str | None = None
        self._previous_status: str | None = None
        self._model_name: str | None = None
        self._report_language: str | None = None
        self._report_length: str | None = None
        self._report_profile: str | None = None
        self._input_path: Path | None = None
        self._result_path: Path | None = None
        self._status_path: Path | None = None
        self._handled = False
        self._cancelling = False
        self._last_progress = 0.0
        self._timeout_error: tuple[str, str] | None = None

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._discard_process_output)
        self._process.readyReadStandardError.connect(self._discard_process_output)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll_status)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill_if_running)

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
    def current_model_name(self) -> str | None:
        return self._model_name

    @property
    def current_report_language(self) -> str | None:
        return self._report_language

    @property
    def current_report_length(self) -> str | None:
        return self._report_length

    @property
    def current_report_profile(self) -> str | None:
        return self._report_profile

    def recover_stale_sessions(self) -> int:
        recovered = 0
        for session in self._sessions.list_sessions():
            if session.status != "generating_report":
                continue
            latest = self._reports.latest_report(session.id)
            if latest is not None:
                status = "report_approved" if latest.status == "approved" else "report_draft"
            else:
                status = "transcribed"
            try:
                self._sessions.restore_status(session.id, status)
            except AppError:
                self._logger.warning(
                    "stale_report_status_update_failed session_id=%s",
                    session.id,
                )
                continue
            recovered += 1
        if recovered:
            self._logger.info("stale_report_sessions_recovered count=%d", recovered)
        return recovered

    def start(
        self,
        session_id: str,
        model_name: str | None = None,
        language: str | None = None,
        length: str | None = None,
        profile: str | None = None,
        custom_instructions: str | None = None,
    ) -> str:
        if self._job_id is not None:
            raise JobError(
                "report_busy",
                "A report is already being generated. Wait for it to finish or cancel it.",
            )
        selected_model = validate_report_model_name(model_name or self._config.report_model)
        selected_language = language or self._config.report_language
        selected_length = length or self._config.report_length
        selected_profile = profile or self._config.report_profile
        instructions = (custom_instructions or "").strip() or None
        if selected_language not in REPORT_LANGUAGES:
            raise ReportError(
                "report_language_invalid",
                "The requested report language is not supported.",
            )
        if selected_length not in REPORT_LENGTHS:
            raise ReportError(
                "report_length_invalid",
                "The requested report length is not supported.",
            )
        if selected_profile not in REPORT_PROFILES:
            raise ReportError(
                "report_profile_invalid",
                "The requested report profile is not supported.",
            )
        if instructions is not None and len(instructions) > 8_000:
            raise ReportError(
                "report_instructions_too_long",
                "The custom writing instructions are too long.",
            )
        source = self._reports.build_source(session_id)
        if instructions is not None:
            source = source.model_copy(update={"custom_instructions": instructions})
        session = self._sessions.get_session(session_id)
        if session is None:
            raise JobError("session_not_found", "The selected session no longer exists.")

        job = self._jobs.create(session_id, "report")
        self._jobs.mark_running(job.id)
        self._job_id = job.id
        self._session_id = session_id
        self._model_name = selected_model
        self._report_language = selected_language
        self._report_length = selected_length
        self._report_profile = selected_profile
        self._previous_status = session.status
        self._handled = False
        self._cancelling = False
        self._last_progress = 0.0
        self._timeout_error = None
        self.started.emit(job.id)
        self._set_progress(0.02, "Preparing report")

        try:
            job_dir = self._paths.jobs_dir / job.id / _JOB_DIR_NAME
            job_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(job_dir, 0o700)
            self._input_path = job_dir / "input.json"
            self._result_path = job_dir / "result.json"
            self._status_path = job_dir / "status.json"
            _atomic_write_json(self._input_path, source.model_dump(mode="json"))
            self._sessions.mark_generating_report(session_id)
        except (OSError, AppError, ValueError):
            self._fail(
                "report_prepare_failed",
                "Could not prepare report generation.",
            )
            return job.id

        command = [
            *self._worker_command,
            "--input",
            str(self._input_path),
            "--model",
            self._model_name or self._config.report_model,
            "--language",
            self._report_language or self._config.report_language,
            "--length",
            self._report_length or self._config.report_length,
            "--profile",
            self._report_profile or self._config.report_profile,
            "--result",
            str(self._result_path),
            "--status",
            str(self._status_path),
        ]
        self._set_progress(0.05, "Starting local report model")
        self._process.start(command[0], command[1:])
        self._poll_timer.start()
        self._timeout_timer.start(REPORT_TIMEOUT_SECONDS * 1000)
        return job.id

    def cancel(self) -> bool:
        if self._job_id is None:
            return False
        if self._cancelling:
            return True
        self._cancelling = True
        self._set_progress(self._last_progress, "Cancelling report")
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            self._kill_timer.start(3000)
        else:
            self._finish_cancelled()
        return True

    def shutdown(self) -> None:
        if self._job_id is None:
            return
        self.cancel()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.waitForFinished(3000)

    def _poll_status(self) -> None:
        if self._job_id is None or self._status_path is None:
            return
        try:
            payload = json.loads(self._status_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        progress = payload.get("progress", self._last_progress)
        try:
            progress_value = max(0.0, min(0.95, float(progress)))
        except (TypeError, ValueError):
            progress_value = self._last_progress
        status = payload.get("status")
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            message = _status_message(status)
        self._set_progress(progress_value, message)

    def _on_process_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if self._handled or self._job_id is None:
            return
        self._stop_timers()
        self._discard_process_output()
        if self._cancelling:
            self._finish_cancelled()
            return
        if self._timeout_error is not None:
            self._fail(*self._timeout_error)
            return
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            code, message = self._failure_from_status()
            self._fail(code, message)
            return
        result = self._read_result()
        if result is None:
            self._fail(
                "report_invalid_result",
                "The local report worker did not produce a valid report.",
            )
            return
        session_id = self._session_id or ""
        try:
            record = self._reports.save_generated_report(
                session_id,
                result,
                profile=self._report_profile,
            )
        except AppError as exc:
            self._fail(exc.code, exc.message)
            return
        except Exception as exc:
            self._logger.error(
                "report_persist_failed session_id=%s error_type=%s",
                session_id,
                type(exc).__name__,
            )
            self._fail(
                "report_persist_failed",
                "Could not save the generated report.",
            )
            return
        if self._job_id is not None:
            self._jobs.mark_completed(self._job_id)
        job_id = self._job_id
        self._logger.info(
            "report_job_completed job_id=%s report_id=%s version=%d",
            job_id,
            record.id,
            record.version,
        )
        self._finish()
        self.completed.emit(job_id or "", record.id)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if self._handled or self._job_id is None:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._stop_timers()
            self._fail(
                "report_worker_failed",
                "Could not start the local report worker.",
            )

    def _on_timeout(self) -> None:
        if self._job_id is None or self._handled:
            return
        self._timeout_error = (
            "report_timeout",
            "Report generation timed out. Check the local model and try again.",
        )
        self._process.kill()

    def _kill_if_running(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()

    def _failure_from_status(self) -> tuple[str, str]:
        if self._status_path is not None:
            try:
                payload = json.loads(self._status_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                code = payload.get("error_code")
                message = payload.get("error_message")
                if isinstance(code, str) and code and isinstance(message, str) and message:
                    return code, message
        return "report_failed", "Local report generation failed."

    def _read_result(self) -> dict[str, object] | None:
        if self._result_path is None:
            return None
        try:
            payload = json.loads(self._result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _fail(self, code: str, message: str) -> None:
        if self._handled or self._job_id is None:
            return
        job_id = self._job_id
        session_id = self._session_id
        self._stop_timers()
        self._jobs.mark_failed(job_id, code, message)
        if session_id is not None:
            try:
                self._sessions.mark_report_failed(session_id, code)
            except AppError:
                self._logger.warning(
                    "report_session_status_failed session_id=%s status=failed",
                    session_id,
                )
        self._logger.info("report_job_failed job_id=%s error_code=%s", job_id, code)
        self._finish()
        self.failed.emit(job_id, code, message)

    def _finish_cancelled(self) -> None:
        if self._handled or self._job_id is None:
            return
        job_id = self._job_id
        session_id = self._session_id
        previous_status = self._previous_status
        self._stop_timers()
        self._jobs.mark_cancelled(job_id)
        if session_id is not None and previous_status is not None:
            try:
                self._sessions.restore_status(session_id, previous_status)
            except AppError:
                self._logger.warning(
                    "report_session_status_failed session_id=%s status=restore",
                    session_id,
                )
        self._logger.info("report_job_cancelled job_id=%s", job_id)
        self._finish()
        self.cancelled.emit(job_id)

    def _finish(self) -> None:
        self._handled = True
        self._cleanup_job_dir()
        self._job_id = None
        self._session_id = None
        self._previous_status = None
        self._model_name = None
        self._report_language = None
        self._report_length = None
        self._report_profile = None
        self._input_path = None
        self._result_path = None
        self._status_path = None
        self._cancelling = False
        self._timeout_error = None
        self._last_progress = 0.0

    def _set_progress(self, progress: float, message: str) -> None:
        if self._job_id is None:
            return
        value = max(0.0, min(0.95, float(progress)))
        self._last_progress = value
        self.progress.emit(self._job_id, value, message)

    def _stop_timers(self) -> None:
        self._poll_timer.stop()
        self._timeout_timer.stop()
        self._kill_timer.stop()

    def _discard_process_output(self) -> None:
        self._process.readAllStandardOutput()
        self._process.readAllStandardError()

    def _cleanup_job_dir(self) -> None:
        if self._job_id is None:
            return
        job_dir = (self._paths.jobs_dir / self._job_id).resolve()
        jobs_root = self._paths.jobs_dir.resolve()
        if job_dir.is_relative_to(jobs_root):
            shutil.rmtree(job_dir, ignore_errors=True)


def _status_message(status: object) -> str:
    if status == "starting":
        return "Starting report worker"
    if status == "verifying_model":
        return "Verifying local report model"
    if status == "generating":
        return "Generating report"
    if status == "completed":
        return "Saving report"
    return "Generating report"


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
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
