from __future__ import annotations

import itertools
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import AppError
from ..schemas.report import ConversationReport

EVALUATION_MANIFEST_VERSION = 1


class EvaluationError(AppError):
    pass


class ReferenceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speaker: str | None = Field(default=None, max_length=64)


class ExpectedReportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=2_000)
    keywords: list[str] = Field(default_factory=list, max_length=50)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str = Field(min_length=1, max_length=2_000)
    language: str = Field(default="es", min_length=1, max_length=16)
    audio: str | None = None
    reference_transcript: str = Field(min_length=1)
    reference_segments: list[ReferenceSegment] = Field(default_factory=list)
    critical_terms: list[str] = Field(default_factory=list)
    critical_numbers: list[str] = Field(default_factory=list)
    critical_negations: list[str] = Field(default_factory=list)
    expected_decisions: list[ExpectedReportItem] = Field(default_factory=list)
    expected_action_items: list[ExpectedReportItem] = Field(default_factory=list)
    hypothesis_transcript: str | None = None
    hypothesis_diarization: str | None = None
    report: str | None = None
    notes: str | None = None


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    cases: list[EvaluationCase] = Field(min_length=1)


@dataclass(frozen=True)
class ReportMetrics:
    evidence_link_validity: float | None
    expected_item_coverage: float | None
    evidence_links: int
    invalid_evidence_links: int
    expected_items: int
    covered_items: int


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: str
    description: str
    word_error_rate: float | None = None
    character_error_rate: float | None = None
    critical_term_recall: float | None = None
    critical_number_recall: float | None = None
    critical_negation_recall: float | None = None
    diarization_accuracy: float | None = None
    report: ReportMetrics | None = None
    missed_terms: tuple[str, ...] = ()
    missed_numbers: tuple[str, ...] = ()
    missed_negations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationSummary:
    name: str
    manifest_path: Path
    cases: tuple[CaseResult, ...] = ()
    aggregates: dict[str, float | None] = field(default_factory=dict)


def normalize_text(text: str) -> str:
    lowered = text.casefold().replace("_", " ")
    cleaned = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    return " ".join(cleaned.split())


