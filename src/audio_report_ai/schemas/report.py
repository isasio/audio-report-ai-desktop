from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

REPORT_SCHEMA_VERSION = 1
REPORT_SCHEMA_NAME = "conversation_report"
ReportProfile = Literal["conversation", "veterinary_clinical"]
_SEGMENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_SPEAKER_ID_PATTERN = r"^SPEAKER_[0-9]{2,}$"

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
SegmentId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=_SEGMENT_ID_PATTERN,
    ),
]
SpeakerId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=_SPEAKER_ID_PATTERN,
    ),
]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportParticipant(ReportModel):
    name: ShortText
    role: ShortText | None = None
    speaker_id: SpeakerId | None = None
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ClinicalField(ReportModel):
    label: ShortText
    value: NonEmptyText
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ReportTopic(ReportModel):
    title: ShortText
    summary: NonEmptyText
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ReportPoint(ReportModel):
    text: NonEmptyText
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ReportDecision(ReportModel):
    description: NonEmptyText
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ReportActionItem(ReportModel):
    description: NonEmptyText
    responsible_person: ShortText | None = None
    deadline: ShortText | None = None
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ReportQuestion(ReportModel):
    question: NonEmptyText
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ReportReviewItem(ReportModel):
    description: NonEmptyText
    reason: ShortText
    supporting_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=500)


class ConversationReport(ReportModel):
    schema_version: Literal[1] = REPORT_SCHEMA_VERSION
    report_profile: ReportProfile = "conversation"
    title: ShortText
    date: ShortText | None = None
    participants: list[ReportParticipant] = Field(default_factory=list)
    clinical_fields: list[ClinicalField] = Field(default_factory=list, max_length=100)
    executive_summary: NonEmptyText
    main_topics: list[ReportTopic] = Field(default_factory=list)
    important_points: list[ReportPoint] = Field(default_factory=list)
    decisions: list[ReportDecision] = Field(default_factory=list)
    action_items: list[ReportActionItem] = Field(default_factory=list)
    unresolved_questions: list[ReportQuestion] = Field(default_factory=list)
    conclusion: NonEmptyText
    items_requiring_review: list[ReportReviewItem] = Field(default_factory=list)


def conversation_report_schema() -> dict[str, Any]:
    schema = ConversationReport.model_json_schema()
    for definition in (
        "ReportTopic",
        "ReportPoint",
        "ReportDecision",
        "ReportActionItem",
        "ReportQuestion",
        "ReportReviewItem",
        "ClinicalField",
    ):
        evidence = (
            schema.get("$defs", {})
            .get(definition, {})
            .get("properties", {})
            .get("supporting_segment_ids")
        )
        if isinstance(evidence, dict):
            evidence["minItems"] = 1
    return schema
