from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt

from ..database.repositories import Session


class SessionTableModel(QAbstractTableModel):
    HEADERS = ("Title", "Created (UTC)", "Status")

    def __init__(
        self,
        sessions: list[Session] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._sessions = list(sessions or [])

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._sessions)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        session = self._sessions[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                session.title,
                session.created_at.replace("T", " ")[:16],
                session.status,
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return session.id
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def set_sessions(self, sessions: list[Session]) -> None:
        self.beginResetModel()
        self._sessions = list(sessions)
        self.endResetModel()

    def session_at(self, row: int) -> Session | None:
        if 0 <= row < len(self._sessions):
            return self._sessions[row]
        return None
