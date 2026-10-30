from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CORRECTED_TRANSCRIPT_SCHEMA_VERSION = 1
REVIEW_STATUS_UNREVIEWED = "unreviewed"
REVIEW_STATUS_EDITED = "edited"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class CorrectedTranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    index: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speaker_id: str | None = Field(default=None, max_length=64)
    raw_text: str = Field(min_length=1)
    corrected_text: str | None = Field(default=None, min_length=1)
    review_status: Literal["unreviewed", "edited"] = REVIEW_STATUS_UNREVIEWED

    @model_validator(mode="after")
    def _validate_interval(self) -> CorrectedTranscriptSegment:
        if self.end < self.start:
            raise ValueError("Segment end must not be before its start.")
        return self


class CorrectedTranscript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = CORRECTED_TRANSCRIPT_SCHEMA_VERSION
    session_id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    updated_at: str = Field(min_length=1, max_length=64)
    segments: list[CorrectedTranscriptSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_segments(self) -> CorrectedTranscript:
        ids = {segment.id for segment in self.segments}
        if len(ids) != len(self.segments):
            raise ValueError("Segment IDs must be unique.")
        for expected_index, segment in enumerate(self.segments):
            if segment.index != expected_index:
                raise ValueError("Segment indexes must be sequential.")
        return self
