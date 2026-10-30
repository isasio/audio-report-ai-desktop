from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FullTranscriptDialog(QDialog):
    """Read, copy, and save the complete transcript as one readable document."""

    def __init__(
        self,
        session_title: str,
        text_builder: Callable[[bool], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Full Transcript - {session_title}")
        self.resize(860, 680)
        self._text_builder = text_builder
        self._session_title = session_title

        self._timestamps = QCheckBox("Show timestamps", self)
        self._timestamps.setChecked(True)
        self._timestamps.toggled.connect(self._refresh)

        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        note = QLabel(
            "Corrected text is shown when available; otherwise the raw transcript is used. "
            "Copy or save this text for offline reading.",
            self,
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        copy_button = QPushButton("Copy", self)
        save_button = QPushButton("Save as Text...", self)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(save_button, QDialogButtonBox.ButtonRole.ActionRole)
        copy_button.clicked.connect(self._copy)
        save_button.clicked.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._timestamps)
        layout.addWidget(self._text)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self._refresh()

    @property
    def transcript_text(self) -> str:
        return self._text.toPlainText()

    def _refresh(self) -> None:
        self._text.setPlainText(self._text_builder(self._timestamps.isChecked()))

    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.transcript_text)

    def _save(self) -> None:
        default_name = _safe_filename(self._session_title) + ".txt"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Full Transcript",
            default_name,
            "Text files (*.txt);;All files (*)",
        )
        if not selected:
            return
        destination = Path(selected).expanduser()
        try:
            _write_text_atomic(destination, self.transcript_text + "\n")
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Could not save transcript",
                f"Could not write the transcript file:\n{destination}\n\n{exc}",
            )
            return
        QMessageBox.information(
            self,
            "Transcript saved",
            f"Saved locally:\n{destination}",
        )


def _safe_filename(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip()).strip("._")
    return cleaned[:80] or "transcript"


def _write_text_atomic(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["FullTranscriptDialog"]
