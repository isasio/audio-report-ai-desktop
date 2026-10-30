from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services.speakers import Speaker


class SpeakerRolesDialog(QDialog):
    _HEADERS = ("Speaker", "Segments", "Name", "Role")

    def __init__(
        self,
        speakers: list[Speaker],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Speaker Roles")
        self.resize(620, 360)

        warning = QLabel(
            "Speaker labels are generated automatically and can be wrong. "
            "Names and roles are assigned manually and are not inferred from audio.",
            self,
        )
        warning.setWordWrap(True)

        self._table = QTableWidget(len(speakers), len(self._HEADERS), self)
        self._table.setHorizontalHeaderLabels(self._HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        for row, speaker in enumerate(speakers):
            speaker_item = QTableWidgetItem(speaker.speaker_id)
            speaker_item.setFlags(speaker_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            count_item = QTableWidgetItem(str(speaker.segment_count))
            count_item.setFlags(count_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item = QTableWidgetItem(speaker.display_name or "")
            role_item = QTableWidgetItem(speaker.role or "")
            self._table.setItem(row, 0, speaker_item)
            self._table.setItem(row, 1, count_item)
            self._table.setItem(row, 2, name_item)
            self._table.setItem(row, 3, role_item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(warning)
        layout.addWidget(self._table)
        layout.addWidget(buttons)

    def values(self) -> list[tuple[str, str, str]]:
        values: list[tuple[str, str, str]] = []
        for row in range(self._table.rowCount()):
            speaker_item = self._table.item(row, 0)
            name_item = self._table.item(row, 2)
            role_item = self._table.item(row, 3)
            if speaker_item is None:
                continue
            values.append(
                (
                    speaker_item.text(),
                    name_item.text() if name_item is not None else "",
                    role_item.text() if role_item is not None else "",
                )
            )
        return values

    def set_name(self, speaker_id: str, name: str) -> None:
        self._set_value(speaker_id, 2, name)

    def set_role(self, speaker_id: str, role: str) -> None:
        self._set_value(speaker_id, 3, role)

    def _set_value(self, speaker_id: str, column: int, value: str) -> None:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and item.text() == speaker_id:
                self._table.setItem(row, column, QTableWidgetItem(value))
                return
