from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

DEFAULT_REPORT_MODEL = "qwen3.5:4b"
LIGHT_REPORT_MODEL = "lfm3:1.7b"
REPORT_LENGTHS = ("short", "normal", "long", "extensive")
REPORT_LENGTH_LABELS = MappingProxyType(
    {
        "short": "Short",
        "normal": "Normal",
        "long": "Long",
        "extensive": "Extensive",
    }
)
REPORT_LENGTH_DESCRIPTIONS = MappingProxyType(
    {
        "short": "Brief summary with the most important outcomes.",
        "normal": "Balanced report with narrative summaries and structured items.",
        "long": "More detail, more topics, and fuller explanations.",
        "extensive": "Maximum practical detail without padding or repetition.",
    }
)
REPORT_LANGUAGES = ("auto", "es", "en", "pt", "fr", "de", "it")
REPORT_LANGUAGE_LABELS = MappingProxyType(
    {
        "auto": "Same as transcript",
        "es": "Spanish",
        "en": "English",
        "pt": "Portuguese",
        "fr": "French",
        "de": "German",
        "it": "Italian",
    }
)
_NUM_PREDICT_BY_LENGTH = {
    "short": 4_096,
    "normal": 8_192,
    "long": 12_288,
    "extensive": 16_384,
}


@dataclass(frozen=True)
class ReportModelPreset:
    name: str
    label: str
    parameters: str
    download_size: str
    memory: str
    context: str
    license: str
    notes: str
    requirements: str | None = None


_PRESETS = (
    ReportModelPreset(
        name=DEFAULT_REPORT_MODEL,
        label="Qwen3.5 4B (default, balanced)",
        parameters="4.2B",
        download_size="about 3.3 GB",
        memory="about 4-6 GB RAM/VRAM",
        context="262K tokens",
        license="Qwen model license; verify the upstream model card",
        notes="Default quality/size balance. Already used by the reference smoke tests.",
    ),
    ReportModelPreset(
        name=LIGHT_REPORT_MODEL,
        label="LFM3 1.7B (light, CPU-friendly)",
        parameters="1.73B",
        download_size="about 1.1-1.7 GB",
        memory="about 1-2 GB RAM/VRAM",
        context="256K tokens",
        license="LFM Open License v1.0",
        notes=(
            "Smallest supported option, multilingual including Spanish. "
            "Use it for fast local reports when memory or speed matters; "
            "review drafts carefully."
        ),
        requirements=(
            "A current Ollama release is required; older servers may reject the "
            "manifest with HTTP 412."
        ),
    ),
    ReportModelPreset(
        name="qwen3.5:9b",
        label="Qwen3.5 9B (higher quality, heavier)",
        parameters="9.7B",
        download_size="about 6.6 GB",
        memory="about 8-12 GB RAM/VRAM",
        context="262K tokens",
        license="Qwen model license; verify the upstream model card",
        notes="Higher quality on long/complex conversations; slower and heavier.",
    ),
)

REPORT_MODEL_PRESETS = _PRESETS
REPORT_MODEL_PRESET_BY_NAME = MappingProxyType({preset.name: preset for preset in _PRESETS})


def preset_for(model_name: str) -> ReportModelPreset | None:
    return REPORT_MODEL_PRESET_BY_NAME.get(model_name)


def report_model_options(_model_name: str, length: str = "normal") -> dict[str, object]:
    """Return conservative Ollama options shared by every supported report model.

    A bounded context and output length keep small models predictable and avoid
    allocating a model's full advertised context window (256K for LFM3).
    """

    if length not in _NUM_PREDICT_BY_LENGTH:
        length = "normal"
    return {
        "temperature": 0,
        "num_ctx": 16_384,
        "num_predict": _NUM_PREDICT_BY_LENGTH[length],
    }


def report_length_guidance(length: str) -> str:
    guidance = {
        "short": (
            "Keep the report short: a 1-2 sentence executive summary, 1-3 topics "
            "with 1-2 sentence summaries, 2-4 important points, and a 1-2 sentence "
            "conclusion. Still include every explicit decision and action item."
        ),
        "normal": (
            "Write a balanced report: a 3-5 sentence executive summary, 2-5 topics "
            "with 2-4 sentence summaries, 4-8 important points, and a 2-4 sentence "
            "conclusion. Include every explicit decision and action item."
        ),
        "long": (
            "Write a detailed report: a 5-7 sentence executive summary, 3-7 topics "
            "with 3-5 sentence summaries, 6-12 important points, and a 3-5 sentence "
            "conclusion. Preserve useful context and rationale without repeating facts."
        ),
        "extensive": (
            "Write an extensive report: a 7-10 sentence executive summary, 5-10 topics "
            "with 4-6 sentence summaries, 10-16 important points, and a 4-6 sentence "
            "conclusion. Cover context, alternatives, contradictions, and consequences "
            "that the transcript supports, without padding or repetition."
        ),
    }
    return guidance.get(length, guidance["normal"])


def report_language_label(language: str) -> str:
    return REPORT_LANGUAGE_LABELS.get(language, language)


def report_length_label(length: str) -> str:
    return REPORT_LENGTH_LABELS.get(length, length)


def format_model_presets() -> str:
    lines = ["Supported local report models (download once during setup):"]
    for preset in REPORT_MODEL_PRESETS:
        lines.append(
            f"- {preset.name}: {preset.parameters}, {preset.download_size}, "
            f"{preset.context}\n"
            f"  {preset.label}\n"
            f"  License: {preset.license}\n"
            f"  {preset.notes}"
        )
        if preset.requirements:
            lines.append(f"  Requirement: {preset.requirements}")
    lines.append("")
    lines.append("Report lengths: short, normal, long, extensive.")
    lines.append("Report languages: same as transcript, Spanish, English, Portuguese,")
    lines.append("French, German, or Italian.")
    return "\n".join(lines)
