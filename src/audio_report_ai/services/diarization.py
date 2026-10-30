from __future__ import annotations

import logging
from pathlib import Path

from ..atomic_json import write_json_atomic
from ..database.repositories import TranscriptSegmentRecord
from ..errors import DiarizationError
from ..paths import AppPaths
from ..schemas.diarization import DiarizationResult, DiarizationTurn
from .sessions import SessionService
from .transcription import TranscriptionService

DIARIZATION_FILENAME = "diarization.json"


def assign_speakers(
    segments: list[TranscriptSegmentRecord],
    turns: list[DiarizationTurn],
) -> dict[str, str | None]:
    assignments: dict[str, str | None] = {}
    for segment in segments:
        best_speaker: str | None = None
        best_overlap = 0.0
        for turn in turns:
            overlap = min(segment.end_seconds, turn.end) - max(segment.start_seconds, turn.start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = turn.speaker_id
        assignments[segment.id] = best_speaker
    return assignments


class DiarizationService:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        transcription: TranscriptionService,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._transcription = transcription
        self._logger = logger or logging.getLogger(__name__)

    def apply_result(
        self,
        session_id: str,
        result: DiarizationResult,
    ) -> list[TranscriptSegmentRecord]:
        if result.session_id != session_id:
            raise DiarizationError(
                "diarization_session_mismatch",
                "The diarization result does not match the selected session.",
            )
        segments = self._transcription.list_segments(session_id)
        if not segments:
            raise DiarizationError(
                "transcript_missing",
                "Transcribe the session before running speaker diarization.",
            )

        assignments = assign_speakers(segments, result.turns)
        try:
            write_json_atomic(
                self.diarization_path(session_id),
                result.model_dump(mode="json"),
            )
        except OSError as exc:
            raise DiarizationError(
                "diarization_write_failed",
                "Could not store the diarization result.",
            ) from exc

        updated = self._transcription.apply_speaker_labels(session_id, assignments)
        self._sessions.mark_diarized(session_id)
        self._logger.info(
            "diarization_applied session_id=%s turn_count=%d speaker_count=%d",
            session_id,
            len(result.turns),
            len({turn.speaker_id for turn in result.turns}),
        )
        return updated

    def diarization_path(self, session_id: str) -> Path:
        root = self._paths.transcripts_dir.resolve()
        path = (self._paths.transcripts_dir / session_id / DIARIZATION_FILENAME).resolve()
        if not path.is_relative_to(root):
            raise DiarizationError(
                "diarization_path_invalid",
                "The diarization storage path is not valid.",
            )
        return path
