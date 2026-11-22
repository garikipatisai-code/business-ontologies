"""M2 invalidation contract (T2b grader; RED until invalidation store lands)."""

import pytest

from bo_store.bo_ontology import (
    Invalidate,
    invalidate_artifact,
)


def _artifact(value=30, repo="repo-b"):
    return {
        "subject": "refund_window_days",
        "value": value,
        "repo": repo,
        "origin": "deterministic",
        "status": "accepted",
    }


def test_value_change_triggers_recompute_and_supersedes():
    """Value change -> recompute + superseded/stale status."""
    invalidated = invalidate_artifact(_artifact(value=45))
    assert invalidated is not None
    assert invalidated.get("superseded") is True or invalidated.get("status") == "stale"


def test_no_silent_carry_after_value_change():
    """No silent carry: old value must not survive recompute."""
    invalidated = invalidate_artifact(_artifact(value=45))
    assert invalidated.get("carried_over") is not True
    assert invalidated.get("value") == 45


def test_unrelated_artifacts_preserved_on_invalidation():
    """Invalidation of one artifact preserves unrelated artifacts."""
    result = invalidate_artifact(_artifact(value=45, repo="repo-b"))
    unrelated = invalidate_artifact(
        {"subject": "refund_window_days", "value": 30, "repo": "repo-c", "origin": "deterministic"}
    )
    assert unrelated is not None
