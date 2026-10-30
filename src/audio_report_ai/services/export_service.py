from __future__ import annotations

import html
import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from docx import Document
from docx.shared import Pt

from ..database.repositories import (
    ReportEvidenceRecord,
    ReportRecord,
    ReportRepository,
    SegmentRepository,
    TranscriptSegmentRecord,
)
from ..errors import ExportError
from ..paths import AppPaths
from ..schemas.report import ConversationReport
from .report_profiles import profile_label, section_labels
from .sessions import SessionService
from .storage import session_directory

ExportFormat = Literal["docx", "html", "pdf", "json"]
_SUPPORTED_FORMATS = frozenset({"docx", "html", "pdf", "json"})
_FORMAT_SUFFIX = {"docx": ".docx", "html": ".html", "pdf": ".pdf", "json": ".json"}
_EVIDENCE_EXCERPT_LIMIT = 400


@dataclass(frozen=True)
class ExportResult:
    path: Path
    report_id: str
    version: int
    status: str
    format: str = "docx"


class ExportService:
    def __init__(
        self,
        paths: AppPaths,
        sessions: SessionService,
        segments: SegmentRepository,
        reports: ReportRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._sessions = sessions
        self._segments = segments
        self._reports = reports
        self._logger = logger or logging.getLogger(__name__)

    def export_report(
        self,
        session_id: str,
        report_id: str,
        format: str = "docx",
    ) -> ExportResult:
        if format not in _SUPPORTED_FORMATS:
            raise ExportError(
                "export_format_invalid",
                "The requested export format is not supported.",
            )
        session = self._sessions.get_session(session_id)
        if session is None:
            raise ExportError("session_not_found", "The selected session no longer exists.")
        record = self._reports.get(report_id)
        if record is None or record.session_id != session_id:
            raise ExportError(
                "report_not_found",
                "The selected report no longer exists in this session.",
            )
        try:
            report = ConversationReport.model_validate_json(record.report_json)
        except ValueError as exc:
            raise ExportError(
                "report_invalid",
                "The stored report cannot be exported because its JSON is invalid.",
            ) from exc
        segment_records = self._segments.list_for_session(session_id)
        evidence = self._reports.evidence_for_report(report_id)
        try:
            target = self.export_path(session_id, record.version, format)
        except ValueError as exc:
            raise ExportError(
                "export_path_invalid",
                "The session identifier cannot be used for an export path.",
            ) from exc

        if format == "docx":
            document = _build_document(
                report,
                record,
                session.title,
                segment_records,
                evidence,
            )
            _atomic_save(target, ".docx", lambda path: document.save(str(path)))
        elif format == "html":
            content = _build_html(
                report,
                record,
                session.title,
                segment_records,
                evidence,
            )
            _atomic_save(target, ".html", lambda path: path.write_text(content, encoding="utf-8"))
        elif format == "json":
            content = _build_export_json(report, record, session.title, evidence)
            _atomic_save(
                target,
                ".json",
                lambda path: path.write_text(content, encoding="utf-8"),
            )
        else:
            content = _build_html(
                report,
                record,
                session.title,
                segment_records,
                evidence,
            )
            _atomic_save(target, ".pdf", lambda path: _write_pdf(path, content))

        self._logger.info(
            "report_exported session_id=%s report_id=%s version=%d status=%s format=%s",
            session_id,
            report_id,
            record.version,
            record.status,
            format,
        )
        return ExportResult(
            path=target,
            report_id=record.id,
            version=record.version,
            status=record.status,
            format=format,
        )

    def export_path(self, session_id: str, version: int, format: str = "docx") -> Path:
        if version <= 0:
            raise ValueError("Report version must be positive.")
        if format not in _SUPPORTED_FORMATS:
            raise ValueError("Unsupported export format.")
        directory = session_directory(self._paths.exports_dir, session_id)
        return directory / f"report_v{version:03d}{_FORMAT_SUFFIX[format]}"


def _build_document(
    report: ConversationReport,
    record: ReportRecord,
    session_title: str,
    segments: list[TranscriptSegmentRecord],
    evidence: list[ReportEvidenceRecord],
) -> Document:
    segment_by_id = {segment.id: segment for segment in segments}
    segment_times = {
        segment.id: (segment.start_seconds, segment.end_seconds) for segment in segments
    }
    labels = section_labels(report.report_profile)

    document = Document()
    document.core_properties.title = report.title
    document.core_properties.subject = profile_label(report.report_profile)
    document.core_properties.author = "Audio Report AI"
    document.core_properties.comments = (
        "Generated locally. Verify every claim against the transcript evidence."
    )

    document.add_heading(report.title, level=0)
    document.add_paragraph(f"Session: {session_title}")
    document.add_paragraph(f"Report type: {profile_label(report.report_profile)}")
    document.add_paragraph(f"Session ID: {record.session_id}")
    document.add_paragraph(f"Report version: v{record.version:03d}")
    document.add_paragraph(f"Report ID: {record.id}")
    status = "APPROVED" if record.status == "approved" else "DRAFT - NOT APPROVED"
    status_paragraph = document.add_paragraph()
    status_run = status_paragraph.add_run(f"Status: {status}")
    status_run.bold = True
    document.add_paragraph(f"Created: {record.created_at}")
    if record.approved_at is not None:
        document.add_paragraph(f"Approved: {record.approved_at}")
    if report.date is not None:
        document.add_paragraph(f"Report date: {report.date}")
    if record.status != "approved":
        warning = document.add_paragraph()
        warning_run = warning.add_run(
            "This report is a draft. It must be reviewed and approved by a human before "
            "it is treated as final."
        )
        warning_run.bold = True

    document.add_heading(labels["summary"].capitalize(), level=1)
    document.add_paragraph(report.executive_summary)

    if report.clinical_fields:
        document.add_heading(labels["clinical_fields"].capitalize(), level=1)
        for field in report.clinical_fields:
            _add_claim(
                document,
                f"{field.label}: {field.value}",
                field.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    if report.participants:
        document.add_heading(labels["participants"].capitalize(), level=1)
        for participant in report.participants:
            details = participant.name
            if participant.role:
                details += f" ({participant.role})"
            if participant.speaker_id:
                details += f" - {participant.speaker_id}"
            _add_claim(
                document,
                details,
                participant.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    if report.main_topics:
        document.add_heading(labels["topics"].capitalize(), level=1)
        for topic in report.main_topics:
            _add_claim(
                document,
                f"{topic.title}: {topic.summary}",
                topic.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    if report.important_points:
        document.add_heading(labels["points"].capitalize(), level=1)
        for point in report.important_points:
            _add_claim(
                document,
                point.text,
                point.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    if report.decisions:
        document.add_heading(labels["decisions"].capitalize(), level=1)
        for decision in report.decisions:
            _add_claim(
                document,
                decision.description,
                decision.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    if report.action_items:
        document.add_heading(labels["actions"].capitalize(), level=1)
        for action in report.action_items:
            details = action.description
            details += f" - Responsible: {action.responsible_person or 'Not specified'}"
            details += f"; Deadline: {action.deadline or 'Not specified'}"
            _add_claim(
                document,
                details,
                action.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    if report.unresolved_questions:
        document.add_heading(labels["questions"].capitalize(), level=1)
        for question in report.unresolved_questions:
            _add_claim(
                document,
                question.question,
                question.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    document.add_heading(labels["conclusion"].capitalize(), level=1)
    document.add_paragraph(report.conclusion)

    if report.items_requiring_review:
        document.add_heading(labels["review"].capitalize(), level=1)
        for item in report.items_requiring_review:
            _add_claim(
                document,
                f"{item.description} ({item.reason})",
                item.supporting_segment_ids,
                segment_times,
                style="List Bullet",
            )

    document.add_heading("Evidence", level=1)
    if evidence:
        document.add_paragraph(
            "Evidence links are stored separately from the report text. Segment timestamps "
            "refer to the original audio."
        )
        table = document.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        headers = ("Claim", "Segment", "Timestamp", "Excerpt")
        for index, header in enumerate(headers):
            table.rows[0].cells[index].text = header
        for link in evidence:
            segment = segment_by_id.get(link.transcript_segment_id)
            cells = table.add_row().cells
            cells[0].text = link.claim_path
            cells[1].text = link.transcript_segment_id
            if segment is None:
                cells[2].text = "not found"
                cells[3].text = ""
                continue
            cells[2].text = (
                f"{_format_timestamp(segment.start_seconds)} - "
                f"{_format_timestamp(segment.end_seconds)}"
            )
            text = segment.corrected_text if segment.corrected_text else segment.raw_text
            cells[3].text = _excerpt(text)
    else:
        document.add_paragraph("No evidence links were stored for this report.")

    footer = document.add_paragraph()
    footer_run = footer.add_run(
        "Exported locally by Audio Report AI. Speaker attribution and report content "
        "can be wrong; verify all claims against the audio and transcript."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(9)
    return document


def _build_html(
    report: ConversationReport,
    record: ReportRecord,
    session_title: str,
    segments: list[TranscriptSegmentRecord],
    evidence: list[ReportEvidenceRecord],
) -> str:
    segment_by_id = {segment.id: segment for segment in segments}
    labels = section_labels(report.report_profile)
    status = "APPROVED" if record.status == "approved" else "DRAFT - NOT APPROVED"
    parts: list[str] = [
        "<!doctype html>",
        '<html><head><meta charset="utf-8">',
        f"<title>{_escape(report.title)}</title>",
        "<style>",
        "body{font-family:sans-serif;max-width:900px;margin:2rem auto;line-height:1.45;}",
        "h1{font-size:1.7rem;} .meta{color:#444;} .status{font-weight:bold;}",
        ".draft{color:#9a3412;background:#ffedd5;padding:.6rem;border-radius:4px;}",
        "table{border-collapse:collapse;width:100%;margin-top:.5rem;}",
        "th,td{border:1px solid #999;padding:.35rem;text-align:left;vertical-align:top;}",
        ".evidence{color:#555;font-size:.9rem;}",
        "</style></head><body>",
        f"<h1>{_escape(report.title)}</h1>",
        f'<p class="meta">Session: {_escape(session_title)}<br>'
        f"Report type: {_escape(profile_label(report.report_profile))}<br>"
        f"Session ID: {_escape(record.session_id)}<br>"
        f"Report version: v{record.version:03d}<br>"
        f"Report ID: {_escape(record.id)}<br>"
        f'<span class="status">Status: {_escape(status)}</span><br>'
        f"Created: {_escape(record.created_at)}"
        + (f"<br>Approved: {_escape(record.approved_at)}" if record.approved_at is not None else "")
        + (f"<br>Report date: {_escape(report.date)}" if report.date else "")
        + "</p>",
    ]
    if record.status != "approved":
        parts.append(
            '<p class="draft">This report is a draft. It must be reviewed and approved by a '
            "human before it is treated as final.</p>"
        )

    parts.extend(
        [
            f"<h2>{_escape(labels['summary'].capitalize())}</h2>",
            f"<p>{_escape_multiline(report.executive_summary)}</p>",
        ]
    )

    if report.clinical_fields:
        parts.append(f"<h2>{_escape(labels['clinical_fields'].capitalize())}</h2><ul>")
        for field in report.clinical_fields:
            parts.append(
                "<li>"
                + _escape(f"{field.label}: {field.value}")
                + _html_evidence(field.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    if report.participants:
        parts.append(f"<h2>{_escape(labels['participants'].capitalize())}</h2><ul>")
        for participant in report.participants:
            details = participant.name
            if participant.role:
                details += f" ({participant.role})"
            if participant.speaker_id:
                details += f" - {participant.speaker_id}"
            parts.append(
                "<li>"
                + _escape(details)
                + _html_evidence(participant.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    if report.main_topics:
        parts.append(f"<h2>{_escape(labels['topics'].capitalize())}</h2><ul>")
        for topic in report.main_topics:
            parts.append(
                "<li>"
                + _escape(f"{topic.title}: {topic.summary}")
                + _html_evidence(topic.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    if report.important_points:
        parts.append(f"<h2>{_escape(labels['points'].capitalize())}</h2><ul>")
        for point in report.important_points:
            parts.append(
                "<li>"
                + _escape(point.text)
                + _html_evidence(point.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    if report.decisions:
        parts.append(f"<h2>{_escape(labels['decisions'].capitalize())}</h2><ul>")
        for decision in report.decisions:
            parts.append(
                "<li>"
                + _escape(decision.description)
                + _html_evidence(decision.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    if report.action_items:
        parts.append(f"<h2>{_escape(labels['actions'].capitalize())}</h2><ul>")
        for action in report.action_items:
            details = (
                f"{action.description} - Responsible: "
                f"{action.responsible_person or 'Not specified'}; Deadline: "
                f"{action.deadline or 'Not specified'}"
            )
            parts.append(
                "<li>"
                + _escape(details)
                + _html_evidence(action.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    if report.unresolved_questions:
        parts.append(f"<h2>{_escape(labels['questions'].capitalize())}</h2><ul>")
        for question in report.unresolved_questions:
            parts.append(
                "<li>"
                + _escape(question.question)
                + _html_evidence(question.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    parts.extend(
        [
            f"<h2>{_escape(labels['conclusion'].capitalize())}</h2>",
            f"<p>{_escape_multiline(report.conclusion)}</p>",
        ]
    )

    if report.items_requiring_review:
        parts.append(f"<h2>{_escape(labels['review'].capitalize())}</h2><ul>")
        for item in report.items_requiring_review:
            parts.append(
                "<li>"
                + _escape(f"{item.description} ({item.reason})")
                + _html_evidence(item.supporting_segment_ids, segment_by_id)
                + "</li>"
            )
        parts.append("</ul>")

    parts.append("<h2>Evidence</h2>")
    if evidence:
        parts.append(
            "<p>Evidence links are stored separately from the report text. Segment "
            "timestamps refer to the original audio.</p>"
        )
        parts.append(
            "<table><thead><tr><th>Claim</th><th>Segment</th><th>Timestamp</th>"
            "<th>Excerpt</th></tr></thead><tbody>"
        )
        for link in evidence:
            segment = segment_by_id.get(link.transcript_segment_id)
            if segment is None:
                timestamp = "not found"
                excerpt = ""
            else:
                timestamp = (
                    f"{_format_timestamp(segment.start_seconds)} - "
                    f"{_format_timestamp(segment.end_seconds)}"
                )
                text = segment.corrected_text if segment.corrected_text else segment.raw_text
                excerpt = _excerpt(text)
            parts.append(
                "<tr>"
                f"<td>{_escape(link.claim_path)}</td>"
                f"<td>{_escape(link.transcript_segment_id)}</td>"
                f"<td>{_escape(timestamp)}</td>"
                f"<td>{_escape(excerpt)}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No evidence links were stored for this report.</p>")

    parts.append(
        '<p class="evidence"><em>Exported locally by Audio Report AI. Speaker attribution '
        "and report content can be wrong; verify all claims against the audio and "
        "transcript.</em></p>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


def _build_export_json(
    report: ConversationReport,
    record: ReportRecord,
    session_title: str,
    evidence: list[ReportEvidenceRecord],
) -> str:
    payload = {
        "format": "audio-report-ai-clinical-report",
        "format_version": 1,
        "report_profile": report.report_profile,
        "profile_label": profile_label(report.report_profile),
        "session": {
            "id": record.session_id,
            "title": session_title,
        },
        "report": {
            "id": record.id,
            "version": record.version,
            "schema_name": record.schema_name,
            "schema_version": record.schema_version,
            "status": record.status,
            "created_at": record.created_at,
            "approved_at": record.approved_at,
            "content": report.model_dump(mode="json"),
        },
        "evidence": [
            {
                "claim_path": link.claim_path,
                "transcript_segment_id": link.transcript_segment_id,
                "note": link.note,
            }
            for link in evidence
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _write_pdf(path: Path, content: str) -> None:
    try:
        from PySide6.QtGui import (
            QFont,
            QGuiApplication,
            QPageSize,
            QPdfWriter,
            QTextDocument,
        )
    except ImportError as exc:
        raise ExportError(
            "export_pdf_unavailable",
            "PDF export requires the Qt GUI libraries.",
        ) from exc
    if QGuiApplication.instance() is None:
        raise ExportError(
            "export_pdf_unavailable",
            "PDF export requires a running Qt application.",
        )
    document = QTextDocument()
    document.setDefaultFont(QFont("DejaVu Sans", 10))
    document.setHtml(content)
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setTitle("Audio Report AI export")
    document.print_(writer)
    del writer


def _add_claim(
    document: Document,
    text: str,
    segment_ids: list[str],
    segment_times: dict[str, tuple[float, float]],
    style: str | None = None,
) -> None:
    paragraph = document.add_paragraph(style=style)
    paragraph.add_run(text)
    evidence_run = paragraph.add_run(_evidence_text(segment_ids, segment_times))
    evidence_run.italic = True
    evidence_run.font.size = Pt(9)


def _html_evidence(
    segment_ids: list[str],
    segment_by_id: dict[str, TranscriptSegmentRecord],
) -> str:
    if not segment_ids:
        return ' <span class="evidence">[No evidence]</span>'
    references: list[str] = []
    for segment_id in segment_ids:
        segment = segment_by_id.get(segment_id)
        if segment is None:
            references.append(f"{segment_id} (timestamp unavailable)")
        else:
            references.append(
                f"{segment_id} at {_format_timestamp(segment.start_seconds)}-"
                f"{_format_timestamp(segment.end_seconds)}"
            )
    return ' <span class="evidence">[Evidence: ' + _escape("; ".join(references)) + "]</span>"


def _evidence_text(
    segment_ids: list[str],
    segment_times: dict[str, tuple[float, float]],
) -> str:
    if not segment_ids:
        return "  [No evidence]"
    references: list[str] = []
    for segment_id in segment_ids:
        times = segment_times.get(segment_id)
        if times is None:
            references.append(f"{segment_id} (timestamp unavailable)")
        else:
            references.append(
                f"{segment_id} at {_format_timestamp(times[0])}-{_format_timestamp(times[1])}"
            )
    return "  [Evidence: " + "; ".join(references) + "]"


def _format_timestamp(seconds: float) -> str:
    total_milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _excerpt(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= _EVIDENCE_EXCERPT_LIMIT:
        return compact
    return compact[: _EVIDENCE_EXCERPT_LIMIT - 3].rstrip() + "..."


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _escape_multiline(text: str) -> str:
    return "<br>".join(_escape(line) for line in text.splitlines())


def _atomic_save(target: Path, suffix: str, writer: Callable[[Path], object]) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target.parent, 0o700)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=suffix,
            dir=target.parent,
        )
        os.close(descriptor)
        temporary = Path(name)
    except OSError as exc:
        raise ExportError(
            "export_write_failed",
            "Could not prepare the export directory.",
        ) from exc

    try:
        writer(temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError(
            "export_write_failed",
            "Could not write the export file.",
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
