"""M3B protocol plumbing tests (B1): provider-neutral plumbing,
validation, budget caps, artifact ledger, stored prompts.

Mock-only: zero egress, no creds, no network, no private data. Live mode is
blocked unless an auth record is present; unauthorized live raises
BlockedError with no fallback and no network touch.

RED: M3B plumbing does not exist yet (src/bo_extract/semantic_adapter is
mock-only M2). Every test below references M3B interface names that are not
implemented, so each fails with AttributeError/ImportError until the
implementer lands the plumbing.
"""

from pathlib import Path

import pytest

from bo_extract import semantic_adapter as _semantic

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts" / "m3b"

PROMPT_NAMES = [
    "concept-extraction",
    "relation-extraction",
    "citation-grounding",
    "abstention-policy",
]


# ---------------------------------------------------------------- mode gate


def test_default_mode_is_mock_egress_disabled():
    """Default mode is mock: egress disabled, no live opt-in by default."""
    assert _semantic.EGRESS_ENABLED is False
    assert _semantic._is_live() is False


def test_live_without_auth_record_raises_blocked_error():
    """Live mode without an auth record raises BlockedError: no fallback,
    never touches network."""
    _semantic.SEMANTIC_CONFIG = {"provider": "nemotron-3.5-super-vl:free", "live": True}
    try:
        with pytest.raises(_semantic.BlockedError):
            _semantic.require_live_auth()
    finally:
        _semantic.SEMANTIC_CONFIG = {}


# ----------------------------------------------------------- validation


def test_validation_rejects_malformed_structured_output():
    """Structured-output validation rejects malformed output (fail closed)."""
    malformed = {"concepts": "not-a-list", "relations": None}
    with pytest.raises(ValueError):
        _semantic.validate_structured_output(malformed)


# ------------------------------------------------------------- budget caps


def test_budget_cap_stops_after_max_calls():
    """Budget cap stops execution after N calls; no silent over-run."""
    cap = _semantic.budget_cap(max_calls=2)
    calls = 0
    for _ in range(10):
        if not cap.allow_call():
            break
        calls += 1
    assert calls == 2


def test_budget_cap_enforced_max_tokens():
    """Token budget cap stops once the token ceiling is reached."""
    cap = _semantic.budget_cap(max_tokens=100)
    assert cap.within_budget(tokens_used=50) is True
    assert cap.within_budget(tokens_used=150) is False


def test_budget_cap_enforced_max_spend():
    """Spend budget cap stops once the spend ceiling is reached."""
    cap = _semantic.budget_cap(max_spend=0.01)
    assert cap.within_budget(cost=0.005) is True
    assert cap.within_budget(cost=0.02) is False


# ------------------------------------------------------------------- ledger


def test_ledger_records_required_fields_on_mock_run():
    """Artifact ledger records model/provider/prompt-version/input-hashes/cost."""
    provider = _semantic.SemanticMockProvider()
    record = _semantic.record_ledger(
        provider=provider,
        prompt="refund window rule",
        cost=0.0,
    )
    assert record is not None
    assert record.get("model") is not None
    assert record.get("provider") is not None
    assert record.get("prompt_version") is not None
    assert record.get("input_hashes") is not None
    assert record.get("cost") is not None


# ------------------------------------------------------------ stored prompts


# CORRECTION (plan B1 end-state, SHA 82b386f9...): B1b created prompts/m3b/*.md,
# so the 4 B1a tests now contradict their own task. Each test asserts BOTH:
# (a) load_stored_prompt(<name>) succeeds, non-empty markdown mentioning
#     MOCK-ONLY/BLOCKED; AND (b) load_stored_prompt("definitely-absent-<name>")
#     raises FileNotFoundError — proving no silent fallback either way.


def test_stored_prompt_concept_extraction_exists_and_absent_raises():
    """Stored prompt loads (B1 end-state); absent name raises FileNotFoundError (no silent fallback)."""
    text = _semantic.load_stored_prompt("concept-extraction")
    assert text.strip(), "stored prompt must be non-empty"
    assert ("MOCK-ONLY" in text) or ("BLOCKED" in text)
    with pytest.raises(FileNotFoundError):
        _semantic.load_stored_prompt("definitely-absent-concept-extraction")


def test_stored_prompt_relation_extraction_exists_and_absent_raises():
    """Stored prompt loads (B1 end-state); absent name raises FileNotFoundError (no silent fallback)."""
    text = _semantic.load_stored_prompt("relation-extraction")
    assert text.strip(), "stored prompt must be non-empty"
    assert ("MOCK-ONLY" in text) or ("BLOCKED" in text)
    with pytest.raises(FileNotFoundError):
        _semantic.load_stored_prompt("definitely-absent-relation-extraction")


def test_stored_prompt_citation_grounding_exists_and_absent_raises():
    """Stored prompt loads (B1 end-state); absent name raises FileNotFoundError (no silent fallback)."""
    text = _semantic.load_stored_prompt("citation-grounding")
    assert text.strip(), "stored prompt must be non-empty"
    assert ("MOCK-ONLY" in text) or ("BLOCKED" in text)
    with pytest.raises(FileNotFoundError):
        _semantic.load_stored_prompt("definitely-absent-citation-grounding")


def test_stored_prompt_abstention_policy_exists_and_absent_raises():
    """Stored prompt loads (B1 end-state); absent name raises FileNotFoundError (no silent fallback)."""
    text = _semantic.load_stored_prompt("abstention-policy")
    assert text.strip(), "stored prompt must be non-empty"
    assert ("MOCK-ONLY" in text) or ("BLOCKED" in text)
    with pytest.raises(FileNotFoundError):
        _semantic.load_stored_prompt("definitely-absent-abstention-policy")
