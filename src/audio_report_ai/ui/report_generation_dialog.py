from __future__ import annotations

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..errors import AppError
from ..services.report_models import (
    REPORT_LANGUAGE_LABELS,
    REPORT_LANGUAGES,
    REPORT_LENGTH_DESCRIPTIONS,
    REPORT_LENGTH_LABELS,
    REPORT_LENGTHS,
    REPORT_MODEL_PRESETS,
    preset_for,
)
from ..services.report_modes import ReportMode, ReportModeStore
from ..services.report_profiles import (
    REPORT_PROFILES,
    profile_description,
    profile_label,
)


class ReportGenerationDialog(QDialog):
    """Choose report options and optional reusable writing instructions."""

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        mode_store: ReportModeStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generate Report")
        self.resize(720, 760)
        self._configured_model = config.report_model
        self._mode_store = mode_store
        self._modes: list[ReportMode] = []

        self._preset = QComboBox(self)
        for preset in REPORT_MODEL_PRESETS:
            self._preset.addItem(f"{preset.name} — {preset.label}", preset.name)
        self._preset.addItem("Custom model name...", None)

        self._model = QLineEdit(self)
        self._model.setPlaceholderText("For example lfm3:1.7b")
        self._description = QLabel(self)
        self._description.setWordWrap(True)
        self._preset.currentIndexChanged.connect(self._on_preset_changed)

        self._profile = QComboBox(self)
        for profile in REPORT_PROFILES:
            self._profile.addItem(profile_label(profile), profile)
        self._select_combo_data(self._profile, config.report_profile)
        self._profile_description = QLabel(self)
        self._profile_description.setWordWrap(True)
        self._profile.currentIndexChanged.connect(self._update_profile_description)
        self._update_profile_description()

        self._language = QComboBox(self)
        for code in REPORT_LANGUAGES:
            self._language.addItem(REPORT_LANGUAGE_LABELS[code], code)
        self._select_combo_data(self._language, config.report_language)

        self._length = QComboBox(self)
        for length in REPORT_LENGTHS:
            self._length.addItem(REPORT_LENGTH_LABELS[length], length)
        self._select_combo_data(self._length, config.report_length)
        self._length_description = QLabel(self)
        self._length_description.setWordWrap(True)
        self._length.currentIndexChanged.connect(self._update_length_description)
        self._update_length_description()

        self._mode = QComboBox(self)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        self._save_mode_button = QPushButton("Save as Mode...", self)
        self._save_mode_button.clicked.connect(self._on_save_mode)
        self._delete_mode_button = QPushButton("Delete", self)
        self._delete_mode_button.clicked.connect(self._on_delete_mode)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._mode, 1)
        mode_row.addWidget(self._save_mode_button)
        mode_row.addWidget(self._delete_mode_button)

        self._instructions = QPlainTextEdit(self)
        self._instructions.setPlaceholderText(
            "Optional. Describe tone, structure, terminology, mandatory sections, or "
            "wording preferences. Example: Keep the report formal, use SOAP headings, "
            "and list owner instructions separately."
        )
        self._instructions.setMaximumHeight(150)
        self._instructions_note = QLabel(
            "Instructions are added to the local prompt as style/format guidance and "
            "cannot override evidence, safety, language, or length requirements. "
            "Saved modes capture the instructions, report type, language, and length; "
            "they are stored only in the app config directory.",
            self,
        )
        self._instructions_note.setWordWrap(True)

        configured_preset = preset_for(config.report_model)
        if configured_preset is None:
            self._preset.setCurrentIndex(self._preset.count() - 1)
            self._model.setText(config.report_model)
            self._model.setEnabled(True)
        else:
            index = self._preset.findData(configured_preset.name)
            if index >= 0:
                self._preset.setCurrentIndex(index)
            self._model.setText(configured_preset.name)
            self._model.setEnabled(False)
        self._update_description()
        self._reload_modes()

        form = QFormLayout()
        form.addRow("Saved mode", mode_row)
        form.addRow("Report type", self._profile)
        form.addRow("", self._profile_description)
        form.addRow("Report model", self._preset)
        form.addRow("Custom name", self._model)
        form.addRow("", self._description)
        form.addRow("Report language", self._language)
        form.addRow("Report length", self._length)
        form.addRow("", self._length_description)
        form.addRow("Writing instructions", self._instructions)
        form.addRow("", self._instructions_note)

        note = QLabel(
            "The model must already be installed in local Ollama. This dialog never "
            "downloads weights. Choose Settings for the default used by the normal "
            "Generate Report command.",
            self,
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        generate_button = QPushButton("Generate Draft", self)
        buttons.addButton(generate_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def selected_model(self) -> str:
        data = self._preset.currentData()
        if isinstance(data, str):
            return data
        return self._model.text().strip()

    def selected_language(self) -> str:
        data = self._language.currentData()
        return data if isinstance(data, str) else "auto"

    def selected_length(self) -> str:
        data = self._length.currentData()
        return data if isinstance(data, str) else "normal"

    def selected_profile(self) -> str:
        data = self._profile.currentData()
        return data if isinstance(data, str) else "conversation"

    def instructions(self) -> str:
        return self._instructions.toPlainText().strip()

    def _select_combo_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _update_length_description(self) -> None:
        self._length_description.setText(
            REPORT_LENGTH_DESCRIPTIONS.get(
                self.selected_length(),
                REPORT_LENGTH_DESCRIPTIONS["normal"],
            )
        )

    def _update_profile_description(self) -> None:
        self._profile_description.setText(profile_description(self.selected_profile()))

    def _on_preset_changed(self, _index: int) -> None:
        data = self._preset.currentData()
        if isinstance(data, str):
            self._model.setText(data)
            self._model.setEnabled(False)
        else:
            self._model.setEnabled(True)
        self._update_description()

    def _update_description(self) -> None:
        model_name = self.selected_model()
        preset = preset_for(model_name)
        if preset is None:
            self._description.setText(
                "Custom Ollama model. Verify that it is installed and supports the "
                "report JSON schema before generating."
            )
            return
        text = (
            f"{preset.download_size}; {preset.memory}; {preset.context}. "
            f"License: {preset.license}. {preset.notes}"
        )
        if preset.requirements:
            text += f" Requirement: {preset.requirements}"
        self._description.setText(text)

    def _reload_modes(self, select: str | None = None) -> None:
        self._mode.blockSignals(True)
        self._mode.clear()
        self._mode.addItem("No saved mode", None)
        if self._mode_store is not None:
            try:
                self._modes = self._mode_store.list_modes()
            except AppError as exc:
                self._modes = []
                QMessageBox.warning(self, "Could not load report modes", exc.message)
            for mode in self._modes:
                self._mode.addItem(mode.name, mode.name)
            if select is not None:
                index = self._mode.findData(select)
                if index >= 0:
                    self._mode.setCurrentIndex(index)
        self._mode.blockSignals(False)
        self._update_mode_buttons()

    def _update_mode_buttons(self) -> None:
        enabled = self._mode_store is not None
        self._save_mode_button.setEnabled(enabled)
        self._delete_mode_button.setEnabled(enabled and isinstance(self._mode.currentData(), str))

    def _on_mode_changed(self, _index: int) -> None:
        mode_name = self._mode.currentData()
        self._update_mode_buttons()
        if not isinstance(mode_name, str):
            return
        mode = next((item for item in self._modes if item.name == mode_name), None)
        if mode is None:
            return
        self._select_combo_data(self._profile, mode.profile)
        self._select_combo_data(self._language, mode.language)
        self._select_combo_data(self._length, mode.length)
        self._instructions.setPlainText(mode.instructions)
        self._update_profile_description()
        self._update_length_description()

    def _on_save_mode(self) -> None:
        if self._mode_store is None:
            return
        instructions = self.instructions()
        if not instructions:
            QMessageBox.warning(
                self,
                "Writing instructions required",
                "Enter writing instructions before saving a mode.",
            )
            return
        current = self._mode.currentData()
        default_name = current if isinstance(current, str) else ""
        name, accepted = QInputDialog.getText(
            self,
            "Save report mode",
            "Mode name:",
            text=default_name,
        )
        if not accepted:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "Mode name required", "Enter a name for the mode.")
            return
        existing = {mode.name for mode in self._modes}
        if name in existing:
            answer = QMessageBox.question(
                self,
                "Overwrite mode",
                f"A mode named '{name}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            mode = ReportMode(
                name=name,
                instructions=instructions,
                profile=self.selected_profile(),
                language=self.selected_language(),
                length=self.selected_length(),
            )
            self._mode_store.save_mode(mode)
        except (AppError, ValidationError) as exc:
            message = exc.message if isinstance(exc, AppError) else "Invalid mode values."
            QMessageBox.warning(self, "Could not save report mode", message)
            return
        self._reload_modes(select=name)
        self._instructions.setPlainText(instructions)

    def _on_delete_mode(self) -> None:
        if self._mode_store is None:
            return
        mode_name = self._mode.currentData()
        if not isinstance(mode_name, str):
            return
        answer = QMessageBox.question(
            self,
            "Delete report mode",
            f"Delete the saved mode '{mode_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self._mode_store.delete_mode(mode_name)
        except AppError as exc:
            QMessageBox.warning(self, "Could not delete report mode", exc.message)
            return
        if deleted:
            self._reload_modes()


__all__ = ["ReportGenerationDialog"]
