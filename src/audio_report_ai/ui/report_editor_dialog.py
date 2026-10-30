from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..database.repositories import ReportRecord
from ..schemas.report import ConversationReport


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    label: str
    optional: bool = False
    evidence_ids: bool = False


class ItemTable(QWidget):
    """Editable table for one repeated report section."""

    def __init__(self, columns: Sequence[ColumnSpec], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = tuple(columns)
        self._table = QTableWidget(0, len(columns), self)
        self._table.setHorizontalHeaderLabels([column.label for column in columns])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)

        add_button = QPushButton("Add row", self)
        add_button.clicked.connect(self.add_row)
        remove_button = QPushButton("Remove selected", self)
        remove_button.clicked.connect(self.remove_selected_rows)
        buttons = QHBoxLayout()
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._table)
        layout.addLayout(buttons)

    @property
    def row_count(self) -> int:
        return self._table.rowCount()

    def add_row(self, values: Sequence[str] | None = None) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for column_index, column in enumerate(self._columns):
            value = values[column_index] if values and column_index < len(values) else ""
            item = QTableWidgetItem(value)
            if column.evidence_ids:
                item.setToolTip("Comma-separated transcript segment IDs")
            self._table.setItem(row, column_index, item)

    def remove_selected_rows(self) -> None:
        rows = sorted(
            {index.row() for index in self._table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            self._table.removeRow(row)

    def set_items(self, items: Sequence[object]) -> None:
        self._table.setRowCount(0)
        for item in items:
            values: list[str] = []
            for column in self._columns:
                value = getattr(item, column.key, None)
                if isinstance(value, list):
                    values.append(", ".join(str(entry) for entry in value))
                elif value is None:
                    values.append("")
                else:
                    values.append(str(value))
            self.add_row(values)

    def items(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in range(self._table.rowCount()):
            values: dict[str, Any] = {}
            skip = False
            for column_index, column in enumerate(self._columns):
                item = self._table.item(row, column_index)
                text = item.text().strip() if item is not None else ""
                if column.evidence_ids:
                    values[column.key] = _parse_ids(text)
                    continue
                if column.optional:
                    values[column.key] = text or None
                    continue
                if not text:
                    skip = True
                values[column.key] = text
            if not skip:
                result.append(values)
        return result


class ReportEditorDialog(QDialog):
    """Structured editor for a draft conversation report."""

    def __init__(self, record: ReportRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit report")
        self.resize(900, 700)
        self._record = record
        self._report = ConversationReport.model_validate_json(record.report_json)
        self._validated: ConversationReport | None = None

        self._title = QLineEdit(self._report.title, self)
        self._date = QLineEdit(self._report.date or "", self)
        self._date.setPlaceholderText("Optional, as stated in the transcript")
        self._summary = QPlainTextEdit(self._report.executive_summary, self)
        self._summary.setMinimumHeight(120)
        self._conclusion = QPlainTextEdit(self._report.conclusion, self)
        self._conclusion.setMinimumHeight(100)

        overview = QWidget(self)
        overview_form = QFormLayout(overview)
        overview_form.addRow("Title", self._title)
        overview_form.addRow("Date", self._date)
        overview_form.addRow("Executive summary", self._summary)
        overview_form.addRow("Conclusion", self._conclusion)

        self._tables: dict[str, ItemTable] = {}
        tabs = QTabWidget(self)
        tabs.addTab(overview, "Overview")
        tabs.addTab(
            self._add_table(
                "participants",
                (
                    ColumnSpec("name", "Name"),
                    ColumnSpec("role", "Role", optional=True),
                    ColumnSpec("speaker_id", "Speaker ID", optional=True),
                    ColumnSpec("supporting_segment_ids", "Evidence IDs", evidence_ids=True),
                ),
            ),
            "Participants",
        )
        tabs.addTab(
            self._add_table(
                "main_topics",
                (
                    ColumnSpec("title", "Title"),
                    ColumnSpec("summary", "Summary"),
                    ColumnSpec("supporting_segment_ids", "Evidence IDs", evidence_ids=True),
                ),
            ),
            "Topics",
        )
        tabs.addTab(
            self._add_table(
                "important_points",
                (
                    ColumnSpec("text", "Point"),
                    ColumnSpec("supporting_segment_ids", "Evidence IDs", evidence_ids=True),
                ),
            ),
            "Points",
        )
        tabs.addTab(
            self._add_table(
                "decisions",
                (
                    ColumnSpec("description", "Decision"),
                    ColumnSpec("supporting_segment_ids", "Evidence IDs", evidence_ids=True),
                ),
            ),
            "Decisions",
        )
        tabs.addTab(
            self._add_table(
                "action_items",
                (
                    ColumnSpec("description", "Action"),
                    ColumnSpec("responsible_person", "Responsible", optional=True),
                    ColumnSpec("deadline", "Deadline", optional=True),
                    ColumnSpec("supporting_segment_ids", "Evidence IDs", evidence_ids=True),
                ),
            ),
            "Actions",
        )
        tabs.addTab(
            self._add_table(
                "unresolved_questions",
                (
                    ColumnSpec("question", "Question"),
                    ColumnSpec("supporting_segment_ids", "Evidence IDs", evidence_ids=True),
                ),
            ),
            "Questions",
        )
        tabs.addTab(
            self._add_table(
                "items_requiring_review",
                (
                    ColumnSpec("description", "Item"),
                    ColumnSpec("reason", "Reason"),
                    ColumnSpec("supporting_segment_ids", "Evidence IDs", evidence_ids=True),
                ),
            ),
            "Review flags",
        )

        for field, table in self._tables.items():
            table.set_items(getattr(self._report, field))

        note = QLabel(
            "Evidence IDs refer to transcript segments. Unknown IDs are removed and flagged "
            "when you save; the original raw transcript is never changed.",
            self,
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _add_table(self, field: str, columns: Sequence[ColumnSpec]) -> ItemTable:
        table = ItemTable(columns, self)
        self._tables[field] = table
        return table

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "title": self._title.text().strip(),
            "date": self._date.text().strip() or None,
            "executive_summary": self._summary.toPlainText().strip(),
            "conclusion": self._conclusion.toPlainText().strip(),
        }
        for field, table in self._tables.items():
            payload[field] = table.items()
        return payload

    @property
    def validated_report(self) -> ConversationReport | None:
        return self._validated

    def _on_save(self) -> None:
        try:
            self._validated = ConversationReport.model_validate(self.payload())
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first.get("loc", ())) or "report"
            message = first.get("msg", "Invalid report value.")
            QMessageBox.warning(
                self,
                "Report not saved",
                f"Check the report fields: {location}: {message}",
            )
            return
        self.accept()


def _parse_ids(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for part in text.replace("\n", ",").split(","):
        value = part.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = ["ColumnSpec", "ItemTable", "ReportEditorDialog"]
_ = Qt  # keep the Qt import explicit for tooling that inspects widget imports
