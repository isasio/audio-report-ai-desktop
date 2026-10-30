from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..database.repositories import SegmentRepository, TranscriptSegmentRecord
from ..errors import TranscriptionError
from ..paths import AppPaths
from ..schemas.corrected_transcript import (
    REVIEW_STATUS_EDITED,
    REVIEW_STATUS_UNREVIEWED,
    CorrectedTranscript,
    CorrectedTranscriptSegment,
)
from ..schemas.transcript import Transcript
from .sessions import SessionService

RAW_TRANSCRIPT_FILENAME = "raw.json"
CORRECTED_TRANSCRIPT_FILENAME = "corrected.json"


class TranscriptionService:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        segments: SegmentRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._segments = segments
        self._logger = logger or logging.getLogger(__name__)

    def persist_result(
        self,
        session_id: str,
        result: Transcript,
    ) -> list[TranscriptSegmentRecord]:
        if result.session_id != session_id:
            raise TranscriptionError(
                "transcription_session_mismatch",
                "The transcription result does not match the selected session.",
            )

        raw_path = self.raw_path(session_id)
        try:
            raw_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(raw_path.parent, 0o700)
            temporary = _write_temporary_json(raw_path, result.model_dump_json(indent=2) + "\n")
        except OSError as exc:
            raise TranscriptionError(
                "transcript_write_failed",
                "Could not store the raw transcript.",
            ) from exc

        records = [
            TranscriptSegmentRecord(
                id=segment.id,
                session_id=session_id,
                segment_index=segment.index,
                start_seconds=segment.start,
                end_seconds=segment.end,
                speaker_id=segment.speaker_id,
                raw_text=segment.text,
                corrected_text=None,
                review_status=REVIEW_STATUS_UNREVIEWED,
                metadata_json=_metadata_json(segment),
            )
            for segment in result.segments
        ]

        try:
            self._segments.replace_for_session(session_id, records)
            os.replace(temporary, raw_path)
            os.chmod(raw_path, 0o600)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise TranscriptionError(
                "transcript_persist_failed",
                "Could not save the transcript to the session.",
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        self._remove_corrected_transcript(session_id)

        try:
            self._sessions.mark_transcribed(session_id, len(records))
        except Exception as exc:
            raise TranscriptionError(
                "transcript_persist_failed",
                "Could not update the session after transcription.",
            ) from exc

        self._logger.info(
            "transcript_persisted session_id=%s segment_count=%d duration_seconds=%.3f language=%s",
            session_id,
            len(records),
            result.duration_seconds,
            result.language or "unknown",
        )
        return records

    def list_segments(self, session_id: str) -> list[TranscriptSegmentRecord]:
        return self._segments.list_for_session(session_id)

    def segment_count(self, session_id: str) -> int:
        return self._segments.count_for_session(session_id)

    def save_correction(
        self,
        session_id: str,
        segment_id: str,
        text: str,
    ) -> TranscriptSegmentRecord:
        if not isinstance(text, str):
            raise TranscriptionError(
                "transcript_correction_invalid",
                "The corrected transcript text is not valid.",
            )
        segment = self._segments.get_for_session(session_id, segment_id)
        if segment is None:
            raise TranscriptionError(
                "transcript_segment_not_found",
                "The selected transcript segment no longer exists.",
            )

        cleaned = text.strip()
        if not cleaned or cleaned == segment.raw_text:
            corrected_text = None
            review_status = REVIEW_STATUS_UNREVIEWED
        else:
            corrected_text = cleaned
            review_status = REVIEW_STATUS_EDITED

        updated = self._segments.update_correction(
            session_id,
            segment_id,
            corrected_text,
            review_status,
        )
        if updated is None:
            raise TranscriptionError(
                "transcript_segment_not_found",
                "The selected transcript segment no longer exists.",
            )

        try:
            self._write_corrected_transcript(session_id)
        except TranscriptionError:
            raise
        except (OSError, ValidationError) as exc:
            raise TranscriptionError(
                "corrected_transcript_write_failed",
                "Could not store the corrected transcript.",
            ) from exc

        self._logger.info(
            "transcript_correction_saved session_id=%s segment_id=%s review_status=%s",
            session_id,
            segment_id,
            review_status,
        )
        return updated

    def apply_speaker_labels(
        self,
        session_id: str,
        speaker_by_segment_id: dict[str, str | None],
    ) -> list[TranscriptSegmentRecord]:
        records = self._segments.list_for_session(session_id)
        if not records:
            raise TranscriptionError(
                "transcript_missing",
                "Transcribe the session before assigning speakers.",
            )
        known_ids = {record.id for record in records}
        if not set(speaker_by_segment_id).issubset(known_ids):
            raise TranscriptionError(
                "transcript_segment_not_found",
                "A speaker assignment refers to a segment that no longer exists.",
            )

        raw_path = self.raw_path(session_id)
        try:
            transcript = Transcript.model_validate_json(raw_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TranscriptionError(
                "raw_transcript_missing",
                "The raw transcript file is missing or unreadable.",
            ) from exc
        except (ValidationError, ValueError) as exc:
            raise TranscriptionError(
                "raw_transcript_invalid",
                "The raw transcript file is not valid.",
            ) from exc

        updated_segments = [
            segment.model_copy(
                update={"speaker_id": speaker_by_segment_id.get(segment.id, segment.speaker_id)}
            )
            for segment in transcript.segments
        ]
        updated_transcript = transcript.model_copy(update={"segments": updated_segments})

        try:
            temporary = _write_temporary_json(
                raw_path,
                updated_transcript.model_dump_json(indent=2) + "\n",
            )
        except OSError as exc:
            raise TranscriptionError(
                "transcript_write_failed",
                "Could not store the speaker labels in the raw transcript.",
            ) from exc

        try:
            self._segments.update_speakers(session_id, speaker_by_segment_id)
            os.replace(temporary, raw_path)
            os.chmod(raw_path, 0o600)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise TranscriptionError(
                "transcript_persist_failed",
                "Could not save the speaker labels.",
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        if self.corrected_path(session_id).is_file():
            try:
                self._write_corrected_transcript(session_id)
            except (OSError, ValidationError) as exc:
                raise TranscriptionError(
                    "corrected_transcript_write_failed",
                    "Could not update the corrected transcript with speaker labels.",
                ) from exc

        self._logger.info(
            "transcript_speakers_updated session_id=%s segment_count=%d speaker_count=%d",
            session_id,
            len(records),
            len({value for value in speaker_by_segment_id.values() if value is not None}),
        )
        return self.list_segments(session_id)

    def raw_path(self, session_id: str) -> Path:
        return self._transcript_path(session_id, RAW_TRANSCRIPT_FILENAME)

    def corrected_path(self, session_id: str) -> Path:
        return self._transcript_path(session_id, CORRECTED_TRANSCRIPT_FILENAME)

    def _transcript_path(self, session_id: str, filename: str) -> Path:
        root = self._paths.transcripts_dir.resolve()
        path = (self._paths.transcripts_dir / session_id / filename).resolve()
        if not path.is_relative_to(root):
            raise TranscriptionError(
                "transcript_path_invalid",
                "The transcript storage path is not valid.",
            )
        return path

    def _write_corrected_transcript(self, session_id: str) -> None:
        records = self._segments.list_for_session(session_id)
        payload = CorrectedTranscript(
            session_id=session_id,
            updated_at=_utc_now(),
            segments=[
                CorrectedTranscriptSegment(
                    id=record.id,
                    index=record.segment_index,
                    start=record.start_seconds,
                    end=record.end_seconds,
                    speaker_id=record.speaker_id,
                    raw_text=record.raw_text,
                    corrected_text=record.corrected_text,
                    review_status=record.review_status,
                )
                for record in records
            ],
        )

        destination = self.corrected_path(session_id)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(destination.parent, 0o700)
        temporary = _write_temporary_json(
            destination,
            payload.model_dump_json(indent=2) + "\n",
        )
        try:
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _remove_corrected_transcript(self, session_id: str) -> None:
        try:
            self.corrected_path(session_id).unlink(missing_ok=True)
        except (OSError, TranscriptionError) as exc:
            self._logger.warning(
                "corrected_transcript_cleanup_failed session_id=%s error_type=%s",
                session_id,
                type(exc).__name__,
            )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _metadata_json(segment: object) -> str:
    metadata = {
        "avg_logprob": getattr(segment, "avg_logprob", None),
        "no_speech_prob": getattr(segment, "no_speech_prob", None),
        "compression_ratio": getattr(segment, "compression_ratio", None),
    }
    return json.dumps(
        {key: value for key, value in metadata.items() if value is not None},
        sort_keys=True,
    )


def _write_temporary_json(destination: Path, payload: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        return temporary
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
