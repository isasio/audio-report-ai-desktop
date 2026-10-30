from __future__ import annotations

from types import MappingProxyType

REPORT_PROFILES = ("conversation", "veterinary_clinical")

REPORT_PROFILE_LABELS = MappingProxyType(
    {
        "conversation": "General conversation report",
        "veterinary_clinical": "Veterinary clinical report",
    }
)
REPORT_PROFILE_DESCRIPTIONS = MappingProxyType(
    {
        "conversation": ("Summary, topics, decisions, action items, questions, and evidence."),
        "veterinary_clinical": (
            "Formal clinical-style report with patient/visit details, history, "
            "examination, assessment, treatment, plan, and evidence. Use qwen3.5:4b "
            "or larger for best structure; review every clinical field."
        ),
    }
)

_SECTION_LABELS = {
    "conversation": MappingProxyType(
        {
            "summary": "EXECUTIVE SUMMARY",
            "topics": "MAIN TOPICS",
            "points": "IMPORTANT POINTS",
            "decisions": "DECISIONS",
            "actions": "ACTION ITEMS",
            "questions": "UNRESOLVED QUESTIONS",
            "conclusion": "CONCLUSION",
            "clinical_fields": "PATIENT AND VISIT DETAILS",
            "participants": "PARTICIPANTS",
            "review": "ITEMS REQUIRING REVIEW",
        }
    ),
    "veterinary_clinical": MappingProxyType(
        {
            "summary": "CLINICAL SUMMARY",
            "topics": "CLINICAL SECTIONS",
            "points": "KEY FINDINGS",
            "decisions": "DIAGNOSIS AND CLINICAL DECISIONS",
            "actions": "TREATMENT, PLAN AND FOLLOW-UP",
            "questions": "PENDING RESULTS AND CLIENT QUESTIONS",
            "conclusion": "CLINICAL CONCLUSION",
            "clinical_fields": "PATIENT AND VISIT DETAILS",
            "participants": "PEOPLE AND PATIENT",
            "review": "ITEMS REQUIRING REVIEW",
        }
    ),
}
REPORT_SECTION_LABELS = MappingProxyType(_SECTION_LABELS)


def profile_label(profile: str) -> str:
    return REPORT_PROFILE_LABELS.get(profile, REPORT_PROFILE_LABELS["conversation"])


def profile_description(profile: str) -> str:
    return REPORT_PROFILE_DESCRIPTIONS.get(
        profile,
        REPORT_PROFILE_DESCRIPTIONS["conversation"],
    )


def section_labels(profile: str) -> MappingProxyType[str, str]:
    return REPORT_SECTION_LABELS.get(profile, REPORT_SECTION_LABELS["conversation"])


def profile_prompt_instruction(profile: str) -> str:
    if profile == "veterinary_clinical":
        return (
            "Create a formal veterinary clinical report from the consultation transcript. "
            "Do not invent diagnoses, test results, medications, doses, patient identifiers, "
            "or owner details. Distinguish owner-reported history, observed examination "
            "findings, clinician assessment, and the agreed plan. Preserve uncertainty and "
            "flag anything that needs veterinary review. "
            "Fill clinical_fields with label/value evidence items for the patient and visit "
            "when the transcript supports them: Patient name, Species, Breed, Sex, Age, "
            "Weight, Owner, Visit date, Clinic, Veterinarian. Translate labels into the "
            "report language. Use main_topics for the clinical narrative sections, for "
            "example Reason for visit, History/anamnesis, Physical examination, Diagnostic "
            "findings, Clinical assessment, Treatment, and Follow-up. Use important_points "
            "for key findings, decisions for diagnosis/differential and clinical decisions, "
            "action_items for treatment, owner instructions, and follow-up, and "
            "unresolved_questions for pending tests or unanswered client questions."
        )
    return (
        "Create a general conversation report with a summary, topics, important points, "
        "decisions, action items, unresolved questions, and a conclusion. Use only "
        "information supported by the transcript."
    )
