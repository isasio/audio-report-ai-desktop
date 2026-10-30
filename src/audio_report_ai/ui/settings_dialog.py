from __future__ import annotations

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..services.report_models import (
    REPORT_LANGUAGE_LABELS,
    REPORT_LANGUAGES,
    REPORT_LENGTH_LABELS,
    REPORT_LENGTHS,
    REPORT_MODEL_PRESETS,
    preset_for,
)
from ..services.report_profiles import REPORT_PROFILES, profile_label


class SettingsDialog(QDialog):
    """Edit the local configuration file without contacting any network service."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(620, 560)
        self._saved: AppConfig | None = None

        self._language = QComboBox(self)
        self._language.addItem("Spanish", "es")
        self._language.addItem("Auto-detect", "auto")
        _select_data(self._language, config.language)

        self._whisper_model = QLineEdit(config.whisper_model, self)
        self._whisper_device = QComboBox(self)
        for label, value in (("Auto", "auto"), ("CPU", "cpu"), ("CUDA", "cuda")):
            self._whisper_device.addItem(label, value)
        _select_data(self._whisper_device, config.whisper_device)

        self._diarization_model = QLineEdit(config.diarization_model, self)
        self._diarization_device = QComboBox(self)
        for label, value in (("Auto", "auto"), ("CPU", "cpu"), ("CUDA", "cuda")):
            self._diarization_device.addItem(label, value)
        _select_data(self._diarization_device, config.diarization_device)

        self._log_level = QComboBox(self)
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self._log_level.addItem(level, level)
        _select_data(self._log_level, config.log_level)

        self._report_preset = QComboBox(self)
        for preset in REPORT_MODEL_PRESETS:
            self._report_preset.addItem(f"{preset.name} - {preset.label}", preset.name)
        self._report_preset.addItem("Custom model name...", None)
        self._report_model = QLineEdit(config.report_model, self)
        self._report_model.setPlaceholderText("For example lfm3:1.7b")
        self._report_description = QLabel(self)
        self._report_description.setWordWrap(True)
        self._report_preset.currentIndexChanged.connect(self._on_report_preset_changed)

        self._report_language = QComboBox(self)
        for code in REPORT_LANGUAGES:
            self._report_language.addItem(REPORT_LANGUAGE_LABELS[code], code)
        _select_data(self._report_language, config.report_language)

        self._report_length = QComboBox(self)
        for length in REPORT_LENGTHS:
            self._report_length.addItem(REPORT_LENGTH_LABELS[length], length)
        _select_data(self._report_length, config.report_length)

        self._report_profile = QComboBox(self)
        for profile in REPORT_PROFILES:
            self._report_profile.addItem(profile_label(profile), profile)
        _select_data(self._report_profile, config.report_profile)

        preset = preset_for(config.report_model)
        if preset is None:
            self._report_preset.setCurrentIndex(self._report_preset.count() - 1)
            self._report_model.setText(config.report_model)
            self._report_model.setEnabled(True)
        else:
            _select_data(self._report_preset, preset.name)
            self._report_model.setText(preset.name)
            self._report_model.setEnabled(False)
        self._update_report_description()

        transcription = QGroupBox("Transcription", self)
        transcription_form = QFormLayout(transcription)
        transcription_form.addRow("Language", self._language)
        transcription_form.addRow("faster-whisper model", self._whisper_model)
        transcription_form.addRow("Device", self._whisper_device)

        diarization = QGroupBox("Speaker diarization (optional)", self)
        diarization_form = QFormLayout(diarization)
        diarization_form.addRow("Model", self._diarization_model)
        diarization_form.addRow("Device", self._diarization_device)

        report = QGroupBox("Report model (local Ollama)", self)
        report_form = QFormLayout(report)
        report_form.addRow("Preset", self._report_preset)
        report_form.addRow("Model name", self._report_model)
        report_form.addRow("", self._report_description)
        report_form.addRow("Report language", self._report_language)
        report_form.addRow("Report length", self._report_length)
        report_form.addRow("Report type", self._report_profile)

        logging_group = QGroupBox("Diagnostics", self)
        logging_form = QFormLayout(logging_group)
        logging_form.addRow("Log level", self._log_level)

        note = QLabel(
            "Settings are stored only in ~/.config/audio-report-ai/config.json. "
            "Restart the application after saving. Model files are never downloaded here.",
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
        layout.addWidget(transcription)
        layout.addWidget(diarization)
        layout.addWidget(report)
        layout.addWidget(logging_group)
        layout.addWidget(note)
        layout.addWidget(buttons)

    @property
    def saved_config(self) -> AppConfig | None:
        return self._saved

    def selected_report_model(self) -> str:
        data = self._report_preset.currentData()
        if isinstance(data, str):
            return data
        return self._report_model.text().strip()

    def _on_report_preset_changed(self, _index: int) -> None:
        data = self._report_preset.currentData()
        if isinstance(data, str):
            self._report_model.setText(data)
            self._report_model.setEnabled(False)
        else:
            self._report_model.setEnabled(True)
        self._update_report_description()

    def _update_report_description(self) -> None:
        preset = preset_for(self.selected_report_model())
        if preset is None:
            self._report_description.setText(
                "Custom Ollama model. Verify that it is installed before generating reports."
            )
            return
        self._report_description.setText(
            f"{preset.download_size}; {preset.memory}; {preset.context}. "
            f"License: {preset.license}. {preset.notes}"
            + (f" Requirement: {preset.requirements}" if preset.requirements else "")
        )

    def _on_save(self) -> None:
        payload = {
            "language": self._language.currentData(),
            "log_level": self._log_level.currentData(),
            "whisper_model": self._whisper_model.text().strip(),
            "whisper_device": self._whisper_device.currentData(),
            "diarization_model": self._diarization_model.text().strip(),
            "diarization_device": self._diarization_device.currentData(),
            "report_model": self.selected_report_model(),
            "report_language": self._report_language.currentData(),
            "report_length": self._report_length.currentData(),
            "report_profile": self._report_profile.currentData(),
        }
        try:
            config = AppConfig.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first.get("loc", ())) or "settings"
            message = first.get("msg", "Invalid value.")
            QMessageBox.warning(
                self,
                "Settings not saved",
                f"Check {location}: {message}",
            )
            return
        self._saved = config
        self.accept()


def _select_data(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


__all__ = ["SettingsDialog"]
