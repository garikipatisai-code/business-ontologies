"""M2 security contract for deep ontology surfaces (T2b grader).

Covers M2 security surface: direct entry path confinement, repo/context
filter scoping, loopback-only binding, silent-failure abstain, origin/status
enums, redaction on new fields.
"""

import inspect

import pytest

from bo_api.server import create_app, run
from bo_store.manifest import verify_manifest
from bo_store.provenance import ORIGINS, STATUSES, redact_value
from bo_extract.deterministic import extract


def test_server_defaults_to_loopback_only():
    """Read-only API server must default to loopback binding."""
    sig = inspect.signature(run)
    assert sig.parameters["host"].default == "127.0.0.1"


def test_provenance_status_enum_rejects_unknown():
    """Unknown provenance status is rejected (strict enum)."""
    from bo_store.provenance import init_db, record_artifact

    db = ":memory:"
    with pytest.raises(ValueError):
        record_artifact(
            db,
            repo="repo-b",
            commit_sha="b" * 40,
            file_path="rules.py",
            line_start=5,
            line_end=5,
            content_hash="d" * 64,
            extractor="deterministic",
            version="1.0",
            run_id="run1",
            timestamp="2026-01-01T00:00:00Z",
            origin="deterministic",
            status="bogus-status",
            confidence=1.0,
            basis="deterministic",
            excerpt="refund_window_days = 30",
        )


def test_provenance_origin_enum_rejects_unknown():
    """Unknown provenance origin is rejected (strict enum)."""
    from bo_store.provenance import init_db, record_artifact

    db = ":memory:"
    with pytest.raises(ValueError):
        record_artifact(
            db,
            repo="repo-b",
            commit_sha="b" * 40,
            file_path="rules.py",
            line_start=5,
            line_end=5,
            content_hash="d" * 64,
            extractor="deterministic",
            version="1.0",
            run_id="run1",
            timestamp="2026-01-01T00:00:00Z",
            origin="bogus-origin",
            status="proposed",
            confidence=1.0,
            basis="deterministic",
            excerpt="refund_window_days = 30",
        )


def test_redact_value_redacts_nested_secrets():
    """Redaction applies to nested new-field payloads (fail closed)."""
    out = redact_value({"subject": "refund_window_days", "value": 30, "snippet": "sk-abc123SECRET456xx"})
    assert out["snippet"] == "[REDACTED]"


def test_manifest_confinement_rejects_path_traversal(tmp_path):
    """Manifest entry paths are confined to the workspace root."""
    m = {"workspace_root": str(tmp_path), "repos": [{"name": "x", "path": "../outside"}]}
    ok, errors = verify_manifest(m)
    assert ok is False
    assert errors


def test_read_only_api_has_no_write_verbs():
    """Read-only API registers no write verbs."""
    handler = create_app(":memory:")
    for verb in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        assert not hasattr(handler, verb)
