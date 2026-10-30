from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt

from ..database.repositories import TranscriptSegmentRecord


def format_timestamp(seconds: float) -> str:
    total_milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def format_full_transcript(
    segments: Sequence[TranscriptSegmentRecord],
    speaker_labels: Mapping[str, str] | None = None,
    include_timestamps: bool = True,
) -> str:
    """Render the whole transcript as readable text, preferring corrections."""

    labels = speaker_labels or {}
    lines: list[str] = []
    for segment in segments:
        text = (segment.corrected_text or segment.raw_text).strip()
        if not text:
            continue
        prefix = f"[{format_timestamp(segment.start_seconds)}] " if include_timestamps else ""
        speaker = ""
        if segment.speaker_id:
            speaker = f"{labels.get(segment.speaker_id, segment.speaker_id)}: "
        lines.append(f"{prefix}{speaker}{text}")
    return "\n".join(lines)


class TranscriptTableModel(QAbstractTableModel):
    HEADERS = ("Start", "End", "Speaker", "Raw text", "Corrected text", "Status")

    def __init__(
        self,
        segments: list[TranscriptSegmentRecord] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._segments = list(segments or [])
        self._speaker_labels: dict[str, str] = {}

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._segments)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        segment = self._segments[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            speaker = segment.speaker_id or ""
            values = (
                format_timestamp(segment.start_seconds),
                format_timestamp(segment.end_seconds),
                self._speaker_labels.get(speaker, speaker),
                segment.raw_text,
                segment.corrected_text or "",
                segment.review_status,
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return segment.id
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

    def set_segments(self, segments: list[TranscriptSegmentRecord]) -> None:
        self.beginResetModel()
        self._segments = list(segments)
        self._speaker_labels = {}
        self.endResetModel()

    def set_speaker_labels(self, labels: dict[str, str]) -> None:
        self.beginResetModel()
        self._speaker_labels = dict(labels)
        self.endResetModel()

    def speaker_label(self, speaker_id: str | None) -> str:
        if not speaker_id:
            return ""
        return self._speaker_labels.get(speaker_id, speaker_id)

    def segment_at(self, row: int) -> TranscriptSegmentRecord | None:
        if 0 <= row < len(self._segments):
            return self._segments[row]
        return None

    def replace_segment(self, segment: TranscriptSegmentRecord) -> bool:
        for row, existing in enumerate(self._segments):
            if existing.id != segment.id:
                continue
            self._segments[row] = segment
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, len(self.HEADERS) - 1),
            )
            return True
        return False
