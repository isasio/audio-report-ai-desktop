from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ..errors import DatabaseError
from .search import normalize_search_text, report_search_text


def connect(database_path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(database_path.parent, 0o700)
        connection = sqlite3.connect(str(database_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.create_function(
            "app_normalize_search",
            1,
            normalize_search_text,
            deterministic=True,
        )
        connection.create_function(
            "app_report_search_text",
            1,
            report_search_text,
            deterministic=True,
        )
        os.chmod(database_path, 0o600)
        return connection
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise DatabaseError(
            "database_open_failed",
            f"Could not open database: {database_path}",
        ) from exc
