from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRANSCRIPT_SCHEMA_VERSION = 1
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    index: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1)
    speaker_id: str | None = Field(default=None, max_length=64)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None

    @model_validator(mode="after")
    def _validate_interval(self) -> TranscriptSegment:
        if self.end < self.start:
            raise ValueError("Segment end must not be before its start.")
        return self


class Transcript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = TRANSCRIPT_SCHEMA_VERSION
    session_id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    language: str | None = Field(default=None, max_length=32)
    language_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    duration_seconds: float = Field(gt=0)
    model: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    device: str = Field(min_length=1, max_length=32)
    compute_type: str = Field(min_length=1, max_length=32)
    created_at: str = Field(min_length=1, max_length=64)
    segments: list[TranscriptSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_segments(self) -> Transcript:
        ids = {segment.id for segment in self.segments}
        if len(ids) != len(self.segments):
            raise ValueError("Segment IDs must be unique.")
        for expected_index, segment in enumerate(self.segments):
            if segment.index != expected_index:
                raise ValueError("Segment indexes must be sequential.")
            if segment.start > self.duration_seconds + 1.0:
                raise ValueError("Segment start is outside the audio duration.")
            if segment.end > self.duration_seconds + 1.0:
                raise ValueError("Segment end is outside the audio duration.")
        return self
