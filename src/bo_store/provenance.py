"""Provenance store (SQLite) for business-ontologies.

Secret redaction is centralized here (redact_value) and applied before INSERT
and on every export. M1 uses canary/test secret formats only - no real secrets.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3


# Secret formats redacted from provenance artifacts. Covers common provider/
# token formats; the entropy fallback catches high-entropy tokens that match
# no named pattern. Pure hex digests (content hashes / commit SHAs) are
# explicitly excluded from the entropy fallback so they are never redacted.
_SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{8,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|ghp_[A-Za-z0-9_\-]{8,}"
    r"|xoxb-[A-Za-z0-9_\-]{8,}"
    r"|BEGIN\s+PRIVATE\s+KEY",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_ENTROPY_THRESHOLD = 4.5
_ENTROPY_MIN_LEN = 20


# Epistemic enums (Sec 8 provenance). ValueError raised on mismatch.
ORIGINS = {"deterministic", "model-inferred", "human-reviewed"}
STATUSES = {"proposed", "accepted", "rejected", "stale", "disputed"}


def _shannon_entropy(value):
    if not value:
        return 0.0
    counts = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def _looks_secret(value):
    """True if *value* contains a named secret pattern or a high-entropy token."""
    if _SECRET_RE.search(value):
        return True
    # Entropy fallback: single high-entropy token (no spaces), not a hex digest.
    if (
        len(value) >= _ENTROPY_MIN_LEN
        and " " not in value
        and not _HEX_RE.match(value)
        and _shannon_entropy(value) >= _ENTROPY_THRESHOLD
    ):
        return True
    return False


def redact_value(value):
    """Return *value* with secrets replaced by [REDACTED], recursing into containers.

    Applied before INSERT and on every export. Whole value is redacted when a
    secret is detected so no fragment leaks.
    """
    if isinstance(value, str):
        if _looks_secret(value):
            return "[REDACTED]"
        return _SECRET_RE.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v) for v in value)
    return value


def _sanitize(value):
    if not isinstance(value, str):
        return value
    return redact_value(value)


def _connect(db):
    if hasattr(db, "execute"):
        return db, False
    conn = sqlite3.connect(str(db))
    return conn, True


def _get(data, *keys):
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    lowered = {str(k).lower(): v for k, v in data.items()}
    for k in keys:
        v = lowered.get(k.lower())
        if v is not None:
            return v
    return None


def init_db(path):
    """Initialize the provenance SQLite database at *path* and return a connection."""
    conn, _ = _connect(path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            commit_sha TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo TEXT,
            commit_sha TEXT,
            file_range TEXT,
            content_hash TEXT,
            extractor_version TEXT,
            run_timestamp TEXT,
            origin TEXT,
            status TEXT,
            confidence_basis TEXT,
            excerpt TEXT
        )"""
    )
    conn.execute("CREATE VIEW IF NOT EXISTS artifacts AS SELECT * FROM provenance")
    conn.commit()
    return conn


def record_repo(db, *args, **kwargs):
    """Record a repository and return its row id."""
    data = dict(kwargs)
    if args and isinstance(args[0], dict):
        data.update(args[0])
        args = args[1:]

    conn, close = _connect(db)

    repo = _get(data, "repo", "name", "repo_name", "repo_id", "repository")
    if repo is None and args:
        repo = args[0]
        args = args[1:]
    commit_sha = _get(data, "commit_sha", "commit", "revision", "sha", "commit_id")
    if commit_sha is None and args:
        commit_sha = args[0]
        args = args[1:]

    repo = _sanitize(repo) if isinstance(repo, str) else repo
    commit_sha = _sanitize(commit_sha) if isinstance(commit_sha, str) else commit_sha

    conn.execute("INSERT INTO repos (name, commit_sha) VALUES (?, ?)", (repo, commit_sha))
    conn.commit()
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if close:
        conn.close()
    return rid