def _tokens(text: str) -> list[str]:
    normalized = normalize_text(text)
    return normalized.split() if normalized else []


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    if not reference:
        return len(hypothesis)
    if not hypothesis:
        return len(reference)
    previous = list(range(len(hypothesis) + 1))
    for ref_index, ref_item in enumerate(reference, start=1):
        current = [ref_index]
        for hyp_index, hyp_item in enumerate(hypothesis, start=1):
            cost = 0 if ref_item == hyp_item else 1
            current.append(
                min(
                    previous[hyp_index] + 1,
                    current[hyp_index - 1] + 1,
                    previous[hyp_index - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref_tokens = _tokens(reference)
    hyp_tokens = _tokens(hypothesis)
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return _edit_distance(ref_tokens, hyp_tokens) / len(ref_tokens)


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref_chars = list(normalize_text(reference).replace(" ", ""))
    hyp_chars = list(normalize_text(hypothesis).replace(" ", ""))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return _edit_distance(ref_chars, hyp_chars) / len(ref_chars)


def critical_recall(terms: list[str], hypothesis: str) -> tuple[float | None, tuple[str, ...]]:
    if not terms:
        return None, ()
    normalized_hypothesis = f" {normalize_text(hypothesis)} "
    missed: list[str] = []
    for term in terms:
        normalized_term = normalize_text(term)
        if not normalized_term or f" {normalized_term} " not in normalized_hypothesis:
            missed.append(term)
    return (len(terms) - len(missed)) / len(terms), tuple(missed)


def _resolve_relative(root: Path, value: str, field_name: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise EvaluationError(
            "evaluation_path_invalid",
            f"{field_name} must be a relative path inside the corpus directory.",
        )
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise EvaluationError(
            "evaluation_path_invalid",
            f"{field_name} must stay inside the corpus directory.",
        )
    return resolved


def load_manifest(manifest_path: str | Path) -> EvaluationManifest:
    path = Path(manifest_path).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvaluationError(
            "evaluation_manifest_unreadable",
            f"Could not read the evaluation manifest: {path}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise EvaluationError(
            "evaluation_manifest_invalid",
            f"Evaluation manifest is not valid JSON: {path}",
        ) from exc
    try:
        manifest = EvaluationManifest.model_validate(raw)
    except ValidationError as exc:
        raise EvaluationError(
            "evaluation_manifest_invalid",
            f"Evaluation manifest is invalid: {path}",
        ) from exc
    root = path.parent
    seen_ids: set[str] = set()
    for case in manifest.cases:
        if case.id in seen_ids:
            raise EvaluationError(
                "evaluation_manifest_invalid",
                f"Duplicate evaluation case id: {case.id}",
            )
        seen_ids.add(case.id)
        for value, field_name in (
            (case.audio, "audio"),
            (case.hypothesis_transcript, "hypothesis_transcript"),
            (case.hypothesis_diarization, "hypothesis_diarization"),
            (case.report, "report"),
        ):
            if value is not None:
                _resolve_relative(root, value, f"{case.id}.{field_name}")
    return manifest


def _read_transcript(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvaluationError(
            "evaluation_transcript_unreadable",
            f"Could not read transcript: {path}",
        ) from exc
    if path.suffix.lower() != ".json":
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvaluationError(
            "evaluation_transcript_invalid",
            f"Transcript JSON is invalid: {path}",
        ) from exc
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        segments = payload
    elif isinstance(payload, dict):
        segments = payload.get("segments")
    else:
        segments = None
    if not isinstance(segments, list):
        raise EvaluationError(
            "evaluation_transcript_invalid",
            f"Transcript JSON must contain a segments list: {path}",
        )
    texts: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = segment.get("text", segment.get("raw_text", segment.get("corrected_text")))
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return " ".join(texts)


def _speaker_map(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvaluationError(
            "evaluation_diarization_unreadable",
            f"Could not read diarization labels: {path}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise EvaluationError(
            "evaluation_diarization_invalid",
            f"Diarization labels are not valid JSON: {path}",
        ) from exc
    if isinstance(payload, dict) and isinstance(payload.get("segments"), list):
        payload = payload["segments"]
    mapping: dict[str, str] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, str):
                mapping[key] = value
        return mapping
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            segment_id = entry.get("id", entry.get("segment_id"))
            speaker = entry.get("speaker", entry.get("speaker_id"))
            if isinstance(segment_id, str) and isinstance(speaker, str):
                mapping[segment_id] = speaker
        return mapping
    raise EvaluationError(
        "evaluation_diarization_invalid",
        f"Diarization labels have an unsupported shape: {path}",
    )


def diarization_accuracy(
    reference_segments: list[ReferenceSegment],
    hypothesis: dict[str, str],
) -> float | None:
    reference = [segment for segment in reference_segments if segment.speaker is not None]
    if not reference:
        return None
    durations: dict[tuple[str, str], float] = {}
    total = 0.0
    for segment in reference:
        duration = max(0.0, segment.end - segment.start)
        total += duration
        predicted = hypothesis.get(segment.id)
        if predicted is not None:
            key = (predicted, segment.speaker or "")
            durations[key] = durations.get(key, 0.0) + duration
    if total <= 0:
        return None
    reference_labels = sorted({segment.speaker for segment in reference if segment.speaker})
    predicted_labels = sorted({value for value in hypothesis.values() if value})
    if not predicted_labels:
        return 0.0
    best = 0.0
    if len(predicted_labels) <= 7 and len(predicted_labels) <= len(reference_labels):
        for permutation in itertools.permutations(reference_labels, len(predicted_labels)):
            score = sum(
                durations.get((predicted, reference_label), 0.0)
                for predicted, reference_label in zip(predicted_labels, permutation, strict=False)
            )
            best = max(best, score)
    else:
        used: set[str] = set()
        for predicted in predicted_labels:
            options = [
                (durations.get((predicted, label), 0.0), label)
                for label in reference_labels
                if label not in used
            ]
            if options:
                score, label = max(options)
                best += score
                used.add(label)
    return best / total


def _report_text(report: ConversationReport) -> str:
    values: list[str] = [
        report.title,
        report.executive_summary,
        report.conclusion,
    ]
    values.extend(participant.name for participant in report.participants)
    values.extend(participant.role or "" for participant in report.participants)
    for topic in report.main_topics:
        values.extend((topic.title, topic.summary))
    values.extend(point.text for point in report.important_points)
    values.extend(decision.description for decision in report.decisions)
    for action in report.action_items:
        values.extend(
            (
                action.description,
                action.responsible_person or "",
                action.deadline or "",
            )
        )
    values.extend(question.question for question in report.unresolved_questions)
    for item in report.items_requiring_review:
        values.extend((item.description, item.reason))
    return " ".join(value for value in values if value)


def _report_metrics(
    report: ConversationReport,
    reference_segment_ids: set[str],
    expected_items: list[ExpectedReportItem],
) -> ReportMetrics:
    evidence_ids: list[str] = []
    for value in _walk_supporting_ids(report.model_dump(mode="json")):
        evidence_ids.append(value)
    valid = sum(1 for segment_id in evidence_ids if segment_id in reference_segment_ids)
    validity = (valid / len(evidence_ids)) if evidence_ids and reference_segment_ids else None
    invalid = len(evidence_ids) - valid if reference_segment_ids else 0

    report_text = f" {normalize_text(_report_text(report))} "
    covered = 0
    for item in expected_items:
        keywords = [normalize_text(keyword) for keyword in item.keywords]
        if keywords and all(f" {keyword} " in report_text for keyword in keywords):
            covered += 1
    coverage = (covered / len(expected_items)) if expected_items else None
    return ReportMetrics(
        evidence_link_validity=validity,
        expected_item_coverage=coverage,
        evidence_links=len(evidence_ids),
        invalid_evidence_links=invalid,
        expected_items=len(expected_items),
        covered_items=covered,
    )


def _walk_supporting_ids(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "supporting_segment_ids" and isinstance(item, list):
                found.extend(entry for entry in item if isinstance(entry, str))
            else:
                found.extend(_walk_supporting_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_supporting_ids(item))
    return found


def _load_report(path: Path) -> ConversationReport:
    try:
        return ConversationReport.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvaluationError(
            "evaluation_report_unreadable",
            f"Could not read report: {path}",
        ) from exc
    except (ValueError, ValidationError) as exc:
        raise EvaluationError(
            "evaluation_report_invalid",
            f"Report is not a valid conversation report: {path}",
        ) from exc


def evaluate_case(case: EvaluationCase, corpus_root: Path) -> CaseResult:
    if case.hypothesis_transcript is None:
        return CaseResult(
            case_id=case.id,
            status="pending",
            description=case.description,
            notes=("No hypothesis transcript yet; record and transcribe this case locally.",),
        )
    transcript_path = _resolve_relative(
        corpus_root,
        case.hypothesis_transcript,
        f"{case.id}.hypothesis_transcript",
    )
    hypothesis = _read_transcript(transcript_path)
    term_recall, missed_terms = critical_recall(case.critical_terms, hypothesis)
    number_recall, missed_numbers = critical_recall(case.critical_numbers, hypothesis)
    negation_recall, missed_negations = critical_recall(case.critical_negations, hypothesis)

    diarization: float | None = None
    if case.hypothesis_diarization is not None:
        labels = _speaker_map(
            _resolve_relative(
                corpus_root,
                case.hypothesis_diarization,
                f"{case.id}.hypothesis_diarization",
            )
        )
        diarization = diarization_accuracy(case.reference_segments, labels)

    report_metrics: ReportMetrics | None = None
    notes: list[str] = []
    if case.report is not None:
        report = _load_report(_resolve_relative(corpus_root, case.report, f"{case.id}.report"))
        expected_items = [*case.expected_decisions, *case.expected_action_items]
        report_metrics = _report_metrics(
            report,
            {segment.id for segment in case.reference_segments},
            expected_items,
        )
    else:
        notes.append("No report artifact was supplied.")

    return CaseResult(
        case_id=case.id,
        status="evaluated",
        description=case.description,
        word_error_rate=word_error_rate(case.reference_transcript, hypothesis),
        character_error_rate=character_error_rate(case.reference_transcript, hypothesis),
        critical_term_recall=term_recall,
        critical_number_recall=number_recall,
        critical_negation_recall=negation_recall,
        diarization_accuracy=diarization,
        report=report_metrics,
        missed_terms=missed_terms,
        missed_numbers=missed_numbers,
        missed_negations=missed_negations,
        notes=tuple(notes),
    )


def _mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def evaluate_manifest(manifest_path: str | Path) -> EvaluationSummary:
    path = Path(manifest_path).expanduser().resolve()
    manifest = load_manifest(path)
    root = path.parent
    cases = tuple(evaluate_case(case, root) for case in manifest.cases)
    aggregates = {
        "word_error_rate": _mean([case.word_error_rate for case in cases]),
        "character_error_rate": _mean([case.character_error_rate for case in cases]),
        "critical_term_recall": _mean([case.critical_term_recall for case in cases]),
        "critical_number_recall": _mean([case.critical_number_recall for case in cases]),
        "critical_negation_recall": _mean([case.critical_negation_recall for case in cases]),
        "diarization_accuracy": _mean([case.diarization_accuracy for case in cases]),
        "report_evidence_link_validity": _mean(
            [case.report.evidence_link_validity if case.report else None for case in cases]
        ),
        "report_expected_item_coverage": _mean(
            [case.report.expected_item_coverage if case.report else None for case in cases]
        ),
    }
    return EvaluationSummary(
        name=manifest.name,
        manifest_path=path,
        cases=cases,
        aggregates=aggregates,
    )


def _format_metric(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def format_evaluation_summary(summary: EvaluationSummary) -> str:
    lines = [f"Evaluation: {summary.name}", f"Manifest: {summary.manifest_path}", ""]
    lines.append(
        "| Case | Status | WER | CER | Terms | Numbers | Negations | "
        "Diarization | Evidence | Coverage |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for case in summary.cases:
        report = case.report
        lines.append(
            "| "
            + " | ".join(
                [
                    case.case_id.replace("|", "\\|"),
                    case.status,
                    _format_metric(case.word_error_rate),
                    _format_metric(case.character_error_rate),
                    _format_metric(case.critical_term_recall),
                    _format_metric(case.critical_number_recall),
                    _format_metric(case.critical_negation_recall),
                    _format_metric(case.diarization_accuracy),
                    _format_metric(report.evidence_link_validity if report else None),
                    _format_metric(report.expected_item_coverage if report else None),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("Aggregate (mean of available cases):")
    for key, value in summary.aggregates.items():
        lines.append(f"  {key}: {_format_metric(value)}")
    missed = [
        (case.case_id, case.missed_terms, case.missed_numbers, case.missed_negations)
        for case in summary.cases
        if case.missed_terms or case.missed_numbers or case.missed_negations
    ]
    if missed:
        lines.append("")
        lines.append("Critical misses:")
        for case_id, terms, numbers, negations in missed:
            details = []
            if terms:
                details.append(f"terms={list(terms)}")
            if numbers:
                details.append(f"numbers={list(numbers)}")
            if negations:
                details.append(f"negations={list(negations)}")
            lines.append(f"  {case_id}: {', '.join(details)}")
    notes = [(case.case_id, case.notes) for case in summary.cases if case.notes]
    if notes:
        lines.append("")
        lines.append("Notes:")
        for case_id, case_notes in notes:
            lines.append(f"  {case_id}: {' '.join(case_notes)}")
    lines.append("")
    lines.append(
        "WER/CER and keyword coverage are automatic proxies. Report truthfulness, "
        "unsupported claims, and action-item correctness still require manual review."
    )
    return "\n".join(lines)


def summary_to_json(summary: EvaluationSummary) -> dict[str, Any]:
    return {
        "name": summary.name,
        "manifest": str(summary.manifest_path),
        "aggregates": summary.aggregates,
        "cases": [
            {
                "id": case.case_id,
                "status": case.status,
                "description": case.description,
                "word_error_rate": case.word_error_rate,
                "character_error_rate": case.character_error_rate,
                "critical_term_recall": case.critical_term_recall,
                "critical_number_recall": case.critical_number_recall,
                "critical_negation_recall": case.critical_negation_recall,
                "diarization_accuracy": case.diarization_accuracy,
                "report": None
                if case.report is None
                else {
                    "evidence_link_validity": case.report.evidence_link_validity,
                    "expected_item_coverage": case.report.expected_item_coverage,
                    "evidence_links": case.report.evidence_links,
                    "invalid_evidence_links": case.report.invalid_evidence_links,
                    "expected_items": case.report.expected_items,
                    "covered_items": case.report.covered_items,
                },
                "missed_terms": list(case.missed_terms),
                "missed_numbers": list(case.missed_numbers),
                "missed_negations": list(case.missed_negations),
                "notes": list(case.notes),
            }
            for case in summary.cases
        ],
    }


def write_summary_json(path: str | Path, summary: EvaluationSummary) -> Path:
    """Write evaluation results atomically without changing the parent's mode.

    Output paths may be outside the app data directory, so unlike report
    storage this helper does not chmod an arbitrary parent directory.
    """

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(summary_to_json(summary), ensure_ascii=False, indent=2) + "\n"
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination
