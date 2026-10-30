from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QTextBrowser, QVBoxLayout, QWidget

from ..database.repositories import ReportRecord, TranscriptSegmentRecord
from ..schemas.report import ConversationReport
from ..services.report_profiles import profile_label, section_labels
from .transcript_view import format_timestamp


class ReportView(QWidget):
    version_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._record: ReportRecord | None = None
        self._records: list[ReportRecord] = []
        self._segment_times: dict[str, tuple[float, float]] = {}
        self._header = QLabel("No report yet", self)
        self._history = QComboBox(self)
        self._history.setVisible(False)
        self._history.currentIndexChanged.connect(self._on_history_changed)
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(False)
        self._browser.setReadOnly(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._header)
        layout.addWidget(self._history)
        layout.addWidget(self._browser)

    @property
    def report_record(self) -> ReportRecord | None:
        return self._record

    def report(self) -> ConversationReport | None:
        if self._record is None:
            return None
        return ConversationReport.model_validate_json(self._record.report_json)

    def set_report(
        self,
        record: ReportRecord | None,
        segments: Sequence[TranscriptSegmentRecord] = (),
    ) -> None:
        self.set_reports([record] if record is not None else [], segments)

    def set_reports(
        self,
        records: Sequence[ReportRecord],
        segments: Sequence[TranscriptSegmentRecord] = (),
        preferred_id: str | None = None,
    ) -> None:
        self._records = list(records)
        self._segment_times = {
            segment.id: (segment.start_seconds, segment.end_seconds) for segment in segments
        }
        self._history.blockSignals(True)
        self._history.clear()
        for record in self._records:
            self._history.addItem(_history_label(record), record.id)
        self._history.setVisible(bool(self._records))
        if not self._records:
            self._history.blockSignals(False)
            self._display(None)
            return

        index = 0
        if preferred_id is not None:
            for position, record in enumerate(self._records):
                if record.id == preferred_id:
                    index = position
                    break
        self._history.setCurrentIndex(index)
        self._history.blockSignals(False)
        self._display(self._records[index])

    def _on_history_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._records):
            return
        record = self._records[index]
        self._display(record)
        self.version_changed.emit(record.id)

    def _display(self, record: ReportRecord | None) -> None:
        self._record = record
        if record is None:
            self._header.setText("No report yet")
            self._browser.setPlainText(
                "Generate a report after transcribing and reviewing the transcript."
            )
            return
        try:
            report = ConversationReport.model_validate_json(record.report_json)
        except ValueError:
            self._header.setText("Report unavailable")
            self._browser.setPlainText("The stored report could not be read.")
            return
        status = "Approved" if record.status == "approved" else "Draft"
        self._header.setText(
            f"{profile_label(report.report_profile)} · {status.lower()} v{record.version}"
        )
        self._browser.setPlainText(_format_report(report, record, self._segment_times))


def _history_label(record: ReportRecord) -> str:
    created = record.created_at.replace("T", " ")[:16]
    return f"v{record.version:03d} - {record.status} - {created}"


def _format_report(
    report: ConversationReport,
    record: ReportRecord,
    segment_times: Mapping[str, tuple[float, float]],
) -> str:
    labels = section_labels(report.report_profile)
    lines = [
        report.title,
        f"Type: {profile_label(report.report_profile)}",
        f"Status: {record.status} | Version: {record.version}",
        f"Created: {record.created_at}",
    ]
    if record.approved_at is not None:
        lines.append(f"Approved: {record.approved_at}")
    if report.date is not None:
        lines.append(f"Date: {report.date}")
    lines.extend(["", labels["summary"], report.executive_summary])

    if report.clinical_fields:
        lines.extend(["", labels["clinical_fields"]])
        for field in report.clinical_fields:
            lines.append(
                f"- {field.label}: {field.value}"
                f"{_evidence(field.supporting_segment_ids, segment_times)}"
            )

    if report.participants:
        lines.extend(["", labels["participants"]])
        for participant in report.participants:
            details = participant.name
            if participant.role:
                details += f" - {participant.role}"
            lines.append(
                f"- {details}{_evidence(participant.supporting_segment_ids, segment_times)}"
            )

    if report.main_topics:
        lines.extend(["", labels["topics"]])
        for topic in report.main_topics:
            lines.append(
                f"- {topic.title}: {topic.summary}"
                f"{_evidence(topic.supporting_segment_ids, segment_times)}"
            )

    if report.important_points:
        lines.extend(["", labels["points"]])
        for point in report.important_points:
            lines.append(f"- {point.text}{_evidence(point.supporting_segment_ids, segment_times)}")

    if report.decisions:
        lines.extend(["", labels["decisions"]])
        for decision in report.decisions:
            lines.append(
                f"- {decision.description}"
                f"{_evidence(decision.supporting_segment_ids, segment_times)}"
            )

    if report.action_items:
        lines.extend(["", labels["actions"]])
        for action in report.action_items:
            assignee = action.responsible_person or "Unassigned"
            deadline = action.deadline or "Not specified"
            lines.append(
                f"- {action.description} | {assignee} | {deadline}"
                f"{_evidence(action.supporting_segment_ids, segment_times)}"
            )

    if report.unresolved_questions:
        lines.extend(["", labels["questions"]])
        for question in report.unresolved_questions:
            lines.append(
                f"- {question.question}{_evidence(question.supporting_segment_ids, segment_times)}"
            )

    lines.extend(["", labels["conclusion"], report.conclusion])

    if report.items_requiring_review:
        lines.extend(["", labels["review"]])
        for item in report.items_requiring_review:
            lines.append(
                f"- {item.description} ({item.reason})"
                f"{_evidence(item.supporting_segment_ids, segment_times)}"
            )

    lines.extend(
        [
            "",
            "Evidence references point to transcript segment IDs and timestamps. "
            "Verify all claims before approving this report.",
        ]
    )
    return "\n".join(lines)


def _evidence(
    segment_ids: list[str],
    segment_times: Mapping[str, tuple[float, float]],
) -> str:
    if not segment_ids:
        return " [no evidence]"
    references: list[str] = []
    for segment_id in segment_ids:
        times = segment_times.get(segment_id)
        if times is None:
            references.append(segment_id)
            continue
        references.append(
            f"{segment_id} @ {format_timestamp(times[0])}-{format_timestamp(times[1])}"
        )
    return " [evidence: " + "; ".join(references) + "]"


def format_report_json(record: ReportRecord) -> str:
    return json.dumps(json.loads(record.report_json), indent=2, ensure_ascii=False)
