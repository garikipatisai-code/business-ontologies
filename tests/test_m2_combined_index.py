"""M3 combined index retention: deterministic index keeps both state machines."""

from pathlib import Path

from bo_extract.deterministic import extract

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _get(obj, *keys, default=None):
    for k in keys:
        if isinstance(obj, dict) and k in obj and obj[k] is not None:
            return obj[k]
        if hasattr(obj, k):
            return getattr(obj, k)
    return default


def _iter(obj):
    return obj if isinstance(obj, list) else []


def _transitions(edges):
    return [
        e
        for e in _iter(edges)
        if str(_get(e, "kind", "relation", "type", "")).lower() == "transition"
    ]


def test_combined_index_retains_refund_and_points_state_machines():
    """Combined index retains BOTH refund and points state machines (no behavior removed)."""
    nodes, edges, rules = extract(FIXTURES)
    trans = _transitions(edges)
    triggers = {
        str((_get(e, "trigger") or {}).get("symbol") or "") for e in trans
    }
    assert "RefundStateMachine.transition" in triggers, triggers
    assert "PointsStateMachine.transition" in triggers, triggers
