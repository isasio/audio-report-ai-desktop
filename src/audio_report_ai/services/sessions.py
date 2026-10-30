from __future__ import annotations

import logging

from ..database.repositories import Session, SessionRepository
from ..errors import SessionError


class SessionService:
    def __init__(
        self,
        repository: SessionRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self._repository = repository
        self._logger = logger or logging.getLogger(__name__)

    def create_session(self, title: str) -> Session:
        session = self._repository.create(title)
        self._logger.info("session_created session_id=%s", session.id)
        return session

    def list_sessions(self) -> list[Session]:
        return self._repository.list_all()

    def search_sessions(self, query: str) -> list[Session]:
        return self._repository.search(query)

    def get_session(self, session_id: str) -> Session | None:
        return self._repository.get(session_id)

    def attach_audio(
        self,
        session_id: str,
        original_audio_path: str,
        duration_seconds: float,
    ) -> Session:
        session = self._repository.set_audio(
            session_id,
            original_audio_path,
            duration_seconds,
        )
        if session is None:
            raise SessionError("session_not_found", "The selected session no longer exists.")
        self._logger.info(
            "session_audio_attached session_id=%s duration_seconds=%.3f",
            session.id,
            duration_seconds,
        )
        return session

    def mark_normalised(self, session_id: str) -> Session:
        session = self._repository.set_status(session_id, "normalised")
        if session is None:
            raise SessionError("session_not_found", "The selected session no longer exists.")
        self._logger.info("session_normalised session_id=%s", session.id)
        return session

    def mark_normalising(self, session_id: str) -> Session:
        session = self._set_status(session_id, "normalising")
        self._logger.info("session_normalising session_id=%s", session.id)
        return session

    def mark_transcribing(self, session_id: str) -> Session:
        session = self._set_status(session_id, "transcribing")
        self._logger.info("session_transcribing session_id=%s", session.id)
        return session

    def mark_transcribed(self, session_id: str, segment_count: int) -> Session:
        session = self._set_status(session_id, "transcribed")
        self._logger.info(
            "session_transcribed session_id=%s segment_count=%d",
            session.id,
            segment_count,
        )
        return session

    def mark_diarizing(self, session_id: str) -> Session:
        session = self._set_status(session_id, "diarizing")
        self._logger.info("session_diarizing session_id=%s", session.id)
        return session

    def mark_diarized(self, session_id: str) -> Session:
        session = self._set_status(session_id, "diarized")
        self._logger.info("session_diarized session_id=%s", session.id)
        return session

    def mark_diarization_failed(self, session_id: str, error_code: str) -> Session:
        session = self._set_status(session_id, "diarization_failed")
        self._logger.info(
            "session_diarization_failed session_id=%s error_code=%s",
            session.id,
            error_code,
        )
        return session

    def mark_normalisation_failed(self, session_id: str, error_code: str) -> Session:
        session = self._set_status(session_id, "normalisation_failed")
        self._logger.info(
            "session_normalisation_failed session_id=%s error_code=%s",
            session.id,
            error_code,
        )
        return session

    def mark_transcription_failed(self, session_id: str, error_code: str) -> Session:
        session = self._set_status(session_id, "transcription_failed")
        self._logger.info(
            "session_transcription_failed session_id=%s error_code=%s",
            session.id,
            error_code,
        )
        return session

    def mark_generating_report(self, session_id: str) -> Session:
        session = self._set_status(session_id, "generating_report")
        self._logger.info("session_generating_report session_id=%s", session.id)
        return session

    def mark_report_draft(self, session_id: str) -> Session:
        session = self._set_status(session_id, "report_draft")
        self._logger.info("session_report_draft session_id=%s", session.id)
        return session

    def mark_report_approved(self, session_id: str) -> Session:
        session = self._set_status(session_id, "report_approved")
        self._logger.info("session_report_approved session_id=%s", session.id)
        return session

    def mark_report_failed(self, session_id: str, error_code: str) -> Session:
        session = self._set_status(session_id, "report_failed")
        self._logger.info(
            "session_report_failed session_id=%s error_code=%s",
            session.id,
            error_code,
        )
        return session

    def restore_status(self, session_id: str, status: str) -> Session:
        return self._set_status(session_id, status)

    def _set_status(self, session_id: str, status: str) -> Session:
        session = self._repository.set_status(session_id, status)
        if session is None:
            raise SessionError("session_not_found", "The selected session no longer exists.")
        return session

    def delete_session(self, session_id: str) -> bool:
        deleted = self._repository.delete(session_id)
        if deleted:
            self._logger.info("session_deleted session_id=%s", session_id)
        return deleted
