from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..database.repositories import JobRepository
from ..errors import SessionError
from ..paths import AppPaths
from .sessions import SessionService
from .storage import session_directory

_ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})


@dataclass(frozen=True)
class DeletionResult:
    session_id: str
    removed_directories: int
    removed_job_directories: int


class SessionDeletionService:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        jobs: JobRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._jobs = jobs
        self._logger = logger or logging.getLogger(__name__)

    def delete_session(self, session_id: str) -> DeletionResult:
        session = self._sessions.get_session(session_id)
        if session is None:
            raise SessionError("session_not_found", "The selected session no longer exists.")

        jobs = self._jobs.list_for_session(session_id)
        if any(job.status in _ACTIVE_JOB_STATUSES for job in jobs):
            raise SessionError(
                "session_busy",
                "Cancel the running processing job before deleting this session.",
            )

        try:
            data_directories = [
                session_directory(root, session_id)
                for root in (
                    self._paths.audio_dir,
                    self._paths.transcripts_dir,
                    self._paths.reports_dir,
                    self._paths.exports_dir,
                )
            ]
            job_directories = [
                session_directory(self._paths.jobs_dir, job.id) for job in jobs if job.id
            ]
        except ValueError as exc:
            raise SessionError(
                "session_delete_failed",
                "The session has an unsafe identifier and was not deleted.",
            ) from exc

        removed_directories = 0
        for directory in data_directories:
            if _remove_path(directory):
                removed_directories += 1

        removed_job_directories = 0
        for directory in job_directories:
            if _remove_path(directory):
                removed_job_directories += 1

        try:
            deleted = self._sessions.delete_session(session_id)
        except sqlite3.Error as exc:
            raise SessionError(
                "session_delete_failed",
                "Session files were removed, but the database row could not be deleted. "
                "Retry the deletion.",
            ) from exc
        if not deleted:
            raise SessionError(
                "session_not_found",
                "The selected session no longer exists.",
            )
        self._logger.info(
            "session_data_deleted session_id=%s data_directories=%d job_directories=%d",
            session_id,
            removed_directories,
            removed_job_directories,
        )
        return DeletionResult(
            session_id=session_id,
            removed_directories=removed_directories,
            removed_job_directories=removed_job_directories,
        )


def _remove_path(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    except OSError as exc:
        raise SessionError(
            "session_delete_failed",
            "Could not remove all session files. The session was not deleted; retry after "
            "checking file permissions.",
        ) from exc
    return True
