from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..schemas.report import (
    ClinicalField,
    ConversationReport,
    ReportActionItem,
    ReportDecision,
    ReportParticipant,
    ReportPoint,
    ReportQuestion,
    ReportReviewItem,
    ReportTopic,
)

_EVIDENCE_FIELDS = (
    "participants",
    "clinical_fields",
    "main_topics",
    "important_points",
    "decisions",
    "action_items",
    "unresolved_questions",
)
_MANDATORY_EVIDENCE_FIELDS = {
    "main_topics",
    "important_points",
    "decisions",
    "action_items",
    "unresolved_questions",
}
_ITEM_TYPES = (
    ReportParticipant
    | ClinicalField
    | ReportTopic
    | ReportPoint
    | ReportDecision
    | ReportActionItem
    | ReportQuestion
    | ReportReviewItem
)


@dataclass(frozen=True)
class ReportEvidenceLink:
    claim_path: str
    transcript_segment_id: str
    note: str


@dataclass(frozen=True)
class ReportValidationResult:
    report: ConversationReport
    evidence: list[ReportEvidenceLink]
    removed_evidence_count: int
    review_flag_count: int


def validate_report(
    report: ConversationReport,
    segment_text_by_id: Mapping[str, str],
    manual_names: Sequence[str] = (),
) -> ReportValidationResult:
    known_ids = set(segment_text_by_id)
    normalized_manual_names = {name.strip().casefold() for name in manual_names if name.strip()}
    all_transcript_text = "\n".join(segment_text_by_id.values()).casefold()
    flags: list[ReportReviewItem] = list(report.items_requiring_review)
    flag_keys = {(item.reason, item.description) for item in flags}
    removed_evidence_count = 0

    def add_flag(reason: str, description: str, segment_ids: list[str]) -> None:
        key = (reason, description)
        if key in flag_keys:
            return
        flag_keys.add(key)
        flags.append(
            ReportReviewItem(
                description=description,
                reason=reason,
                supporting_segment_ids=_unique_known_ids(segment_ids, known_ids),
            )
        )

    updated: dict[str, list[_ITEM_TYPES]] = {}
    for field in _EVIDENCE_FIELDS:
        items = getattr(report, field)
        sanitized: list[_ITEM_TYPES] = []
        for index, item in enumerate(items):
            path = f"{field}[{index}]"
            evidence_ids = _unique_known_ids(item.supporting_segment_ids, known_ids)
            unknown_ids = [
                segment_id
                for segment_id in item.supporting_segment_ids
                if segment_id not in known_ids
            ]
            removed_evidence_count += len(unknown_ids)
            if unknown_ids:
                add_flag(
                    "unknown_evidence",
                    (
                        f"Evidence for {path} referred to transcript segments that are not "
                        "in this session and was removed."
                    ),
                    [],
                )
            if (
                field in _MANDATORY_EVIDENCE_FIELDS
                and item.supporting_segment_ids
                and not evidence_ids
            ) or (field in _MANDATORY_EVIDENCE_FIELDS and not item.supporting_segment_ids):
                add_flag(
                    "missing_evidence",
                    f"The report item {path} has no supporting transcript evidence.",
                    [],
                )
            if isinstance(item, ReportParticipant):
                name = item.name.strip()
                if (
                    name.casefold() not in normalized_manual_names
                    and name.casefold() not in all_transcript_text
                ):
                    add_flag(
                        "unsupported_participant",
                        f"Participant {name!r} at {path} is not supported by the transcript.",
                        evidence_ids,
                    )
            if isinstance(item, ReportActionItem):
                if item.responsible_person is None:
                    add_flag(
                        "unassigned_action",
                        f"Action item {path} has no responsible person.",
                        evidence_ids,
                    )
                elif evidence_ids:
                    support_text = _support_text(evidence_ids, segment_text_by_id)
                    person = item.responsible_person.strip()
                    if (
                        person.casefold() not in normalized_manual_names
                        and person.casefold() not in support_text
                    ):
                        add_flag(
                            "unsupported_detail",
                            (
                                f"The responsible person for action item {path} is not "
                                "supported by its evidence."
                            ),
                            evidence_ids,
                        )
                if item.deadline is not None and evidence_ids:
                    support_text = _support_text(evidence_ids, segment_text_by_id)
                    deadline = item.deadline.strip()
                    if deadline.casefold() not in support_text:
                        add_flag(
                            "unsupported_detail",
                            (
                                f"The deadline for action item {path} is not supported "
                                "by its evidence."
                            ),
                            evidence_ids,
                        )
            sanitized.append(item.model_copy(update={"supporting_segment_ids": evidence_ids}))
        updated[field] = sanitized

    if report.date is not None and report.date.strip().casefold() not in all_transcript_text:
        add_flag(
            "unsupported_date",
            "The report date is not supported by the transcript.",
            [],
        )

    if report.report_profile == "veterinary_clinical":
        if not report.clinical_fields:
            add_flag(
                "missing_clinical_fields",
                "The veterinary report has no patient or visit details.",
                [],
            )
        if not report.main_topics:
            add_flag(
                "missing_clinical_sections",
                "The veterinary report has no clinical narrative sections.",
                [],
            )
        if not report.action_items:
            add_flag(
                "missing_follow_up",
                "The veterinary report has no treatment, plan, or follow-up items.",
                [],
            )

    if flags != report.items_requiring_review:
        updated["items_requiring_review"] = flags
    else:
        updated["items_requiring_review"] = list(report.items_requiring_review)

    validated = report.model_copy(update=updated)
    evidence: list[ReportEvidenceLink] = []
    seen: set[tuple[str, str]] = set()
    for field in (*_EVIDENCE_FIELDS, "items_requiring_review"):
        for index, item in enumerate(getattr(validated, field)):
            path = f"{field}[{index}]"
            for segment_id in item.supporting_segment_ids:
                key = (path, segment_id)
                if key in seen or segment_id not in known_ids:
                    continue
                seen.add(key)
                evidence.append(
                    ReportEvidenceLink(
                        claim_path=path,
                        transcript_segment_id=segment_id,
                        note="supporting",
                    )
                )
    return ReportValidationResult(
        report=validated,
        evidence=evidence,
        removed_evidence_count=removed_evidence_count,
        review_flag_count=len(validated.items_requiring_review),
    )


def _unique_known_ids(segment_ids: Sequence[str], known_ids: set[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for segment_id in segment_ids:
        if segment_id in seen or segment_id not in known_ids:
            continue
        seen.add(segment_id)
        unique.append(segment_id)
    return unique


def _support_text(
    segment_ids: Sequence[str],
    segment_text_by_id: Mapping[str, str],
) -> str:
    return "\n".join(segment_text_by_id[segment_id] for segment_id in segment_ids).casefold()