def record_artifact(db, *args, **kwargs):
    """Record a provenance artifact and return its row id."""
    data = dict(kwargs)
    if args and isinstance(args[0], dict):
        data.update(args[0])
        args = args[1:]

    conn, close = _connect(db)

    repo = _get(data, "repo", "repo_name", "repo_id", "name", "repository")
    if repo is None and args:
        repo = args[0]
        args = args[1:]
    commit_sha = _get(data, "commit_sha", "commit", "revision", "sha", "commit_id")
    if commit_sha is None and args:
        commit_sha = args[0]
        args = args[1:]

    file_path = _get(data, "file_path", "file", "path", "filepath", "filename")
    line_start = _get(data, "line_start", "start", "start_line", "line_from")
    line_end = _get(data, "line_end", "end", "end_line", "line_to")
    file_range = _get(data, "file_range", "range", "lines", "line_range")
    if file_range is None:
        if file_path is not None:
            if line_start is not None and line_end is not None:
                file_range = f"{file_path}:{line_start}-{line_end}"
            elif line_start is not None:
                file_range = f"{file_path}:{line_start}"
            else:
                file_range = str(file_path)
        else:
            file_range = None

    content_hash = _get(data, "content_hash", "contenthash", "hash", "sha256", "content_sha256")
    if content_hash is None:
        content_hash = ""

    extractor = _get(data, "extractor", "extractor_name", "extractor_id")
    version = _get(data, "version", "extractor_ver", "extractor_version_num")
    extractor_version = _get(data, "extractor_version", "extractorVersion")
    if extractor_version is None:
        if extractor is not None or version is not None:
            e = "" if extractor is None else str(extractor)
            v = "" if version is None else str(version)
            extractor_version = f"{e}/{v}".strip("/")
        else:
            extractor_version = None

    run_id = _get(data, "run_id", "run", "runId", "run_number")
    timestamp = _get(data, "timestamp", "ts", "time", "created_at", "recorded_at")
    run_timestamp = _get(data, "run_timestamp", "run_ts")
    if run_timestamp is None:
        if run_id is not None and timestamp is not None:
            run_timestamp = f"{run_id}@{timestamp}"
        elif timestamp is not None:
            run_timestamp = str(timestamp)
        elif run_id is not None:
            run_timestamp = str(run_id)

    origin = _get(data, "origin", "origin_type")
    status = _get(data, "status", "state")

    if origin is not None and origin not in ORIGINS:
        raise ValueError(f"invalid origin: {origin!r}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"invalid status: {status!r}")

    confidence = _get(data, "confidence", "score")
    basis = _get(data, "basis", "reason", "rationale")
    confidence_basis = _get(data, "confidence_basis", "confidenceBasis")
    if confidence_basis is None:
        if confidence is not None and basis is not None:
            confidence_basis = f"{confidence}:{basis}"
        elif confidence is not None:
            confidence_basis = str(confidence)
        elif basis is not None:
            confidence_basis = str(basis)

    excerpt = _get(data, "excerpt", "text", "snippet", "content")
    redacted = _get(data, "redacted", "redact", "is_redacted")

    # sanitize all strings
    repo = _sanitize(repo) if isinstance(repo, str) else repo
    commit_sha = _sanitize(commit_sha) if isinstance(commit_sha, str) else commit_sha
    file_range = _sanitize(file_range) if isinstance(file_range, str) else file_range
    content_hash = _sanitize(content_hash) if isinstance(content_hash, str) else content_hash
    extractor_version = _sanitize(extractor_version) if isinstance(extractor_version, str) else extractor_version
    run_timestamp = _sanitize(run_timestamp) if isinstance(run_timestamp, str) else run_timestamp
    origin = _sanitize(origin) if isinstance(origin, str) else origin
    status = _sanitize(status) if isinstance(status, str) else status
    confidence_basis = _sanitize(confidence_basis) if isinstance(confidence_basis, str) else confidence_basis
    if isinstance(excerpt, str):
        excerpt = _sanitize(excerpt)
        if redacted is True or redacted == 1:
            excerpt = "[REDACTED]"

    if not isinstance(content_hash, str) or content_hash == "":
        if isinstance(excerpt, str):
            content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        else:
            content_hash = hashlib.sha256(b"").hexdigest()

    conn.execute(
        """INSERT INTO provenance
           (repo, commit_sha, file_range, content_hash, extractor_version,
            run_timestamp, origin, status, confidence_basis, excerpt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (repo, commit_sha, file_range, content_hash, extractor_version,
         run_timestamp, origin, status, confidence_basis, excerpt),
    )
    conn.commit()
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if close:
        conn.close()
    return rid