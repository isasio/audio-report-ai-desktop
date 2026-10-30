from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SetupGuideDialog(QDialog):
    """Local setup instructions with no network access."""

    def __init__(self, guide_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Setup and Offline Status")
        self.resize(780, 640)
        self._guide_text = guide_text

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "All processing stays on this machine. Install local models only "
                "when you choose to; runtime never downloads them.",
                self,
            )
        )
        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setPlainText(guide_text)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self._text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        copy_button = QPushButton("Copy commands", self)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        copy_button.clicked.connect(self._copy)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def guide_text(self) -> str:
        return self._guide_text

    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._guide_text)
