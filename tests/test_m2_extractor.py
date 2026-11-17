"""M2 frozen extractor contract for deep ground truth (T2b grader).

Imports src ONLY in this file (ground-truth file must not import the extractor).

Frozen expectations for the deterministic deep-extraction contract:
rule deep fields, transition records, event split, misleading comments,
similar names, call-edge evidence-only (C5). RED until the T2 implementation lands.
"""

from pathlib import Path

from bo_extract.deterministic import extract, abstain

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


def _rules_named(rules, name):
    return [r for r in _iter(rules) if str(_get(r, "name", "rule", "key", "symbol")) == name]


def test_refund_window_rule_declares_deep_ground_truth_fields():
    """Refund window rule must carry deep ground truth fields."""
    nodes, edges, rules = extract(FIXTURES)
    refund = _rules_named(rules, "refund_window_days")
    assert len(refund) == 1, refund
    rule = refund[0]
    assert _get(rule, "value", "rule_value") == 30
    for field in ("statement", "preconditions", "predicate", "params", "exceptions", "outcomes"):
        assert field in rule, f"deep ground truth rule missing field {field!r}"
        assert rule.get(field) is not None, f"deep ground truth rule field {field!r} is None"


def test_refund_window_rule_location_evidence_and_confidence():
    """Refund window rule carries file+line evidence and confidence 1.0."""
    nodes, edges, rules = extract(FIXTURES)
    refund = _rules_named(rules, "refund_window_days")
    assert len(refund) == 1
    rule = refund[0]
    ev = _get(rule, "evidence", "provenance", "source", "location") or rule
    ev_file = _get(ev, "file", "file_path", "path") or _get(rule, "file", "file_path")
    ev_line = _get(ev, "line", "line_start", "lineno", "start_line") or _get(
        rule, "line", "line_start", "lineno", "start_line"
    )
    assert ev_file is not None and "rules.py" in str(ev_file)
    assert ev_line is not None and int(ev_line) > 0
    assert float(_get(rule, "confidence", "score") or 0) == 1.0


def test_refund_transition_declared_with_source_target_guard_trigger_evidence():
    """Refund transition must declare source/target/guard/trigger + evidence."""
    nodes, edges, rules = extract(FIXTURES)
    trans = [
        e
        for e in _iter(edges)
        if str(_get(e, "kind", "relation", "type", "")).lower() == "transition"
    ]
    assert len(trans) == 1, trans
    t = trans[0]
    for field in ("source", "target", "guard", "trigger", "evidence", "unknown_source"):
        assert field in t and t.get(field) is not None, f"transition missing {field}"


def test_event_split_construction_present_publication_abstain():
    """Event: declaration + construction present, publication abstains (C2)."""
    nodes, edges, rules = extract(FIXTURES)
    calls = [n for n in _iter(nodes) if _get(n, "kind") == "call"]
    names = {_get(n, "name", "id", "symbol") for n in calls}
    assert "build_refund_requested" in names, names
    pub = [
        n
        for n in calls
        if "publish" in str(_get(n, "name", "id", "symbol") or "").lower()
    ]
    assert pub == [], pub


def test_misleading_comment_not_promoted_to_instruction():
    """MISLEADING comment stays inert text; never becomes executable instruction."""
    nodes, edges, rules = extract(FIXTURES)
    blob = f"{rules}{edges}{nodes}"
    assert "always succeeds" not in str(blob).lower()


def test_similar_name_constant_excluded_from_policy_rules():
    """Similar-named display constant excluded; policy label is the real rule."""
    nodes, edges, rules = extract(FIXTURES)
    names = {_get(r, "name", "rule", "key", "symbol") for r in _iter(rules)}
    assert "refund_window_days" in names
    assert "REFUND_WINDOW_DISPLAY" not in names


def test_call_edge_preserved_as_evidence_only_c5():
    """Cross-repo call edge stays evidence only, never a published assertion."""
    nodes, edges, rules = extract(FIXTURES)
    rels = [e for e in _iter(edges) if str(_get(e, "kind", "relation", "type", "")).lower() == "invokes"]
    assert len(rels) == 1, rels
    rel = rels[0]
    assert _get(rel, "source") == "OrderService.createOrder"
    assert _get(rel, "target") == "RefundPolicy.applyWindow"
    assert _get(rel, "source_repo") == "repo-a"
    assert _get(rel, "target_repo") == "repo-b"
    assert str(_get(rel, "origin", "")).lower() == "deterministic"


def test_abstain_on_no_evidence_contract():
    """abstain() fails closed with no-evidence status."""
    result = abstain(FIXTURES / "does-not-exist")
    status = _get(result, "status")
    assert str(status).lower() == "abstain"
