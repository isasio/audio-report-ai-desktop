from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from .report import NonEmptyText, ReportModel, SegmentId, ShortText


class ReportSourceSegment(ReportModel):
    id: SegmentId
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    speaker: ShortText | None = None
    text: NonEmptyText

    @model_validator(mode="after")
    def _validate_time_range(self) -> ReportSourceSegment:
        if self.end_seconds < self.start_seconds:
            raise ValueError("Segment end must not precede its start.")
        return self


class ReportGenerationSource(ReportModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: NonEmptyText
    title: ShortText
    language: str = Field(default="es", min_length=2, max_length=16)
    custom_instructions: str | None = Field(default=None, max_length=8_000)
    segments: list[ReportSourceSegment] = Field(min_length=1, max_length=100_000)


class TranscriptFact(ReportModel):
    kind: Literal[
        "fact",
        "decision",
        "action_item",
        "question",
        "contradiction",
        "ambiguity",
    ]
    text: NonEmptyText
    responsible_person: ShortText | None = None
    deadline: ShortText | None = None
    supporting_segment_ids: list[SegmentId] = Field(min_length=1, max_length=20)


class TranscriptFacts(ReportModel):
    facts: list[TranscriptFact] = Field(default_factory=list, max_length=500)


def transcript_facts_schema() -> dict[str, Any]:
    return TranscriptFacts.model_json_schema()
