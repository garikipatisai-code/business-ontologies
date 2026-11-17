import ast
from pathlib import Path

import pytest

from bo_extract.deterministic import extract, abstain


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _as_iter(x):
    if x is None:
        return []
    return list(x)


def _get(obj, *keys, default=None):
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            return obj[k]
        if hasattr(obj, k):
            return getattr(obj, k)
    return default


def _kind(node):
    return _get(node, "kind", "type", "node_type", "node_kind")


def _name(node):
    return _get(node, "name", "id", "label", "symbol")


def test_extract_returns_nodes_edges_rules_shape():
    """extract(repo)->(nodes, edges, rules) shape using stdlib ast only, no import/exec of fixture code."""
    result = extract(FIXTURES)
    # tuple/list shape: (nodes, edges, rules)
    assert isinstance(result, (tuple, list))
    assert len(result) == 3
    nodes, edges, rules = result
    assert isinstance(_as_iter(nodes), list)
    assert isinstance(_as_iter(edges), list)
    assert isinstance(_as_iter(rules), list)


def test_structural_nodes_found():
    """Structural: Module/Class/Function/Call nodes found for key symbols."""
    nodes, edges, rules = extract(FIXTURES)
    items = _as_iter(nodes) + _as_iter(edges)

    kinds = {_kind(n) for n in items if _kind(n) is not None}
    names = {_name(n) for n in items if _name(n) is not None}
    # normalize kinds to lowercase strings for matching
    kind_strs = {str(k).lower() for k in kinds}

    assert any("module" in k for k in kind_strs), f"Module node missing, kinds={kinds}"
    assert any("class" in k for k in kind_strs), f"Class node missing, kinds={kinds}"
    assert any("function" in k or "def" in k or "method" in k for k in kind_strs), (
        f"Function node missing, kinds={kinds}"
    )
    assert any("call" in k for k in kind_strs), f"Call node missing, kinds={kinds}"

    for expected in ("OrderService", "createOrder", "RefundPolicy", "applyWindow"):
        assert expected in names, f"Expected symbol {expected!r} not found, names={sorted(n for n in names if n)}"


def test_exactly_one_refund_window_rule_deterministic_with_evidence():
    """Exactly 1 Rule refund_window_days=30 from rules.py with origin deterministic, file+line evidence."""
    nodes, edges, rules = extract(FIXTURES)
    rules_list = _as_iter(rules)

    refund_rules = [
        r
        for r in rules_list
        if _get(r, "name", "rule", "key", "symbol") == "refund_window_days"
        or _get(r, "value", "rule_value") == 30
        or _get(r, "refund_window_days") == 30
    ]
    assert len(refund_rules) == 1, f"Expected exactly 1 refund_window_days rule, got {refund_rules!r}"
    rule = refund_rules[0]

    # value 30
    value = _get(rule, "value", "rule_value", "refund_window_days")
    assert value == 30, f"Expected refund_window_days value 30, got {value!r}"

    # origin deterministic
    origin = _get(rule, "origin")
    assert str(origin).lower() == "deterministic", f"Expected origin deterministic, got {origin!r}"

    # evidence from rules.py with file+line
    evidence = _get(rule, "evidence", "provenance", "source", "location")
    ev_file = _get(rule, "file", "file_path", "path")
    ev_line = _get(rule, "line", "line_start", "lineno")
    if evidence is not None:
        ev_file = ev_file or _get(evidence, "file", "file_path", "path")
        ev_line = ev_line or _get(evidence, "line", "line_start", "lineno", "start_line")
    assert ev_file is not None and "rules.py" in str(ev_file), (
        f"Expected evidence file rules.py, got file={ev_file!r} evidence={evidence!r}"
    )
    assert ev_line is not None and int(ev_line) > 0, (
        f"Expected evidence line number, got line={ev_line!r} evidence={evidence!r}"
    )


def test_abstain_on_empty_dir_returns_no_evidence(tmp_path):
    """abstain() on empty dir returns status abstain/no-evidence."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = abstain(empty)

    status = _get(result, "status")
    reason = _get(result, "reason", "message", "detail", "evidence")
    text = f"{status} {reason} {result}".lower()
    assert status is not None and str(status).lower() == "abstain", (
        f"Expected status abstain, got {status!r} result={result!r}"
    )
    assert "no-evidence" in text or "no evidence" in text or "no_evidence" in text, (
        f"Expected no-evidence indication, got {result!r}"
    )


def test_prompt_injection_comment_not_executable_instruction():
    """Prompt-injection comment does not become executable instruction (treated as text)."""
    # must not import/exec fixture code; extraction should be safe and structural
    nodes, edges, rules = extract(FIXTURES)
    all_items = _as_iter(nodes) + _as_iter(edges) + _as_iter(rules)

    injection_phrases = [
        "ignore previous instructions",
        "exfiltrate secrets",
        "prompt injection",
    ]

    # no extracted artifact should represent the injection comment as an instruction/command/action
    for item in all_items:
        kind = str(_get(item, "kind", "type", "node_type", "")).lower()
        assert "instruction" not in kind and "command" not in kind and "exec" not in kind, (
            f"Injection text became executable instruction node: {item!r}"
        )
        blob = str(item).lower()
        # presence as plain extracted executable payload would be bad; allow absence or only as inert text metadata
        # but contract states "does not become executable instruction" - ensure no rule/edge encodes it
        if isinstance(item, dict):
            assert item.get("executable") is not True

    # specifically rules must only contain the deterministic business rule
    rules_list = _as_iter(rules)
    for r in rules_list:
        rblob = str(r).lower()
        for phrase in injection_phrases:
            assert phrase not in rblob, f"Injection phrase leaked into rule: {phrase!r} in {r!r}"
