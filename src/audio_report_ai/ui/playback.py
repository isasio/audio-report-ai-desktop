from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class PlaybackController(QObject):
    error_occurred = Signal(str)
    state_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def load(self, path: Path) -> None:
        raise NotImplementedError

    def seek(self, seconds: float) -> None:
        raise NotImplementedError

    def play_from(self, seconds: float) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    @property
    def position_seconds(self) -> float:
        raise NotImplementedError

    @property
    def is_playing(self) -> bool:
        raise NotImplementedError


class QtPlaybackController(PlaybackController):
    _READY_STATUSES = {
        QMediaPlayer.MediaStatus.LoadedMedia,
        QMediaPlayer.MediaStatus.BufferingMedia,
        QMediaPlayer.MediaStatus.BufferedMedia,
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._source: Path | None = None
        self._pending_position_ms: int | None = None
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)

    def load(self, path: Path) -> None:
        source = Path(path)
        if self._source == source:
            return
        self._player.stop()
        self._source = source
        self._pending_position_ms = None
        self._player.setSource(QUrl.fromLocalFile(str(source)))

    def seek(self, seconds: float) -> None:
        self._pending_position_ms = max(0, int(round(float(seconds) * 1000)))
        self._apply_pending_position()

    def play_from(self, seconds: float) -> None:
        self.seek(seconds)
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def stop(self) -> None:
        self._pending_position_ms = None
        self._player.stop()

    @property
    def position_seconds(self) -> float:
        return self._player.position() / 1000.0

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _apply_pending_position(self) -> None:
        if self._pending_position_ms is None:
            return
        if self._player.mediaStatus() not in self._READY_STATUSES:
            return
        self._player.setPosition(self._pending_position_ms)
        self._pending_position_ms = None

    def _on_media_status_changed(self, _status: QMediaPlayer.MediaStatus) -> None:
        self._apply_pending_position()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.state_changed.emit("playing")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.state_changed.emit("paused")
        else:
            self.state_changed.emit("stopped")

    def _on_error(self, _error: QMediaPlayer.Error, error_string: str) -> None:
        self.error_occurred.emit(error_string or "Audio playback failed.")
