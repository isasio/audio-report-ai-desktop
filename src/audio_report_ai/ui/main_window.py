from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import AppConfig, load_config, save_config
from ..database.repositories import ReportRecord, Session, TranscriptSegmentRecord
from ..errors import AppError, AudioImportError, AudioRecordingError, ReportError
from ..paths import AppPaths
from ..services.audio_import import SUPPORTED_AUDIO_EXTENSIONS, AudioImportService
from ..services.audio_recorder import MicrophoneRecorder
from ..services.export_service import ExportResult, ExportService
from ..services.job_coordinator import JobCoordinator
from ..services.model_locator import ModelLocator
from ..services.report_coordinator import ReportCoordinator
from ..services.report_modes import MODE_FILE_NAME, ReportModeStore
from ..services.reports import ReportService
from ..services.runtime_check import ffmpeg_available, format_setup_guide
from ..services.session_deletion import SessionDeletionService
from ..services.sessions import SessionService
from ..services.speakers import Speaker, SpeakerService
from ..services.transcription import TranscriptionService
from .full_transcript_dialog import FullTranscriptDialog
from .playback import PlaybackController
from .report_editor_dialog import ReportEditorDialog
from .report_generation_dialog import ReportGenerationDialog
from .report_view import ReportView
from .session_list import SessionTableModel
from .settings_dialog import SettingsDialog
from .setup_guide import SetupGuideDialog
from .speaker_roles_dialog import SpeakerRolesDialog
from .transcript_editor import TranscriptEditor
from .transcript_view import format_full_transcript, format_timestamp


