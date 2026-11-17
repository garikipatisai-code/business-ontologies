import json
import sqlite3
from pathlib import Path

import pytest

from bo_store.manifest import load_manifest, verify_manifest
from bo_store.provenance import init_db, record_repo, record_artifact

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_loads_two_repos():
    m = load_manifest(ROOT / "manifests" / "m1-manifest.json")
    assert isinstance(m, dict)
    assert len(m["repos"]) == 2


def test_manifest_rejects_absolute_path(tmp_path):
    m = load_manifest(ROOT / "manifests" / "m1-manifest.json")
    bad = json.loads(json.dumps(m))
    bad["repos"][0]["path"] = "/etc/passwd"
    ok, errors = verify_manifest(bad, workspace_root=tmp_path)
    assert ok is False
    assert errors


def test_manifest_rejects_symlink_escape(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = ws / "escape"
    link.symlink_to(outside)
    m = {"workspace_root": str(ws), "repos": [{"name": "x", "path": "escape"}]}
    ok, errors = verify_manifest(m)
    assert ok is False
    assert errors


def test_provenance_row_has_ten_fields_plus_id(tmp_path):
    """Provenance row has 10 data columns plus id (11 total incl id)."""
    db = tmp_path / "prov.db"
    init_db(db)
    record_repo(db, repo="repo-a", commit_sha="a" * 40)
    aid = record_artifact(
        db,
        repo="repo-a",
        commit_sha="a" * 40,
        file_path="app.py",
        line_start=1,
        line_end=2,
        content_hash="b" * 64,
        extractor="struct",
        extractor_version="1.0",
        run_id="run1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=0.9,
        basis="ast",
        excerpt="hello",
        redacted=False,
    )
    assert isinstance(aid, int)
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()]
    chosen = None
    for t in ("provenance", "artifacts", "provenance_artifacts"):
        if t in tables:
            chosen = t
            break
    assert chosen is not None
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({chosen})").fetchall()]
    assert len(cols) == 11, cols


def test_secret_not_in_manifest_or_db(tmp_path):
    m = load_manifest(ROOT / "manifests" / "m1-manifest.json")
    assert "sk-test" not in json.dumps(m)
    db = tmp_path / "prov.db"
    init_db(db)
    record_repo(db, repo="repo-a", commit_sha="a" * 40)
    record_artifact(
        db,
        repo="repo-a",
        commit_sha="a" * 40,
        file_path="app.py",
        line_start=1,
        line_end=2,
        content_hash="b" * 64,
        extractor="struct",
        extractor_version="1.0",
        run_id="run1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=0.9,
        basis="ast",
        excerpt="STRIPE_KEY=sk-test-REDACT-ME",
        redacted=True,
    )
    conn = sqlite3.connect(db)
    dump = []
    for (t,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall():
        try:
            for row in conn.execute(f"SELECT * FROM {t}"):
                dump.append(str(row))
        except Exception:
            pass
    assert "sk-test" not in " ".join(dump)


_SECRET_FORMATS = [
    ("sk-test", "sk-test-abc123SECRET"),
    ("AKIA", "AKIAIOSFODNN7EXAMPLE"),
    ("ghp_", "ghp_abcdefghijklmnopqrstuv"),
    ("xoxb-", "xoxb-1234567890-ABCDEFGH"),
    ("BEGIN PRIVATE KEY", "-----BEGIN PRIVATE KEY-----"),
]


def _make_artifact(db, excerpt, content_hash="c" * 64):
    record_artifact(
        db,
        repo="repo-a",
        commit_sha="a" * 40,
        file_path="app.py",
        line_start=1,
        line_end=1,
        content_hash=content_hash,
        extractor="struct",
        version="1.0",
        run_id="r1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=0.9,
        basis="ast",
        excerpt=excerpt,
    )
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT excerpt FROM provenance WHERE id=1").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


@pytest.mark.parametrize("label,secret", _SECRET_FORMATS)
def test_secret_format_redacted(tmp_path, label, secret):
    """All 5 secret formats are redacted before INSERT (canaries, no real secrets)."""
    db = tmp_path / "prov.db"
    init_db(db)
    excerpt = _make_artifact(db, f"token={secret}")
    assert excerpt == "[REDACTED]", excerpt
    assert secret not in excerpt


def test_entropy_fallback_redacts_high_entropy_token(tmp_path):
    """High-entropy non-hex token (no named pattern) is redacted by entropy fallback."""
    db = tmp_path / "prov.db"
    init_db(db)
    token = "aB3xK9mP2vR8wLq1N4jT7zW5eRmPq"
    excerpt = _make_artifact(db, f"token={token}")
    assert excerpt == "[REDACTED]", excerpt
    assert token not in excerpt


def test_hex_digest_not_redacted(tmp_path):
    """A pure hex digest (content hash / commit SHA) is not touched by the entropy fallback."""
    db = tmp_path / "prov.db"
    init_db(db)
    digest = "c" * 64
    excerpt = _make_artifact(db, digest, content_hash=digest)
    assert excerpt == digest, excerpt
