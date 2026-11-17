-- M2 0001 init: typed business-ontology store (SQLite).
-- Idempotent: every object uses IF NOT EXISTS. Applied under PRAGMA user_version.

CREATE TABLE IF NOT EXISTS concepts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    origin      TEXT NOT NULL,
    status      TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    evidence    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS operations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    origin      TEXT NOT NULL,
    status      TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    evidence    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS params (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    value,
    type        TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    value,
    statement     TEXT,
    preconditions TEXT NOT NULL DEFAULT '[]',
    predicate     TEXT,
    params        TEXT NOT NULL DEFAULT '[]',
    exceptions    TEXT NOT NULL DEFAULT '[]',
    outcomes      TEXT NOT NULL DEFAULT '[]',
    location      TEXT,
    supporting    TEXT NOT NULL DEFAULT '[]',
    conflicting   TEXT NOT NULL DEFAULT '[]',
    limitations   TEXT NOT NULL DEFAULT '[]',
    origin        TEXT NOT NULL,
    status        TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 1.0,
    evidence      TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS states (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    origin      TEXT NOT NULL,
    status      TEXT NOT NULL,
    evidence    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transitions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT,
    target         TEXT NOT NULL,
    guard          TEXT,
    trigger        TEXT,
    unknown_source INTEGER NOT NULL DEFAULT 0,
    origin         TEXT NOT NULL,
    status         TEXT NOT NULL,
    evidence       TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    phase      TEXT NOT NULL,
    origin     TEXT NOT NULL,
    status     TEXT NOT NULL,
    evidence   TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evidence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT NOT NULL,
    commit_sha   TEXT,
    path         TEXT NOT NULL,
    line_start   INTEGER,
    line_end     INTEGER,
    extractor    TEXT,
    version      TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assertions (
    id          TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    predicate   TEXT NOT NULL,
    object,
    occurrence  INTEGER NOT NULL DEFAULT 1,
    origin      TEXT NOT NULL,
    status      TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    evidence    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject         TEXT NOT NULL,
    status          TEXT NOT NULL,
    reviewer        TEXT,
    reason          TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    original_status TEXT,
    decided_at      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Append-only audit history for every store mutation.
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    record_id  TEXT,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',
    at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rules_name ON rules (name);
CREATE INDEX IF NOT EXISTS idx_evidence_repo_path ON evidence (repo, path);
CREATE INDEX IF NOT EXISTS idx_assertions_subject ON assertions (subject);
CREATE INDEX IF NOT EXISTS idx_reviews_subject ON reviews (subject);
CREATE INDEX IF NOT EXISTS idx_audit_kind_record ON audit_log (kind, record_id);

PRAGMA user_version = 1;
