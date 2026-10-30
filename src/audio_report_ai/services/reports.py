from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from pydantic import ValidationError

from ..database.repositories import (
    ReportEvidenceRecord,
    ReportRecord,
    ReportRepository,
    SegmentRepository,
    SpeakerRoleRecord,
    SpeakerRoleRepository,
)
from ..errors import AppError, ReportError
from ..paths import AppPaths
from ..schemas.report import (
    REPORT_SCHEMA_NAME,
    REPORT_SCHEMA_VERSION,
    ConversationReport,
)
from ..schemas.report_generation import ReportGenerationSource, ReportSourceSegment
from .report_validator import ReportEvidenceLink, validate_report
from .sessions import SessionService

REPORT_STATUS_DRAFT = "draft"
REPORT_STATUS_APPROVED = "approved"


class ReportService:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        segments: SegmentRepository,
        speaker_roles: SpeakerRoleRepository,
        reports: ReportRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._segments = segments
        self._speaker_roles = speaker_roles
        self._reports = reports
        self._logger = logger or logging.getLogger(__name__)

    def build_source(self, session_id: str) -> ReportGenerationSource:
        session = self._sessions.get_session(session_id)
        if session is None:
            raise ReportError("session_not_found", "The selected session no longer exists.")
        records = self._segments.list_for_session(session_id)
        if not records:
            raise ReportError(
                "transcript_missing",
                "Transcribe the session before generating a report.",
            )
        roles = {role.speaker_id: role for role in self._speaker_roles.list_for_session(session_id)}
        source_segments: list[ReportSourceSegment] = []
        for record in records:
            text = (
                record.corrected_text if record.corrected_text is not None else record.raw_text
            ).strip()
            if not text:
                continue
            source_segments.append(
                ReportSourceSegment(
                    id=record.id,
                    start_seconds=record.start_seconds,
                    end_seconds=record.end_seconds,
                    speaker=_speaker_label(record.speaker_id, roles),
                    text=text,
                )
            )
        if not source_segments:
            raise ReportError(
                "transcript_empty",
                "The transcript does not contain any text to report on.",
            )
        return ReportGenerationSource(
            session_id=session.id,
            title=session.title,
            language=session.language,
            segments=source_segments,
        )

    def save_generated_report(
        self,
        session_id: str,
        payload: object,
        profile: str | None = None,
    ) -> ReportRecord:
        source = self.build_source(session_id)
        report, evidence = self._validate_payload(session_id, payload, source)
        if profile is not None and report.report_profile != profile:
            report = report.model_copy(update={"report_profile": profile})
        version = self._reports.next_version(session_id)
        report_json = report.model_dump_json(indent=2)
        path = self.report_path(session_id, version)
        if path.exists():
            raise ReportError(
                "report_version_conflict",
                "The next report version already exists on disk.",
            )
        try:
            _atomic_write_text(path, report_json + "\n")
        except OSError as exc:
            raise ReportError(
                "report_write_failed",
                "Could not store the report file.",
            ) from exc
        try:
            record = self._reports.create(
                session_id,
                version,
                REPORT_SCHEMA_NAME,
                REPORT_SCHEMA_VERSION,
                report_json,
                _evidence_records(evidence),
            )
        except (sqlite3.Error, ValueError) as exc:
            _unlink(path)
            raise ReportError(
                "report_persist_failed",
                "Could not save the report to the session.",
            ) from exc
        self._mark_draft(session_id)
        self._logger.info(
            (
                "report_generated session_id=%s report_id=%s version=%d "
                "evidence_count=%d review_flags=%d"
            ),
            session_id,
            record.id,
            record.version,
            len(evidence),
            len(report.items_requiring_review),
        )
        return record

    def update_draft_report(self, report_id: str, payload: object) -> ReportRecord:
        existing = self._reports.get(report_id)
        if existing is None:
            raise ReportError(
                "report_not_found",
                "The selected report no longer exists.",
            )
        if existing.status != REPORT_STATUS_DRAFT:
            raise ReportError(
                "report_approved",
                "Approved reports cannot be edited.",
            )
        source = self.build_source(existing.session_id)
        report, evidence = self._validate_payload(existing.session_id, payload, source)
        report_json = report.model_dump_json(indent=2)
        path = self.report_path(existing.session_id, existing.version)
        try:
            previous_content = path.read_text(encoding="utf-8")
        except OSError:
            previous_content = existing.report_json + "\n"
        try:
            _atomic_write_text(path, report_json + "\n")
        except OSError as exc:
            raise ReportError(
                "report_write_failed",
                "Could not update the report file.",
            ) from exc
        try:
            updated = self._reports.update_draft(
                report_id,
                report_json,
                _evidence_records(evidence),
            )
        except sqlite3.Error as exc:
            _restore_text(path, previous_content)
            raise ReportError(
                "report_persist_failed",
                "Could not update the report.",
            ) from exc
        if updated is None:
            _restore_text(path, previous_content)
            raise ReportError(
                "report_approved",
                "Approved reports cannot be edited.",
            )
        self._mark_draft(existing.session_id)
        self._logger.info(
            "report_updated report_id=%s session_id=%s version=%d evidence_count=%d",
            report_id,
            existing.session_id,
            existing.version,
            len(evidence),
        )
        return updated

    def approve_report(self, report_id: str) -> ReportRecord:
        existing = self._reports.get(report_id)
        if existing is None:
            raise ReportError(
                "report_not_found",
                "The selected report no longer exists.",
            )
        if existing.status == REPORT_STATUS_APPROVED:
            raise ReportError(
                "report_already_approved",
                "This report is already approved.",
            )
        approved = self._reports.approve(report_id)
        if approved is None:
            raise ReportError(
                "report_not_found",
                "The selected report no longer exists.",
            )
        try:
            self._sessions.mark_report_approved(approved.session_id)
        except AppError:
            self._logger.warning(
                "report_session_status_failed report_id=%s status=approved",
                approved.id,
            )
        self._logger.info(
            "report_approved report_id=%s session_id=%s version=%d",
            approved.id,
            approved.session_id,
            approved.version,
        )
        return approved

    def latest_report(self, session_id: str) -> ReportRecord | None:
        return self._reports.latest_for_session(session_id)

    def get_report(self, report_id: str) -> ReportRecord | None:
        return self._reports.get(report_id)

    def list_reports(self, session_id: str) -> list[ReportRecord]:
        return self._reports.list_for_session(session_id)

    def evidence_for_report(self, report_id: str) -> list[ReportEvidenceRecord]:
        return self._reports.evidence_for_report(report_id)

    def report_path(self, session_id: str, version: int) -> Path:
        return self._paths.reports_dir / session_id / f"report_v{version:03d}.json"

    def _validate_payload(
        self,
        session_id: str,
        payload: object,
        source: ReportGenerationSource,
    ) -> tuple[ConversationReport, list[ReportEvidenceLink]]:
        try:
            report = ConversationReport.model_validate(payload)
        except ValidationError as exc:
            raise ReportError(
                "report_invalid",
                "The report content does not match the conversation report schema.",
            ) from exc
        roles = self._speaker_roles.list_for_session(session_id)
        manual_names = [
            value
            for role in roles
            for value in (role.display_name, role.role)
            if value and value.strip()
        ]
        validation = validate_report(
            report,
            {segment.id: segment.text for segment in source.segments},
            manual_names,
        )
        return validation.report, validation.evidence

    def _mark_draft(self, session_id: str) -> None:
        try:
            self._sessions.mark_report_draft(session_id)
        except AppError:
            self._logger.warning(
                "report_session_status_failed session_id=%s status=draft",
                session_id,
            )


def _speaker_label(
    speaker_id: str | None,
    roles: dict[str, SpeakerRoleRecord],
) -> str | None:
    if speaker_id is None:
        return None
    role = roles.get(speaker_id)
    if role is None:
        return speaker_id
    display_name = (role.display_name or "").strip()
    role_name = (role.role or "").strip()
    if display_name and role_name:
        return f"{display_name} ({role_name})"
    if display_name:
        return display_name
    if role_name:
        return role_name
    return speaker_id


def _evidence_records(evidence: list[ReportEvidenceLink]) -> list[ReportEvidenceRecord]:
    return [
        ReportEvidenceRecord(
            report_id="",
            claim_path=item.claim_path,
            transcript_segment_id=item.transcript_segment_id,
            note=item.note,
        )
        for item in evidence
    ]


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
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


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _restore_text(path: Path, content: str) -> None:
    try:
        _atomic_write_text(path, content)
    except OSError:
        pass
