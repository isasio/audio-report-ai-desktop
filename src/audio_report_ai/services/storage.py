from __future__ import annotations

import re
from pathlib import Path

_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def session_directory(root: Path, session_id: str) -> Path:
    """Return the app-managed directory for a session, rejecting unsafe identifiers."""

    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Unsafe session identifier.")
    resolved_root = root.resolve()
    candidate = (resolved_root / session_id).resolve()
    if candidate.parent != resolved_root:
        raise ValueError("Session directory escapes the app-managed root.")
    return candidate
