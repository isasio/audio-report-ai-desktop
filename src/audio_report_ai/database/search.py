from __future__ import annotations

import json
import unicodedata


def normalize_search_text(value: object) -> str:
    """Fold case, accents, and compatibility forms for local session search.

    The database stores original text untouched. This function is registered as a
    deterministic SQLite function and applied only at query time.
    """

    if not isinstance(value, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return without_marks.casefold()


def report_search_text(report_json: object) -> str:
    """Return only the string values from a stored report JSON document.

    JSON object keys are deliberately excluded so searching for "summary" does
    not match every report merely because the schema uses an
    ``executive_summary`` key.
    """

    if not isinstance(report_json, str) or not report_json.strip():
        return ""
    try:
        payload = json.loads(report_json)
    except (TypeError, ValueError):
        return ""

    parts: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    collect(payload)
    return "\n".join(parts)