class MainWindow(QMainWindow):
    def __init__(
        self,
        session_service: SessionService,
        audio_import_service: AudioImportService | None = None,
        transcription_service: TranscriptionService | None = None,
        job_coordinator: JobCoordinator | None = None,
        playback: PlaybackController | None = None,
        speaker_service: SpeakerService | None = None,
        microphone_recorder: MicrophoneRecorder | None = None,
        report_service: ReportService | None = None,
        report_coordinator: ReportCoordinator | None = None,
        export_service: ExportService | None = None,
        session_deletion_service: SessionDeletionService | None = None,
        config: AppConfig | None = None,
        paths: AppPaths | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_service = session_service
        self._audio_import_service = audio_import_service
        self._transcription_service = transcription_service
        self._job_coordinator = job_coordinator
        self._playback = playback
        self._speaker_service = speaker_service
        self._microphone_recorder = microphone_recorder
        self._report_service = report_service
        self._report_coordinator = report_coordinator
        self._export_service = export_service
        self._session_deletion_service = session_deletion_service
        self._config = config
        self._paths = paths
        self._active_session_id: str | None = None
        self._active_operation: str | None = None
        self._recording_timer = QTimer(self)
        self._recording_timer.setInterval(200)
        self._recording_timer.timeout.connect(self._update_recording_status)
        self.setWindowTitle("Audio Report AI")
        self.resize(1100, 800)

        self._model = SessionTableModel()
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self._editor = TranscriptEditor(self)
        self._editor.save_requested.connect(self._on_save_correction)
        self._editor.segment_selected.connect(self._on_transcript_segment_selected)

        self._report_view = ReportView(self)
        self._report_view.version_changed.connect(self._on_report_version_changed)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search session titles and transcripts")
        self._search.setClearButtonEnabled(True)
        self._search.setMaxLength(200)
        self._search.textChanged.connect(self._on_search_changed)

        sessions_panel = QWidget(self)
        sessions_layout = QVBoxLayout(sessions_panel)
        sessions_layout.setContentsMargins(0, 0, 0, 0)
        sessions_layout.addWidget(self._search)
        sessions_layout.addWidget(self._table)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(sessions_panel)
        splitter.addWidget(self._editor)
        splitter.addWidget(self._report_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self._new_action = QAction("&New Session", self)
        self._new_action.setShortcut(QKeySequence.StandardKey.New)
        self._new_action.triggered.connect(self._on_new_session)

        self._import_action = QAction("&Import Audio...", self)
        self._import_action.setShortcut(QKeySequence("Ctrl+I"))
        self._import_action.triggered.connect(self._on_import_audio)
        self._import_action.setEnabled(False)

        self._record_action = QAction("&Record Audio...", self)
        self._record_action.setShortcut(QKeySequence("Ctrl+R"))
        self._record_action.triggered.connect(self._on_record)
        self._record_action.setEnabled(False)

        self._cancel_recording_action = QAction("Cancel &Recording", self)
        self._cancel_recording_action.triggered.connect(self._on_cancel_recording)
        self._cancel_recording_action.setEnabled(False)

        self._transcribe_action = QAction("&Transcribe", self)
        self._transcribe_action.setShortcut(QKeySequence("Ctrl+T"))
        self._transcribe_action.triggered.connect(self._on_transcribe)
        self._transcribe_action.setEnabled(False)

        self._diarize_action = QAction("&Diarize Speakers", self)
        self._diarize_action.setShortcut(QKeySequence("Ctrl+D"))
        self._diarize_action.triggered.connect(self._on_diarize)
        self._diarize_action.setEnabled(False)

        self._cancel_action = QAction("&Cancel Processing", self)
        self._cancel_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._cancel_action.triggered.connect(self._on_cancel)
        self._cancel_action.setEnabled(False)

        self._speaker_roles_action = QAction("Speaker &Roles...", self)
        self._speaker_roles_action.triggered.connect(self._on_edit_speaker_roles)
        self._speaker_roles_action.setEnabled(False)

        self._play_action = QAction("&Play from Selected Segment", self)
        self._play_action.setShortcut(QKeySequence("Ctrl+Space"))
        self._play_action.triggered.connect(self._on_play)
        self._play_action.setEnabled(False)

        self._pause_action = QAction("&Pause Playback", self)
        self._pause_action.triggered.connect(self._on_pause)
        self._pause_action.setEnabled(False)

        self._stop_action = QAction("S&top Playback", self)
        self._stop_action.triggered.connect(self._on_stop)
        self._stop_action.setEnabled(False)

        self._full_transcript_action = QAction("View &Full Transcript...", self)
        self._full_transcript_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self._full_transcript_action.triggered.connect(self.show_full_transcript)
        self._full_transcript_action.setEnabled(False)

        self._copy_transcript_action = QAction("&Copy Full Transcript", self)
        self._copy_transcript_action.triggered.connect(self.copy_full_transcript)
        self._copy_transcript_action.setEnabled(False)

        self._generate_report_action = QAction("&Generate Report", self)
        self._generate_report_action.setShortcut(QKeySequence("Ctrl+G"))
        self._generate_report_action.triggered.connect(self._on_generate_report)
        self._generate_report_action.setEnabled(False)

        self._generate_report_with_model_action = QAction(
            "Generate Report with &Model...",
            self,
        )
        self._generate_report_with_model_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self._generate_report_with_model_action.triggered.connect(
            self._on_generate_report_with_model
        )
        self._generate_report_with_model_action.setEnabled(False)

        self._edit_report_action = QAction("&Edit Report...", self)
        self._edit_report_action.triggered.connect(self._on_edit_report)
        self._edit_report_action.setEnabled(False)

        self._approve_report_action = QAction("&Approve Report", self)
        self._approve_report_action.triggered.connect(self._on_approve_report)
        self._approve_report_action.setEnabled(False)

        self._export_report_action = QAction("&Export DOCX...", self)
        self._export_report_action.setShortcut(QKeySequence("Ctrl+E"))
        self._export_report_action.triggered.connect(lambda: self._on_export_report("docx"))
        self._export_report_action.setEnabled(False)

        self._export_html_action = QAction("Export &HTML...", self)
        self._export_html_action.triggered.connect(lambda: self._on_export_report("html"))
        self._export_html_action.setEnabled(False)

        self._export_pdf_action = QAction("Export &PDF...", self)
        self._export_pdf_action.triggered.connect(lambda: self._on_export_report("pdf"))
        self._export_pdf_action.setEnabled(False)

        self._export_json_action = QAction("Export Structured &JSON...", self)
        self._export_json_action.triggered.connect(lambda: self._on_export_report("json"))
        self._export_json_action.setEnabled(False)

        self._delete_session_action = QAction("&Delete Session...", self)
        self._delete_session_action.triggered.connect(self._on_delete_session)
        self._delete_session_action.setEnabled(False)

        self._settings_action = QAction("&Settings...", self)
        self._settings_action.triggered.connect(self._on_settings)
        self._settings_action.setEnabled(self._paths is not None)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._settings_action)
        file_menu.addSeparator()
        file_menu.addAction(self._new_action)
        file_menu.addAction(self._import_action)
        file_menu.addAction(self._record_action)
        file_menu.addAction(self._cancel_recording_action)
        file_menu.addAction(self._transcribe_action)
        file_menu.addAction(self._diarize_action)
        file_menu.addAction(self._cancel_action)
        file_menu.addSeparator()
        file_menu.addAction(self._delete_session_action)
        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        playback_menu = self.menuBar().addMenu("&Playback")
        playback_menu.addAction(self._play_action)
        playback_menu.addAction(self._pause_action)
        playback_menu.addAction(self._stop_action)

        transcript_menu = self.menuBar().addMenu("&Transcript")
        transcript_menu.addAction(self._full_transcript_action)
        transcript_menu.addAction(self._copy_transcript_action)

        speakers_menu = self.menuBar().addMenu("S&peakers")
        speakers_menu.addAction(self._diarize_action)
        speakers_menu.addAction(self._speaker_roles_action)

        report_menu = self.menuBar().addMenu("&Report")
        report_menu.addAction(self._generate_report_action)
        report_menu.addAction(self._generate_report_with_model_action)
        report_menu.addAction(self._edit_report_action)
        report_menu.addAction(self._approve_report_action)
        report_menu.addAction(self._export_report_action)
        report_menu.addAction(self._export_html_action)
        report_menu.addAction(self._export_pdf_action)
        report_menu.addAction(self._export_json_action)

        self._setup_guide_action = QAction("&Setup and Offline Status...", self)
        self._setup_guide_action.triggered.connect(self.show_setup_guide)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._setup_guide_action)

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addAction(self._new_action)
        toolbar.addAction(self._import_action)
        toolbar.addAction(self._record_action)
        toolbar.addAction(self._transcribe_action)
        toolbar.addAction(self._diarize_action)
        toolbar.addAction(self._generate_report_action)
        toolbar.addAction(self._export_report_action)
        toolbar.addAction(self._cancel_action)
        toolbar.addSeparator()
        toolbar.addAction(self._play_action)
        toolbar.addAction(self._full_transcript_action)

        self._recording_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self._recording_label)

        self._playback_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self._playback_label)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setMaximumWidth(220)
        self._progress.setVisible(False)
        self.statusBar().addPermanentWidget(self._progress)

        if self._playback is not None:
            self._playback.state_changed.connect(self._on_playback_state_changed)
            self._playback.error_occurred.connect(self._on_playback_error)

        if self._job_coordinator is not None:
            self._job_coordinator.started.connect(self._on_processing_started)
            self._job_coordinator.progress.connect(self._on_processing_progress)
            self._job_coordinator.completed.connect(self._on_processing_completed)
            self._job_coordinator.failed.connect(self._on_processing_failed)
            self._job_coordinator.cancelled.connect(self._on_processing_cancelled)

        if self._report_coordinator is not None:
            self._report_coordinator.started.connect(self._on_processing_started)
            self._report_coordinator.progress.connect(self._on_processing_progress)
            self._report_coordinator.completed.connect(self._on_report_completed)
            self._report_coordinator.failed.connect(self._on_processing_failed)
            self._report_coordinator.cancelled.connect(self._on_processing_cancelled)

        self.refresh_sessions()
        self._show_setup_notice()

    def refresh_sessions(self) -> None:
        selected = self.selected_session()
        query = self.search_query()
        sessions = (
            self._session_service.search_sessions(query)
            if query
            else self._session_service.list_sessions()
        )
        self._model.set_sessions(sessions)
        if selected is not None and not self._select_session_id(selected.id):
            self._table.clearSelection()
        if query:
            self.statusBar().showMessage(f"{len(sessions)} matching session(s)")
        else:
            self.statusBar().showMessage(f"{len(sessions)} session(s)")
        self._update_actions()

    def search_query(self) -> str:
        return self._search.text().strip()

    def set_search_query(self, text: str) -> None:
        self._search.setText(text)

    def _on_search_changed(self, _text: str) -> None:
        self.refresh_sessions()

    def _select_session_id(self, session_id: str) -> bool:
        for row in range(self.session_count()):
            session = self.session_at(row)
            if session is not None and session.id == session_id:
                self._table.selectRow(row)
                return True
        return False

    def create_session(self, title: str) -> Session:
        session = self._session_service.create_session(title)
        self.refresh_sessions()
        return session

    def import_audio_for_session(self, session_id: str, source_path: str | Path) -> Session:
        if self._audio_import_service is None:
            raise AudioImportError(
                "audio_import_unavailable",
                "Audio import is not available in this window.",
            )
        session = self._audio_import_service.import_audio(session_id, source_path)
        self.refresh_sessions()
        duration = session.duration_seconds or 0.0
        self.statusBar().showMessage(f"Imported audio ({duration:.1f} s)")
        return session

    def start_recording_for_session(self, session_id: str) -> None:
        if self._microphone_recorder is None:
            raise AudioRecordingError(
                "recording_unavailable",
                "Microphone recording is not available in this window.",
            )
        self._stop_playback_if_active()
        self._microphone_recorder.start(session_id)
        self._recording_timer.start()
        self._update_recording_status()
        self.statusBar().showMessage("Recording audio. Press Ctrl+R to stop.")
        self._update_actions()

    def stop_recording(self) -> Session:
        if self._microphone_recorder is None:
            raise AudioRecordingError(
                "recording_unavailable",
                "Microphone recording is not available in this window.",
            )
        try:
            session = self._microphone_recorder.stop()
        finally:
            self._recording_timer.stop()
            self._recording_label.clear()
            self.refresh_sessions()
            self._update_actions()
        duration = session.duration_seconds or 0.0
        self.statusBar().showMessage(f"Recorded audio ({duration:.1f} s)")
        return session

    def cancel_recording(self) -> bool:
        if self._microphone_recorder is None or not self.is_recording():
            return False
        try:
            self._microphone_recorder.cancel()
        finally:
            self._recording_timer.stop()
            self._recording_label.clear()
            self.refresh_sessions()
            self._update_actions()
        self.statusBar().showMessage("Recording discarded")
        return True

    def is_recording(self) -> bool:
        return self._microphone_recorder is not None and self._microphone_recorder.is_recording

    def recording_elapsed_seconds(self) -> float:
        if self._microphone_recorder is None:
            return 0.0
        return self._microphone_recorder.elapsed_seconds

    def can_record(self) -> bool:
        return self._record_action.isEnabled() and not self.is_recording()

    def can_cancel_recording(self) -> bool:
        return self._cancel_recording_action.isEnabled()

    def transcribe_session(self, session_id: str) -> str:
        if self._job_coordinator is None:
            raise AppError(
                "transcription_unavailable",
                "Transcription is not available in this window.",
            )
        previous_session_id = self._active_session_id
        previous_operation = self._active_operation
        self._active_session_id = session_id
        self._active_operation = "transcription"
        try:
            return self._job_coordinator.start_transcription(session_id)
        except AppError:
            self._active_session_id = previous_session_id
            self._active_operation = previous_operation
            raise

    def diarize_session(self, session_id: str) -> str:
        if self._job_coordinator is None or not self._job_coordinator.supports_diarization:
            raise AppError(
                "diarization_unavailable",
                "Speaker diarization is not available in this window.",
            )
        previous_session_id = self._active_session_id
        previous_operation = self._active_operation
        self._active_session_id = session_id
        self._active_operation = "diarization"
        try:
            return self._job_coordinator.start_diarization(session_id)
        except AppError:
            self._active_session_id = previous_session_id
            self._active_operation = previous_operation
            raise

    def generate_report(
        self,
        session_id: str,
        model_name: str | None = None,
        language: str | None = None,
        length: str | None = None,
        profile: str | None = None,
        custom_instructions: str | None = None,
    ) -> str:
        if self._report_coordinator is None or self._report_service is None:
            raise ReportError(
                "report_unavailable",
                "Report generation is not available in this window.",
            )
        previous_session_id = self._active_session_id
        previous_operation = self._active_operation
        self._active_session_id = session_id
        self._active_operation = "report"
        try:
            return self._report_coordinator.start(
                session_id,
                model_name=model_name,
                language=language,
                length=length,
                profile=profile,
                custom_instructions=custom_instructions,
            )
        except AppError:
            self._active_session_id = previous_session_id
            self._active_operation = previous_operation
            raise

    def load_report_for_session(self, session_id: str, report_id: str | None = None) -> None:
        if self._report_service is None:
            self._report_view.set_reports([])
            return
        segments = (
            self._transcription_service.list_segments(session_id)
            if self._transcription_service is not None
            else []
        )
        preferred_id = report_id
        if preferred_id is None:
            current = self._report_view.report_record
            if current is not None and current.session_id == session_id:
                preferred_id = current.id
        self._report_view.set_reports(
            self._report_service.list_reports(session_id),
            segments,
            preferred_id=preferred_id,
        )

    def selected_report(self) -> ReportRecord | None:
        return self._report_view.report_record

    def save_report_payload(self, report_id: str, payload: object) -> ReportRecord:
        if self._report_service is None:
            raise ReportError(
                "report_unavailable",
                "Report editing is not available in this window.",
            )
        if not isinstance(payload, dict):
            raise ReportError(
                "report_json_invalid",
                "The report payload must be an object.",
            )
        record = self._report_service.update_draft_report(report_id, payload)
        self.refresh_sessions()
        self.load_report_for_session(record.session_id, record.id)
        self.statusBar().showMessage(f"Report v{record.version} saved")
        return record

    def save_report_json(self, report_id: str, text: str) -> ReportRecord:
        if self._report_service is None:
            raise ReportError(
                "report_unavailable",
                "Report editing is not available in this window.",
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReportError(
                "report_json_invalid",
                "The report JSON is not valid.",
            ) from exc
        if not isinstance(payload, dict):
            raise ReportError(
                "report_json_invalid",
                "The report JSON must contain an object.",
            )
        return self.save_report_payload(report_id, payload)

    def approve_report(self, report_id: str) -> ReportRecord:
        if self._report_service is None:
            raise ReportError(
                "report_unavailable",
                "Report approval is not available in this window.",
            )
        record = self._report_service.approve_report(report_id)
        self.refresh_sessions()
        self.load_report_for_session(record.session_id, record.id)
        self.statusBar().showMessage(f"Report v{record.version} approved")
        return record

    def export_report(
        self,
        session_id: str,
        report_id: str,
        format: str = "docx",
    ) -> ExportResult:
        if self._export_service is None:
            raise AppError(
                "export_unavailable",
                "Document export is not available in this window.",
            )
        result = self._export_service.export_report(session_id, report_id, format)
        self.statusBar().showMessage(
            f"Report v{result.version} exported as {result.format} to {result.path}"
        )
        return result

    def delete_session(self, session_id: str) -> None:
        if self._session_deletion_service is None:
            raise AppError(
                "session_delete_unavailable",
                "Session deletion is not available in this window.",
            )
        selected_id = self.selected_session_id()
        self._session_deletion_service.delete_session(session_id)
        if selected_id == session_id:
            self._editor.set_segments([])
            self._report_view.set_reports([])
            self._table.clearSelection()
        self.refresh_sessions()
        self.statusBar().showMessage("Session and all local data deleted")

    def can_generate_report(self) -> bool:
        return self._generate_report_action.isEnabled()

    def load_transcript_for_session(self, session_id: str) -> None:
        if self._transcription_service is None:
            self._editor.set_segments([])
            return
        self._editor.set_segments(self._transcription_service.list_segments(session_id))
        self._refresh_speaker_labels(session_id)

    def full_transcript_text(self, include_timestamps: bool = True) -> str:
        session = self.selected_session()
        if session is None or self._transcription_service is None:
            return ""
        segments = self._transcription_service.list_segments(session.id)
        labels: dict[str, str] = {}
        if self._speaker_service is not None:
            try:
                labels = self._speaker_service.labels_for_session(session.id)
            except AppError:
                labels = {}
        return format_full_transcript(segments, labels, include_timestamps)

    def show_full_transcript(self) -> bool:
        session = self.selected_session()
        text = self.full_transcript_text(include_timestamps=True)
        if session is None or not text:
            self.statusBar().showMessage("No transcript is available for this session.")
            return False
        segments = (
            self._transcription_service.list_segments(session.id)
            if self._transcription_service is not None
            else []
        )
        labels: dict[str, str] = {}
        if self._speaker_service is not None:
            try:
                labels = self._speaker_service.labels_for_session(session.id)
            except AppError:
                labels = {}
        dialog = FullTranscriptDialog(
            session.title,
            lambda show_timestamps: format_full_transcript(
                segments,
                labels,
                show_timestamps,
            ),
            self,
        )
        dialog.exec()
        return True

    def copy_full_transcript(self) -> bool:
        text = self.full_transcript_text(include_timestamps=True)
        if not text:
            self.statusBar().showMessage("No transcript is available for this session.")
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.statusBar().showMessage("Full transcript copied")
        return True

    def _refresh_speaker_labels(self, session_id: str) -> None:
        labels: dict[str, str] = {}
        if self._speaker_service is not None:
            try:
                labels = self._speaker_service.labels_for_session(session_id)
            except AppError:
                labels = {}
        self._editor.set_speaker_labels(labels)

    def session_count(self) -> int:
        return self._model.rowCount()

    def session_at(self, row: int) -> Session | None:
        return self._model.session_at(row)

    def transcript_count(self) -> int:
        return self._editor.row_count()

    def transcript_at(self, row: int) -> TranscriptSegmentRecord | None:
        return self._editor.segment_at(row)

    def select_transcript_row(self, row: int) -> bool:
        return self._editor.select_row(row)

    def selected_transcript_segment(self) -> TranscriptSegmentRecord | None:
        return self._editor.selected_segment()

    def current_raw_text(self) -> str:
        return self._editor.current_raw_text()

    def current_corrected_text(self) -> str:
        return self._editor.current_corrected_text()

    def set_corrected_text(self, text: str) -> None:
        self._editor.set_current_corrected_text(text)

    def can_save_correction(self) -> bool:
        return self._editor.can_save()

    def save_selected_correction(self) -> TranscriptSegmentRecord | None:
        segment = self._editor.selected_segment()
        if segment is None:
            return None
        return self._on_save_correction(segment.id, self._editor.current_corrected_text())

    def can_play(self) -> bool:
        return self._play_action.isEnabled()

    def play_selected_segment(self) -> bool:
        session = self.selected_session()
        path = self._playback_path(session)
        if self._playback is None or path is None:
            self.statusBar().showMessage("Audio playback is not available for this session.")
            return False
        segment = self._editor.selected_segment()
        start_seconds = segment.start_seconds if segment is not None else 0.0
        self._playback.load(path)
        self._playback.play_from(start_seconds)
        self._playback_label.setText(f"Playing from {format_timestamp(start_seconds)}")
        self.statusBar().showMessage("Playing audio")
        return True

    def pause_playback(self) -> bool:
        if self._playback is None:
            return False
        self._playback.pause()
        self.statusBar().showMessage("Playback paused")
        return True

    def stop_playback(self) -> bool:
        if self._playback is None:
            return False
        self._playback.stop()
        self.statusBar().showMessage("Playback stopped")
        return True

    def playback_position_seconds(self) -> float | None:
        if self._playback is None:
            return None
        return self._playback.position_seconds

    def speakers(self) -> list[Speaker]:
        session = self.selected_session()
        if session is None or self._speaker_service is None:
            return []
        return self._speaker_service.list_speakers(session.id)

    def speaker_count(self) -> int:
        return len(self.speakers())

    def speaker_at(self, row: int) -> Speaker | None:
        speakers = self.speakers()
        if 0 <= row < len(speakers):
            return speakers[row]
        return None

    def set_speaker_role(
        self,
        speaker_id: str,
        display_name: str | None,
        role: str | None,
    ) -> Speaker | None:
        session = self.selected_session()
        if session is None or self._speaker_service is None:
            return None
        try:
            updated = self._speaker_service.set_role(
                session.id,
                speaker_id,
                display_name,
                role,
            )
        except AppError as exc:
            QMessageBox.warning(self, "Could not save speaker role", exc.message)
            return None
        self.load_transcript_for_session(session.id)
        self.statusBar().showMessage("Speaker role saved")
        self._update_actions()
        return updated

    def save_speaker_roles(self, values: list[tuple[str, str, str]]) -> bool:
        session = self.selected_session()
        if session is None or self._speaker_service is None:
            return False
        try:
            for speaker_id, display_name, role in values:
                self._speaker_service.set_role(
                    session.id,
                    speaker_id,
                    display_name,
                    role,
                )
        except AppError as exc:
            QMessageBox.warning(self, "Could not save speaker roles", exc.message)
            return False
        self.load_transcript_for_session(session.id)
        self.statusBar().showMessage("Speaker roles saved")
        self._update_actions()
        return True

    def edit_speaker_roles(self) -> bool:
        session = self.selected_session()
        if session is None or self._speaker_service is None:
            return False
        speakers = self._speaker_service.list_speakers(session.id)
        if not speakers:
            self.statusBar().showMessage("Run speaker diarization before assigning roles.")
            return False
        dialog = SpeakerRolesDialog(speakers, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        return self.save_speaker_roles(dialog.values())

    def can_diarize(self) -> bool:
        return self._diarize_action.isEnabled()

    def can_edit_speaker_roles(self) -> bool:
        return self._speaker_roles_action.isEnabled()

    def can_transcribe(self) -> bool:
        return self._transcribe_action.isEnabled()

    def setup_guide_text(self) -> str:
        if self._config is None or self._paths is None:
            return (
                "Setup information is unavailable in this window.\n"
                "Run: uv run python -m audio_report_ai --setup-guide"
            )
        return format_setup_guide(self._config, self._paths, __version__)

    def show_setup_guide(self) -> bool:
        dialog = SetupGuideDialog(self.setup_guide_text(), self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _show_setup_notice(self) -> None:
        if self._config is None or self._paths is None:
            return
        missing: list[str] = []
        if not ffmpeg_available():
            missing.append("FFmpeg/FFprobe")
        try:
            model_ready = ModelLocator(
                self._paths,
                self._config.whisper_model,
            ).is_available()
        except AppError:
            model_ready = False
        if not model_ready:
            missing.append(f"speech model '{self._config.whisper_model}'")
        if missing:
            self.statusBar().showMessage(
                "Setup needed: " + ", ".join(missing) + ". Open Help > Setup and Offline Status.",
                15000,
            )

    def is_processing(self) -> bool:
        return self._progress.isVisible()

    def select_session_row(self, row: int) -> None:
        self._table.selectRow(row)

    def selected_session(self) -> Session | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._model.session_at(rows[0].row())

    def selected_session_id(self) -> str | None:
        session = self.selected_session()
        return session.id if session is not None else None

    def _is_busy(self) -> bool:
        return (self._job_coordinator is not None and self._job_coordinator.is_running) or (
            self._report_coordinator is not None and self._report_coordinator.is_running
        )

    def _update_actions(self) -> None:
        session = self.selected_session()
        busy = self._is_busy()
        recording = self.is_recording()
        self._new_action.setEnabled(not recording)
        self._import_action.setEnabled(
            self._audio_import_service is not None
            and session is not None
            and not busy
            and not recording
        )
        self._record_action.setText("&Stop Recording" if recording else "&Record Audio...")
        self._record_action.setEnabled(
            recording
            or (
                self._microphone_recorder is not None
                and session is not None
                and not session.original_audio_path
                and not busy
            )
        )
        self._cancel_recording_action.setEnabled(recording)
        self._table.setEnabled(not recording)
        self._search.setEnabled(not recording)
        self._transcribe_action.setEnabled(
            self._job_coordinator is not None
            and session is not None
            and bool(session.original_audio_path)
            and not busy
            and not recording
        )
        self._cancel_action.setEnabled(busy)
        can_play = (
            self._playback is not None
            and self._playback_path(session) is not None
            and not busy
            and not recording
        )
        self._play_action.setEnabled(can_play)
        self._pause_action.setEnabled(can_play)
        self._stop_action.setEnabled(can_play)
        can_read_transcript = (
            self._transcription_service is not None
            and session is not None
            and self._editor.row_count() > 0
            and not recording
        )
        self._full_transcript_action.setEnabled(can_read_transcript)
        self._copy_transcript_action.setEnabled(can_read_transcript)
        self._diarize_action.setEnabled(
            self._job_coordinator is not None
            and self._job_coordinator.supports_diarization
            and session is not None
            and self._editor.row_count() > 0
            and not busy
            and not recording
        )
        self._speaker_roles_action.setEnabled(
            self._speaker_service is not None
            and session is not None
            and self._editor.has_speakers()
            and not busy
            and not recording
        )
        record = self._report_view.report_record
        draft_selected = (
            record is not None
            and record.status == "draft"
            and session is not None
            and record.session_id == session.id
            and not busy
            and not recording
        )
        self._generate_report_action.setEnabled(
            self._report_coordinator is not None
            and self._report_service is not None
            and session is not None
            and self._editor.row_count() > 0
            and not busy
            and not recording
        )
        self._generate_report_with_model_action.setEnabled(
            self._report_coordinator is not None
            and self._report_service is not None
            and session is not None
            and self._editor.row_count() > 0
            and not busy
            and not recording
        )
        self._edit_report_action.setEnabled(draft_selected)
        self._approve_report_action.setEnabled(draft_selected)
        self._export_report_action.setEnabled(
            self._export_service is not None
            and record is not None
            and session is not None
            and record.session_id == session.id
            and not busy
            and not recording
        )
        self._export_html_action.setEnabled(self._export_report_action.isEnabled())
        self._export_pdf_action.setEnabled(self._export_report_action.isEnabled())
        self._export_json_action.setEnabled(self._export_report_action.isEnabled())
        self._delete_session_action.setEnabled(
            self._session_deletion_service is not None
            and session is not None
            and not busy
            and not recording
        )
        self._editor.set_editable(
            self._transcription_service is not None
            and session is not None
            and not busy
            and not recording
        )

    def _update_recording_status(self) -> None:
        if not self.is_recording():
            self._recording_label.clear()
            return
        self._recording_label.setText(
            f"Recording {format_timestamp(self.recording_elapsed_seconds())}"
        )

    def _on_selection_changed(self, *_args: object) -> None:
        session = self.selected_session()
        self._stop_playback_if_active()
        self._playback_label.clear()
        if session is None:
            self._editor.set_segments([])
            self._report_view.set_report(None)
        else:
            self.load_transcript_for_session(session.id)
            self.load_report_for_session(session.id)
        self._update_actions()

    def _on_transcript_segment_selected(self, segment: object) -> None:
        if not isinstance(segment, TranscriptSegmentRecord):
            return
        session = self.selected_session()
        path = self._playback_path(session)
        if self._playback is None or path is None:
            return
        self._playback.load(path)
        self._playback.seek(segment.start_seconds)
        self._playback_label.setText(f"At {format_timestamp(segment.start_seconds)}")

    def _on_save_correction(self, segment_id: str, text: str) -> TranscriptSegmentRecord | None:
        session = self.selected_session()
        if session is None or self._transcription_service is None:
            return None
        try:
            updated = self._transcription_service.save_correction(
                session.id,
                segment_id,
                text,
            )
        except AppError as exc:
            QMessageBox.warning(self, "Could not save correction", exc.message)
            return None
        self._editor.apply_saved_segment(updated)
        self.statusBar().showMessage(
            f"Correction saved at {format_timestamp(updated.start_seconds)}"
        )
        return updated

    def _on_play(self) -> None:
        self.play_selected_segment()

    def _on_pause(self) -> None:
        self.pause_playback()

    def _on_stop(self) -> None:
        self.stop_playback()

    def _on_playback_state_changed(self, state: str) -> None:
        messages = {
            "playing": "Playing audio",
            "paused": "Playback paused",
            "stopped": "Playback stopped",
        }
        message = messages.get(state)
        if message is not None:
            self.statusBar().showMessage(message)

    def _on_playback_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Playback error: {message}")

    def _stop_playback_if_active(self) -> None:
        if self._playback is None:
            return
        if self._playback.is_playing or self._playback.position_seconds > 0:
            self._playback.stop()

    def _playback_path(self, session: Session | None) -> Path | None:
        if session is None or not session.original_audio_path:
            return None
        path = Path(session.original_audio_path)
        return path if path.is_file() else None

    def _on_new_session(self) -> None:
        title, accepted = QInputDialog.getText(self, "New Session", "Session title:")
        if not accepted:
            return
        try:
            self.create_session(title)
        except AppError as exc:
            QMessageBox.warning(self, "Could not create session", exc.message)

    def _on_record(self) -> None:
        if self.is_recording():
            try:
                self.stop_recording()
            except AppError as exc:
                QMessageBox.warning(self, "Could not finish recording", exc.message)
            return
        session = self.selected_session()
        if session is None:
            return
        try:
            self.start_recording_for_session(session.id)
        except AppError as exc:
            QMessageBox.warning(self, "Could not start recording", exc.message)

    def _on_cancel_recording(self) -> None:
        self.cancel_recording()

    def _on_generate_report(self) -> None:
        session = self.selected_session()
        if session is None:
            return
        try:
            self.generate_report(session.id)
        except AppError as exc:
            QMessageBox.warning(self, "Could not generate report", exc.message)

    def _on_generate_report_with_model(self) -> None:
        session = self.selected_session()
        if session is None:
            return
        if self._paths is None:
            QMessageBox.warning(
                self,
                "Report model unavailable",
                "The configuration path is not available.",
            )
            return
        try:
            config = self._config or load_config(self._paths)
        except AppError as exc:
            QMessageBox.warning(self, "Could not load report settings", exc.message)
            return
        dialog = ReportGenerationDialog(
            config,
            self,
            mode_store=ReportModeStore(self._paths.config_dir / MODE_FILE_NAME),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        model_name = dialog.selected_model()
        if not model_name:
            QMessageBox.warning(
                self,
                "Report model missing",
                "Choose a preset or enter a local Ollama model name.",
            )
            return
        try:
            self.generate_report(
                session.id,
                model_name=model_name,
                language=dialog.selected_language(),
                length=dialog.selected_length(),
                profile=dialog.selected_profile(),
                custom_instructions=dialog.instructions(),
            )
        except AppError as exc:
            QMessageBox.warning(self, "Could not generate report", exc.message)

    def _on_edit_report(self) -> None:
        record = self.selected_report()
        if record is None:
            return
        try:
            dialog = ReportEditorDialog(record, self)
        except ValueError:
            QMessageBox.warning(
                self,
                "Could not edit report",
                "The stored report JSON is invalid and cannot be edited.",
            )
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.save_report_payload(record.id, dialog.payload())
        except AppError as exc:
            QMessageBox.warning(self, "Could not save report", exc.message)

    def _on_report_version_changed(self, _report_id: str) -> None:
        record = self._report_view.report_record
        if record is not None:
            self.statusBar().showMessage(f"Viewing report v{record.version} ({record.status})")
        self._update_actions()

    def _on_approve_report(self) -> None:
        record = self.selected_report()
        if record is None:
            return
        answer = QMessageBox.question(
            self,
            "Approve report",
            (
                f"Approve report v{record.version}?\n\n"
                "Approved reports cannot be edited. Verify every claim and evidence link first."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.approve_report(record.id)
        except AppError as exc:
            QMessageBox.warning(self, "Could not approve report", exc.message)

    def _on_export_report(self, format: str = "docx") -> None:
        record = self.selected_report()
        session = self.selected_session()
        if record is None or session is None:
            return
        try:
            result = self.export_report(session.id, record.id, format)
        except AppError as exc:
            QMessageBox.warning(self, "Could not export report", exc.message)
            return
        QMessageBox.information(
            self,
            "Report exported",
            f"Saved locally ({result.format}):\n{result.path}",
        )

    def _on_delete_session(self) -> None:
        session = self.selected_session()
        if session is None:
            return
        answer = QMessageBox.warning(
            self,
            "Delete session",
            (
                f"Permanently delete session '{session.title}'?\n\n"
                "This removes the original and normalised audio, raw and corrected "
                "transcripts, speaker labels, reports, exports, and job records for this "
                "session. This cannot be undone."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.delete_session(session.id)
        except AppError as exc:
            QMessageBox.warning(self, "Could not delete session", exc.message)

    def _on_settings(self) -> None:
        if self._paths is None:
            QMessageBox.warning(
                self,
                "Settings unavailable",
                "Settings cannot be edited because the configuration path is not available.",
            )
            return
        try:
            current = self._config or load_config(self._paths)
        except AppError as exc:
            QMessageBox.warning(self, "Could not load settings", exc.message)
            return
        dialog = SettingsDialog(current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        saved = dialog.saved_config
        if saved is None:
            return
        try:
            save_config(saved, self._paths)
        except AppError as exc:
            QMessageBox.warning(self, "Could not save settings", exc.message)
            return
        self._config = saved
        QMessageBox.information(
            self,
            "Settings saved",
            "Settings were saved locally. Restart Audio Report AI to apply them.",
        )

    def _on_import_audio(self) -> None:
        session = self.selected_session()
        if session is None:
            return
        extensions = " ".join(f"*{extension}" for extension in sorted(SUPPORTED_AUDIO_EXTENSIONS))
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Audio",
            "",
            f"Audio files ({extensions})",
        )
        if not source_path:
            return
        try:
            self.import_audio_for_session(session.id, source_path)
        except AppError as exc:
            QMessageBox.warning(self, "Could not import audio", exc.message)

    def _on_transcribe(self) -> None:
        session = self.selected_session()
        if session is None:
            return
        try:
            self.transcribe_session(session.id)
        except AppError as exc:
            QMessageBox.warning(self, "Could not start transcription", exc.message)

    def _on_diarize(self) -> None:
        session = self.selected_session()
        if session is None:
            return
        try:
            self.diarize_session(session.id)
        except AppError as exc:
            QMessageBox.warning(self, "Could not start diarization", exc.message)

    def _on_edit_speaker_roles(self) -> None:
        self.edit_speaker_roles()

    def _on_cancel(self) -> None:
        if self._report_coordinator is not None and self._report_coordinator.is_running:
            self._report_coordinator.cancel()
        elif self._job_coordinator is not None:
            self._job_coordinator.cancel()

    def _operation(self) -> str:
        return self._active_operation or "transcription"

    def _on_processing_started(self, _job_id: str) -> None:
        self._stop_playback_if_active()
        self._progress.setValue(0)
        self._progress.setVisible(True)
        operation = self._operation()
        self.statusBar().showMessage(f"Starting {_operation_label(operation).lower()}")
        self._update_actions()

    def _on_processing_progress(self, _job_id: str, progress: float, message: str) -> None:
        self._progress.setValue(max(0, min(100, int(round(progress * 100)))))
        self.statusBar().showMessage(message)

    def _on_processing_completed(self, _job_id: str) -> None:
        self._progress.setVisible(False)
        operation = self._operation()
        session_id = self._active_session_id
        self._active_session_id = None
        self._active_operation = None
        self.refresh_sessions()
        if session_id is not None:
            self.load_transcript_for_session(session_id)
            self.load_report_for_session(session_id)
        self.statusBar().showMessage(f"{_operation_label(operation)} complete")
        self._update_actions()

    def _on_report_completed(self, job_id: str, report_id: str) -> None:
        session_id = self._active_session_id
        self._on_processing_completed(job_id)
        if session_id is not None:
            self.load_report_for_session(session_id, report_id)
            self._update_actions()

    def _on_processing_failed(self, _job_id: str, _code: str, message: str) -> None:
        self._progress.setVisible(False)
        operation = self._operation()
        self._active_session_id = None
        self._active_operation = None
        self.refresh_sessions()
        if operation == "report":
            session_id = self.selected_session_id()
            if session_id is not None:
                self.load_report_for_session(session_id)
        label = _operation_label(operation)
        self.statusBar().showMessage(f"{label} failed")
        self._update_actions()
        QMessageBox.warning(self, f"{label} failed", message)

    def _on_processing_cancelled(self, _job_id: str) -> None:
        self._progress.setVisible(False)
        operation = self._operation()
        self._active_session_id = None
        self._active_operation = None
        self.refresh_sessions()
        if operation == "report":
            session_id = self.selected_session_id()
            if session_id is not None:
                self.load_report_for_session(session_id)
        label = _operation_label(operation)
        self.statusBar().showMessage(f"{label} cancelled")
        self._update_actions()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._microphone_recorder is not None and self._microphone_recorder.is_recording:
            self._microphone_recorder.cancel()
        self._recording_timer.stop()
        if self._playback is not None:
            self._playback.stop()
        if self._report_coordinator is not None:
            self._report_coordinator.shutdown()
        if self._job_coordinator is not None:
            self._job_coordinator.shutdown()
        super().closeEvent(event)


def _operation_label(operation: str) -> str:
    if operation == "report":
        return "Report generation"
    if operation == "diarization":
        return "Diarization"
    return "Transcription"
