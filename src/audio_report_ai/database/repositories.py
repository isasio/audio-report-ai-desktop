from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..errors import SessionError
from .search import normalize_search_text


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise SessionError("session_invalid_title", "Session title cannot be empty.")
    if len(cleaned) > 200:
        raise SessionError(
            "session_invalid_title",
            "Session title must be 200 characters or fewer.",
        )
    return cleaned


def _like_pattern(value: str) -> str:
    escaped = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


@dataclass(frozen=True)
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    profile: str
    language: str
    status: str
    original_audio_path: str | None
    duration_seconds: float | None


@dataclass(frozen=True)
class TranscriptSegmentRecord:
    id: str
    session_id: str
    segment_index: int
    start_seconds: float
    end_seconds: float
    speaker_id: str | None
    raw_text: str
    corrected_text: str | None
    review_status: str
    metadata_json: str


@dataclass(frozen=True)
class SpeakerRoleRecord:
    session_id: str
    speaker_id: str
    display_name: str | None
    role: str | None
    updated_at: str


@dataclass(frozen=True)
class ProcessingJob:
    id: str
    session_id: str
    stage: str
    status: str
    progress: float
    error_code: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class ReportRecord:
    id: str
    session_id: str
    version: int
    schema_name: str
    schema_version: int
    report_json: str
    status: str
    created_at: str
    updated_at: str
    approved_at: str | None


@dataclass(frozen=True)
class ReportEvidenceRecord:
    report_id: str
    claim_path: str
    transcript_segment_id: str
    note: str | None


class SessionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        title: str,
        profile: str = "conversation",
        language: str = "es",
    ) -> Session:
        cleaned = _validate_title(title)
        now = _utc_now()
        session = Session(
            id=str(uuid.uuid4()),
            title=cleaned,
            created_at=now,
            updated_at=now,
            profile=profile,
            language=language,
            status="created",
            original_audio_path=None,
            duration_seconds=None,
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO sessions (
                    id, title, created_at, updated_at, profile, language, status,
                    original_audio_path, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    session.created_at,
                    session.updated_at,
                    session.profile,
                    session.language,
                    session.status,
                    session.original_audio_path,
                    session.duration_seconds,
                ),
            )
        return session

    def get(self, session_id: str) -> Session | None:
        row = self._connection.execute(
            """
            SELECT id, title, created_at, updated_at, profile, language, status,
                   original_audio_path, duration_seconds
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        return self._row_to_session(row) if row is not None else None

    def list_all(self) -> list[Session]:
        rows = self._connection.execute(
            """
            SELECT id, title, created_at, updated_at, profile, language, status,
                   original_audio_path, duration_seconds
            FROM sessions
            ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def search(self, query: str, limit: int = 200) -> list[Session]:
        cleaned = query.strip()
        if not cleaned:
            return self.list_all()
        if limit <= 0:
            return []
        pattern = _like_pattern(normalize_search_text(cleaned))
        rows = self._connection.execute(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.profile, s.language,
                   s.status, s.original_audio_path, s.duration_seconds
            FROM sessions AS s
            WHERE app_normalize_search(s.title) LIKE ? ESCAPE '!'
               OR EXISTS (
                   SELECT 1
                   FROM transcript_segments AS ts
                   WHERE ts.session_id = s.id
                     AND (
                         app_normalize_search(ts.raw_text) LIKE ? ESCAPE '!'
                         OR app_normalize_search(COALESCE(ts.corrected_text, '')) LIKE ? ESCAPE '!'
                     )
               )
               OR EXISTS (
                   SELECT 1
                   FROM reports AS r
                   WHERE r.session_id = s.id
                     AND app_normalize_search(app_report_search_text(r.report_json))
                         LIKE ? ESCAPE '!'
               )
            ORDER BY s.created_at DESC, s.rowid DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def update_title(self, session_id: str, title: str) -> bool:
        cleaned = _validate_title(title)
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned, _utc_now(), session_id),
            )
        return cursor.rowcount > 0

    def set_status(self, session_id: str, status: str) -> Session | None:
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, _utc_now(), session_id),
            )
        return self.get(session_id) if cursor.rowcount > 0 else None

    def set_audio(
        self,
        session_id: str,
        original_audio_path: str,
        duration_seconds: float,
        status: str = "audio_imported",
    ) -> Session | None:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE sessions
                SET original_audio_path = ?, duration_seconds = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (original_audio_path, duration_seconds, status, _utc_now(), session_id),
            )
        return self.get(session_id) if cursor.rowcount > 0 else None

    def delete(self, session_id: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            profile=row["profile"],
            language=row["language"],
            status=row["status"],
            original_audio_path=row["original_audio_path"],
            duration_seconds=row["duration_seconds"],
        )


class SegmentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def replace_for_session(
        self,
        session_id: str,
        segments: Sequence[TranscriptSegmentRecord],
    ) -> list[TranscriptSegmentRecord]:
        if any(segment.session_id != session_id for segment in segments):
            raise ValueError("All segments must belong to the requested session.")
        if len({segment.id for segment in segments}) != len(segments):
            raise ValueError("Segment IDs must be unique.")
        if len({segment.segment_index for segment in segments}) != len(segments):
            raise ValueError("Segment indexes must be unique.")

        with self._connection:
            self._connection.execute(
                "DELETE FROM transcript_segments WHERE session_id = ?",
                (session_id,),
            )
            self._connection.executemany(
                """
                INSERT INTO transcript_segments (
                    id, session_id, segment_index, start_seconds, end_seconds,
                    speaker_id, raw_text, corrected_text, review_status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        segment.id,
                        segment.session_id,
                        segment.segment_index,
                        segment.start_seconds,
                        segment.end_seconds,
                        segment.speaker_id,
                        segment.raw_text,
                        segment.corrected_text,
                        segment.review_status,
                        segment.metadata_json,
                    )
                    for segment in segments
                ],
            )
        return self.list_for_session(session_id)

    def list_for_session(self, session_id: str) -> list[TranscriptSegmentRecord]:
        rows = self._connection.execute(
            """
            SELECT id, session_id, segment_index, start_seconds, end_seconds,
                   speaker_id, raw_text, corrected_text, review_status, metadata_json
            FROM transcript_segments
            WHERE session_id = ?
            ORDER BY segment_index
            """,
            (session_id,),
        ).fetchall()
        return [self._row_to_segment(row) for row in rows]

    def get_for_session(
        self,
        session_id: str,
        segment_id: str,
    ) -> TranscriptSegmentRecord | None:
        row = self._connection.execute(
            """
            SELECT id, session_id, segment_index, start_seconds, end_seconds,
                   speaker_id, raw_text, corrected_text, review_status, metadata_json
            FROM transcript_segments
            WHERE id = ? AND session_id = ?
            """,
            (segment_id, session_id),
        ).fetchone()
        return self._row_to_segment(row) if row is not None else None

    def update_correction(
        self,
        session_id: str,
        segment_id: str,
        corrected_text: str | None,
        review_status: str,
    ) -> TranscriptSegmentRecord | None:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE transcript_segments
                SET corrected_text = ?, review_status = ?
                WHERE id = ? AND session_id = ?
                """,
                (corrected_text, review_status, segment_id, session_id),
            )
            if cursor.rowcount == 0:
                return None
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_utc_now(), session_id),
            )
        return self.get_for_session(session_id, segment_id)

    def count_for_session(self, session_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM transcript_segments WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def update_speakers(
        self,
        session_id: str,
        speaker_by_segment_id: dict[str, str | None],
    ) -> list[TranscriptSegmentRecord]:
        if not speaker_by_segment_id:
            return self.list_for_session(session_id)
        with self._connection:
            self._connection.executemany(
                """
                UPDATE transcript_segments
                SET speaker_id = ?
                WHERE id = ? AND session_id = ?
                """,
                [
                    (speaker_id, segment_id, session_id)
                    for segment_id, speaker_id in speaker_by_segment_id.items()
                ],
            )
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_utc_now(), session_id),
            )
        return self.list_for_session(session_id)

    def speaker_counts_for_session(self, session_id: str) -> list[tuple[str, int]]:
        rows = self._connection.execute(
            """
            SELECT speaker_id, COUNT(*)
            FROM transcript_segments
            WHERE session_id = ? AND speaker_id IS NOT NULL
            GROUP BY speaker_id
            ORDER BY speaker_id
            """,
            (session_id,),
        ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    @staticmethod
    def _row_to_segment(row: sqlite3.Row) -> TranscriptSegmentRecord:
        return TranscriptSegmentRecord(
            id=row["id"],
            session_id=row["session_id"],
            segment_index=row["segment_index"],
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            speaker_id=row["speaker_id"],
            raw_text=row["raw_text"],
            corrected_text=row["corrected_text"],
            review_status=row["review_status"],
            metadata_json=row["metadata_json"],
        )


class SpeakerRoleRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_for_session(self, session_id: str) -> list[SpeakerRoleRecord]:
        rows = self._connection.execute(
            """
            SELECT session_id, speaker_id, display_name, role, updated_at
            FROM speaker_roles
            WHERE session_id = ?
            ORDER BY speaker_id
            """,
            (session_id,),
        ).fetchall()
        return [self._row_to_role(row) for row in rows]

    def get(self, session_id: str, speaker_id: str) -> SpeakerRoleRecord | None:
        row = self._connection.execute(
            """
            SELECT session_id, speaker_id, display_name, role, updated_at
            FROM speaker_roles
            WHERE session_id = ? AND speaker_id = ?
            """,
            (session_id, speaker_id),
        ).fetchone()
        return self._row_to_role(row) if row is not None else None

    def set(
        self,
        session_id: str,
        speaker_id: str,
        display_name: str | None,
        role: str | None,
    ) -> SpeakerRoleRecord:
        now = _utc_now()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO speaker_roles (
                    session_id, speaker_id, display_name, role, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, speaker_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    role = excluded.role,
                    updated_at = excluded.updated_at
                """,
                (session_id, speaker_id, display_name, role, now),
            )
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        record = self.get(session_id, speaker_id)
        if record is None:
            raise RuntimeError("Could not store speaker role.")
        return record

    def delete(self, session_id: str, speaker_id: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM speaker_roles WHERE session_id = ? AND speaker_id = ?",
                (session_id, speaker_id),
            )
            if cursor.rowcount:
                self._connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (_utc_now(), session_id),
                )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_role(row: sqlite3.Row) -> SpeakerRoleRecord:
        return SpeakerRoleRecord(
            session_id=row["session_id"],
            speaker_id=row["speaker_id"],
            display_name=row["display_name"],
            role=row["role"],
            updated_at=row["updated_at"],
        )


class ReportRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_version(self, session_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM reports WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 1

    def create(
        self,
        session_id: str,
        version: int,
        schema_name: str,
        schema_version: int,
        report_json: str,
        evidence: Sequence[ReportEvidenceRecord],
        status: str = "draft",
    ) -> ReportRecord:
        if version <= 0:
            raise ValueError("Report version must be positive.")
        if status not in {"draft", "approved"}:
            raise ValueError("Unsupported report status.")
        if any(item.report_id != "" for item in evidence):
            raise ValueError("Evidence report IDs are assigned by the repository.")
        report_id = str(uuid.uuid4())
        now = _utc_now()
        approved_at = now if status == "approved" else None
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO reports (
                    id, session_id, version, schema_name, schema_version,
                    report_json, status, created_at, updated_at, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    session_id,
                    version,
                    schema_name,
                    schema_version,
                    report_json,
                    status,
                    now,
                    now,
                    approved_at,
                ),
            )
            self._insert_evidence(report_id, evidence)
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        report = self.get(report_id)
        if report is None:
            raise RuntimeError("Could not store the report.")
        return report

    def get(self, report_id: str) -> ReportRecord | None:
        row = self._connection.execute(
            """
            SELECT id, session_id, version, schema_name, schema_version,
                   report_json, status, created_at, updated_at, approved_at
            FROM reports
            WHERE id = ?
            """,
            (report_id,),
        ).fetchone()
        return self._row_to_report(row) if row is not None else None

    def latest_for_session(self, session_id: str) -> ReportRecord | None:
        row = self._connection.execute(
            """
            SELECT id, session_id, version, schema_name, schema_version,
                   report_json, status, created_at, updated_at, approved_at
            FROM reports
            WHERE session_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return self._row_to_report(row) if row is not None else None

    def list_for_session(self, session_id: str) -> list[ReportRecord]:
        rows = self._connection.execute(
            """
            SELECT id, session_id, version, schema_name, schema_version,
                   report_json, status, created_at, updated_at, approved_at
            FROM reports
            WHERE session_id = ?
            ORDER BY version DESC
            """,
            (session_id,),
        ).fetchall()
        return [self._row_to_report(row) for row in rows]

    def evidence_for_report(self, report_id: str) -> list[ReportEvidenceRecord]:
        rows = self._connection.execute(
            """
            SELECT report_id, claim_path, transcript_segment_id, note
            FROM report_evidence
            WHERE report_id = ?
            ORDER BY claim_path, transcript_segment_id
            """,
            (report_id,),
        ).fetchall()
        return [self._row_to_evidence(row) for row in rows]

    def update_draft(
        self,
        report_id: str,
        report_json: str,
        evidence: Sequence[ReportEvidenceRecord],
    ) -> ReportRecord | None:
        row = self._connection.execute(
            "SELECT session_id, status FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        if row is None or row["status"] != "draft":
            return None
        session_id = str(row["session_id"])
        now = _utc_now()
        with self._connection:
            self._connection.execute(
                "UPDATE reports SET report_json = ?, updated_at = ? WHERE id = ?",
                (report_json, now, report_id),
            )
            self._connection.execute(
                "DELETE FROM report_evidence WHERE report_id = ?",
                (report_id,),
            )
            self._insert_evidence(report_id, evidence)
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        return self.get(report_id)

    def approve(self, report_id: str) -> ReportRecord | None:
        now = _utc_now()
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE reports
                SET status = 'approved', approved_at = ?, updated_at = ?
                WHERE id = ? AND status = 'draft'
                """,
                (now, now, report_id),
            )
        return self.get(report_id) if cursor.rowcount else None

    def delete(self, report_id: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM reports WHERE id = ?",
                (report_id,),
            )
        return cursor.rowcount > 0

    def _insert_evidence(
        self,
        report_id: str,
        evidence: Sequence[ReportEvidenceRecord],
    ) -> None:
        unique: dict[tuple[str, str], ReportEvidenceRecord] = {}
        for item in evidence:
            unique[(item.claim_path, item.transcript_segment_id)] = item
        self._connection.executemany(
            """
            INSERT INTO report_evidence (
                report_id, claim_path, transcript_segment_id, note
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    report_id,
                    item.claim_path,
                    item.transcript_segment_id,
                    item.note,
                )
                for item in unique.values()
            ],
        )

    @staticmethod
    def _row_to_report(row: sqlite3.Row) -> ReportRecord:
        return ReportRecord(
            id=row["id"],
            session_id=row["session_id"],
            version=row["version"],
            schema_name=row["schema_name"],
            schema_version=row["schema_version"],
            report_json=row["report_json"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            approved_at=row["approved_at"],
        )

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> ReportEvidenceRecord:
        return ReportEvidenceRecord(
            report_id=row["report_id"],
            claim_path=row["claim_path"],
            transcript_segment_id=row["transcript_segment_id"],
            note=row["note"],
        )


class JobRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, session_id: str, stage: str) -> ProcessingJob:
        job_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO processing_jobs (
                    id, session_id, stage, status, progress, created_at
                ) VALUES (?, ?, ?, 'queued', 0.0, ?)
                """,
                (job_id, session_id, stage, now),
            )
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("Could not create processing job.")
        return job

    def get(self, job_id: str) -> ProcessingJob | None:
        row = self._connection.execute(
            """
            SELECT id, session_id, stage, status, progress, error_code,
                   error_message, created_at, started_at, finished_at
            FROM processing_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def latest_for_session(self, session_id: str) -> ProcessingJob | None:
        row = self._connection.execute(
            """
            SELECT id, session_id, stage, status, progress, error_code,
                   error_message, created_at, started_at, finished_at
            FROM processing_jobs
            WHERE session_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_for_session(self, session_id: str) -> list[ProcessingJob]:
        rows = self._connection.execute(
            """
            SELECT id, session_id, stage, status, progress, error_code,
                   error_message, created_at, started_at, finished_at
            FROM processing_jobs
            WHERE session_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (session_id,),
        ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def mark_running(self, job_id: str) -> ProcessingJob | None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'running', progress = 0.0, started_at = ?,
                    error_code = NULL, error_message = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (_utc_now(), job_id),
            )
        return self.get(job_id)

    def update_progress(self, job_id: str, progress: float) -> ProcessingJob | None:
        clamped = max(0.0, min(1.0, float(progress)))
        with self._connection:
            self._connection.execute(
                """
                UPDATE processing_jobs
                SET progress = ?
                WHERE id = ? AND status = 'running'
                """,
                (clamped, job_id),
            )
        return self.get(job_id)

    def mark_completed(self, job_id: str) -> ProcessingJob | None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'completed', progress = 1.0, finished_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_utc_now(), job_id),
            )
        return self.get(job_id)

    def mark_failed(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
    ) -> ProcessingJob | None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'failed', error_code = ?, error_message = ?, finished_at = ?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (error_code, error_message, _utc_now(), job_id),
            )
        return self.get(job_id)

    def mark_cancelled(self, job_id: str) -> ProcessingJob | None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'cancelled', finished_at = ?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (_utc_now(), job_id),
            )
        return self.get(job_id)

    def mark_stale_running_as_failed(
        self,
        error_code: str = "job_interrupted",
        error_message: str = "Processing was interrupted. Retry the operation.",
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'failed', error_code = ?, error_message = ?, finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (error_code, error_message, _utc_now()),
            )
        return cursor.rowcount

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ProcessingJob:
        return ProcessingJob(
            id=row["id"],
            session_id=row["session_id"],
            stage=row["stage"],
            status=row["status"],
            progress=row["progress"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
