CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    profile TEXT NOT NULL DEFAULT 'conversation',
    language TEXT NOT NULL DEFAULT 'es',
    status TEXT NOT NULL DEFAULT 'created',
    original_audio_path TEXT,
    duration_seconds REAL
);

CREATE TABLE transcript_segments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    speaker_id TEXT,
    raw_text TEXT NOT NULL,
    corrected_text TEXT,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(session_id, segment_index)
);

CREATE TABLE processing_jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0.0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE speaker_roles (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    speaker_id TEXT NOT NULL,
    display_name TEXT,
    role TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, speaker_id)
);

CREATE INDEX idx_sessions_created_at ON sessions(created_at DESC);
CREATE INDEX idx_segments_session ON transcript_segments(session_id, segment_index);
CREATE INDEX idx_jobs_session ON processing_jobs(session_id, created_at);
CREATE INDEX idx_jobs_status ON processing_jobs(status);

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
