from __future__ import annotations

import logging
from dataclasses import dataclass

from ..database.repositories import SegmentRepository, SpeakerRoleRepository
from ..errors import SpeakerError
from .sessions import SessionService

_MAX_LABEL_LENGTH = 100


@dataclass(frozen=True)
class Speaker:
    speaker_id: str
    segment_count: int
    display_name: str | None = None
    role: str | None = None

    @property
    def label(self) -> str:
        if self.display_name and self.role:
            return f"{self.display_name} ({self.role})"
        if self.display_name:
            return self.display_name
        if self.role:
            return f"{self.speaker_id} ({self.role})"
        return self.speaker_id


class SpeakerService:
    def __init__(
        self,
        sessions: SessionService,
        segments: SegmentRepository,
        roles: SpeakerRoleRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self._sessions = sessions
        self._segments = segments
        self._roles = roles
        self._logger = logger or logging.getLogger(__name__)

    def list_speakers(self, session_id: str) -> list[Speaker]:
        if self._sessions.get_session(session_id) is None:
            raise SpeakerError("session_not_found", "The selected session no longer exists.")
        roles = {record.speaker_id: record for record in self._roles.list_for_session(session_id)}
        speakers: list[Speaker] = []
        for speaker_id, count in self._segments.speaker_counts_for_session(session_id):
            role = roles.get(speaker_id)
            speakers.append(
                Speaker(
                    speaker_id=speaker_id,
                    segment_count=count,
                    display_name=role.display_name if role is not None else None,
                    role=role.role if role is not None else None,
                )
            )
        return speakers

    def labels_for_session(self, session_id: str) -> dict[str, str]:
        return {speaker.speaker_id: speaker.label for speaker in self.list_speakers(session_id)}

    def set_role(
        self,
        session_id: str,
        speaker_id: str,
        display_name: str | None,
        role: str | None,
    ) -> Speaker | None:
        if self._sessions.get_session(session_id) is None:
            raise SpeakerError("session_not_found", "The selected session no longer exists.")
        counts = dict(self._segments.speaker_counts_for_session(session_id))
        if speaker_id not in counts:
            raise SpeakerError(
                "speaker_not_found",
                "The selected speaker label no longer exists in this transcript.",
            )

        clean_name = _clean_label(display_name, "speaker_invalid_name", "Speaker name")
        clean_role = _clean_label(role, "speaker_invalid_role", "Speaker role")
        if clean_name is None and clean_role is None:
            self._roles.delete(session_id, speaker_id)
        else:
            self._roles.set(session_id, speaker_id, clean_name, clean_role)

        self._logger.info("speaker_role_saved session_id=%s speaker_id=%s", session_id, speaker_id)
        for speaker in self.list_speakers(session_id):
            if speaker.speaker_id == speaker_id:
                return speaker
        return None


def _clean_label(
    value: str | None,
    code: str,
    label: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SpeakerError(code, f"{label} must be text.")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_LABEL_LENGTH:
        raise SpeakerError(code, f"{label} must be {_MAX_LABEL_LENGTH} characters or fewer.")
    return cleaned
