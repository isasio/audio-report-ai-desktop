from __future__ import annotations

import sqlite3
from pathlib import Path

from ..errors import DatabaseError

SCHEMA_VERSION = 3
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_MIGRATION_V2 = """
CREATE TABLE speaker_roles (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    speaker_id TEXT NOT NULL,
    display_name TEXT,
    role TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, speaker_id)
);
"""

_MIGRATION_V3 = """
CREATE TABLE reports (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version > 0),
    schema_name TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT,
    UNIQUE (session_id, version)
);

CREATE TABLE report_evidence (
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    claim_path TEXT NOT NULL,
    transcript_segment_id TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (report_id, claim_path, transcript_segment_id)
);

CREATE INDEX idx_reports_session ON reports(session_id, version DESC);
CREATE INDEX idx_report_evidence_report ON report_evidence(report_id);
"""

_MIGRATIONS = {
    2: _MIGRATION_V2,
    3: _MIGRATION_V3,
}


def migrate(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise DatabaseError(
            "database_schema_newer",
            f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}.",
        )
    if version == SCHEMA_VERSION:
        return
    if version < 0:
        raise DatabaseError(
            "database_migration_failed",
            "The database schema version is invalid.",
        )

    try:
        if version == 0:
            script = SCHEMA_PATH.read_text(encoding="utf-8")
        else:
            script = "\n".join(_MIGRATIONS[step] for step in range(version + 1, SCHEMA_VERSION + 1))
        connection.executescript(
            f"BEGIN;\n{script}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )
    except (OSError, KeyError, sqlite3.Error) as exc:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise DatabaseError(
            "database_migration_failed",
            "Could not migrate the database schema.",
        ) from exc
