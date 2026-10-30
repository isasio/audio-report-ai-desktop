from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..database.repositories import TranscriptSegmentRecord
from .transcript_view import TranscriptTableModel, format_timestamp


class TranscriptEditor(QWidget):
    save_requested = Signal(str, str)
    segment_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editable = False

        self._model = TranscriptTableModel()
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        self._raw_view = QPlainTextEdit(self)
        self._raw_view.setReadOnly(True)
        self._raw_view.setPlaceholderText("Select a transcript segment to view its raw text.")

        self._corrected_view = QPlainTextEdit(self)
        self._corrected_view.setPlaceholderText("Select a transcript segment to edit its text.")

        self._status_label = QLabel("No segment selected", self)
        self._revert_button = QPushButton("Use Raw Text", self)
        self._revert_button.clicked.connect(self._on_revert_clicked)
        self._save_button = QPushButton("Save Correction", self)
        self._save_button.clicked.connect(self._on_save_clicked)

        raw_panel = self._text_panel("Raw transcript (read-only)", self._raw_view)
        corrected_panel = self._text_panel("Corrected transcript", self._corrected_view)
        editor_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        editor_splitter.addWidget(raw_panel)
        editor_splitter.addWidget(corrected_panel)
        editor_splitter.setStretchFactor(0, 1)
        editor_splitter.setStretchFactor(1, 1)

        controls = QHBoxLayout()
        controls.addWidget(self._status_label)
        controls.addStretch(1)
        controls.addWidget(self._revert_button)
        controls.addWidget(self._save_button)

        editor_container = QWidget(self)
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(editor_splitter)
        editor_layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self._table)
        splitter.addWidget(editor_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.activated.connect(self._on_save_clicked)
        self._update_controls()

    def row_count(self) -> int:
        return self._model.rowCount()

    def segment_at(self, row: int) -> TranscriptSegmentRecord | None:
        return self._model.segment_at(row)

    def selected_segment(self) -> TranscriptSegmentRecord | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._model.segment_at(rows[0].row())

    def select_row(self, row: int) -> bool:
        if self._model.segment_at(row) is None:
            return False
        self._table.selectRow(row)
        return True

    def current_raw_text(self) -> str:
        return self._raw_view.toPlainText()

    def current_corrected_text(self) -> str:
        return self._corrected_view.toPlainText()

    def set_current_corrected_text(self, text: str) -> None:
        self._corrected_view.setPlainText(text)

    def can_save(self) -> bool:
        return self._editable and self.selected_segment() is not None

    def set_segments(self, segments: list[TranscriptSegmentRecord]) -> None:
        self._model.set_segments(segments)
        self._table.clearSelection()
        self._clear_editor()

    def set_speaker_labels(self, labels: dict[str, str]) -> None:
        self._model.set_speaker_labels(labels)

    def has_speakers(self) -> bool:
        return any(
            bool(segment.speaker_id)
            for row in range(self.row_count())
            if (segment := self._model.segment_at(row)) is not None
        )

    def speaker_label(self, speaker_id: str | None) -> str:
        return self._model.speaker_label(speaker_id)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._update_controls()

    def apply_saved_segment(self, segment: TranscriptSegmentRecord) -> None:
        if not self._model.replace_segment(segment):
            return
        selected = self.selected_segment()
        if selected is None or selected.id != segment.id:
            return
        self._raw_view.setPlainText(segment.raw_text)
        self._corrected_view.setPlainText(segment.corrected_text or segment.raw_text)
        self._update_status(segment)
        self._update_controls()

    def _text_panel(self, title: str, view: QPlainTextEdit) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(title, panel))
        layout.addWidget(view)
        return panel

    def _on_selection_changed(self, *_args: object) -> None:
        segment = self.selected_segment()
        if segment is None:
            self._clear_editor()
            self.segment_selected.emit(None)
            return
        self._raw_view.setPlainText(segment.raw_text)
        self._corrected_view.setPlainText(segment.corrected_text or segment.raw_text)
        self._update_status(segment)
        self._update_controls()
        self.segment_selected.emit(segment)

    def _on_save_clicked(self) -> None:
        if not self.can_save():
            return
        segment = self.selected_segment()
        if segment is not None:
            self.save_requested.emit(segment.id, self._corrected_view.toPlainText())

    def _on_revert_clicked(self) -> None:
        segment = self.selected_segment()
        if segment is not None:
            self._corrected_view.setPlainText(segment.raw_text)

    def _clear_editor(self) -> None:
        self._raw_view.clear()
        self._corrected_view.clear()
        self._status_label.setText("No segment selected")
        self._update_controls()

    def _update_status(self, segment: TranscriptSegmentRecord) -> None:
        self._status_label.setText(
            f"{format_timestamp(segment.start_seconds)} - {segment.review_status.capitalize()}"
        )

    def _update_controls(self) -> None:
        has_segment = self.selected_segment() is not None
        enabled = self._editable and has_segment
        self._corrected_view.setReadOnly(not self._editable)
        self._save_button.setEnabled(enabled)
        self._revert_button.setEnabled(enabled)
        self._save_shortcut.setEnabled(enabled)
