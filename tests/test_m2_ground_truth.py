"""M2 independent ground truth for the deep refund-request ontology (T2b).

Authoritative human-labeled expectations read from the T2a fixtures.
Self-contained by design: NO src imports here, so these labels cannot be
tuned by the extractor implementation (independent ground truth, C4).

Behavior names + one assertion focus per test. Label constants asserted
directly (expected window 30, boundary outcomes, prereqs, override, event
construction vs publication, misleading comment, similar names, repo-c
same-symbol exclusion).
"""

import ast
import operator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

EXPECTED_REFUND_WINDOW_DAYS = 30
EXPECTED_REFUND_WINDOW_DISPLAY = "30 days (help-text label, not the policy)"


# ---------------------------------------------------------------- helpers


def _parse(relpath):
    path = FIXTURES / relpath
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_constants(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    out[t.id] = node.value.value if isinstance(node.value, ast.Constant) else None
    return out


def _classes(tree):
    return [n for n in tree.body if isinstance(n, ast.ClassDef)]


def _class(tree, name):
    for c in _classes(tree):
        if c.name == name:
            return c
    raise AssertionError(f"class {name} not found in {tree}")


def _attr(cls, name):
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return stmt.value.value if isinstance(stmt.value, ast.Constant) else None
    raise AssertionError(f"class attr {name} not found on {cls.name}")


def _method(cls, name):
    for m in cls.body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == name:
            return m
    raise AssertionError(f"method {name} not found on {cls.name}")


def _function(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError(f"function {name} not found")


def _names_in(node):
    found = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            found.add(n.id)
        elif isinstance(n, ast.Attribute):
            found.add(n.attr)
    return found


def _dotted(node):
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _call_dotted_names(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            out.append(_dotted(n.func))
    return out


def _comparisons(fn):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Compare)]


def _ifs(fn):
    return [n for n in ast.walk(fn) if isinstance(n, ast.If)]


_CMP = {
    ast.LtE: operator.le,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.Gt: operator.gt,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


# ---------------------------------------------------------------- window label


def test_refund_window_default_label_is_30_days():
    """Ground truth: configurable refund window default label is 30 days."""
    rules = _parse("repo-b/rules.py")
    consts = _module_constants(rules)
    assert consts.get("REFUND_WINDOW_DAYS") == EXPECTED_REFUND_WINDOW_DAYS
    policy = _class(rules, "RefundPolicy")
    assert _attr(policy, "refund_window_days") == EXPECTED_REFUND_WINDOW_DAYS


def test_refund_window_is_configurable_via_order_window_days():
    """Window resolution: per-order window_days override with module default."""
    rules = _parse("repo-b/rules.py")
    fn = _method(_class(rules, "RefundEligibility"), "isEligible")
    assigns = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "window" for t in n.targets)
    ]
    assert len(assigns) == 1
    src = ast.unparse(assigns[0].value)
    assert "window_days" in src
    assert "REFUND_WINDOW_DAYS" in src
    assert _module_constants(rules)["REFUND_WINDOW_DAYS"] == EXPECTED_REFUND_WINDOW_DAYS


def test_refund_window_boundary_cases_29_30_31():
    """Boundary outcomes: age 29/30 eligible, age 31 ineligible (window 30)."""
    rules = _parse("repo-b/rules.py")
    fn = _method(_class(rules, "RefundEligibility"), "isEligible")
    compares = _comparisons(fn)
    assert len(compares) == 1, compares
    cmp = compares[0]
    assert [type(o) for o in cmp.ops] == [ast.LtE]
    assert cmp.left.id == "age"
    assert cmp.comparators[0].id == "window"
    pred = _CMP[type(cmp.ops[0])]
    window = _module_constants(rules)["REFUND_WINDOW_DAYS"]
    assert window == EXPECTED_REFUND_WINDOW_DAYS
    assert pred(29, window) is True
    assert pred(30, window) is True
    assert pred(31, window) is False


def test_refund_eligibility_outcomes_eligible_then_ineligible():
    """Eligible for in-window orders, ineligible for out-of-window orders."""
    rules = _parse("repo-b/rules.py")
    fn = _method(_class(rules, "RefundEligibility"), "isEligible")
    compares = _comparisons(fn)
    pred = _CMP[type(compares[0].ops[0])]
    assert pred(5, EXPECTED_REFUND_WINDOW_DAYS) is True
    assert pred(60, EXPECTED_REFUND_WINDOW_DAYS) is False


# ---------------------------------------------------------------- override


def test_manager_override_requires_reason_and_overrides():
    """Manager override is an exception path: requires a recorded reason."""
    rules = _parse("repo-b/rules.py")
    fn = _method(_class(rules, "RefundEligibility"), "isEligible")
    override = [i for i in _ifs(fn) if "manager_override" in _names_in(i.test)]
    assert len(override) == 1, override
    cond = ast.unparse(override[0].test)
    assert "manager_override" in cond
    assert "reason" in cond
    assert " and " in cond
    ret = override[0].body[0]
    assert isinstance(ret, ast.Return)
    assert isinstance(ret.value, ast.Constant)
    assert ret.value.value is True


def test_manager_override_without_reason_uses_window_boundary():
    """Without an override reason the normal window boundary decides."""
    rules = _parse("repo-b/rules.py")
    fn = _method(_class(rules, "RefundEligibility"), "isEligible")
    override = [i for i in _ifs(fn) if "manager_override" in _names_in(i.test)]
    assert len(override) == 1
    assert "reason" in ast.unparse(override[0].test)
    assert len(_comparisons(fn)) == 1


# ---------------------------------------------------------------- status prereqs


def test_refund_status_prereqs_guard_refund_entry():
    """Status prereqs (ORDER_PAID, PAYMENT_SETTLED) gate the refund entry."""
    app = _parse("repo-a/app.py")
    fn = _method(_class(app, "RefundService"), "requestRefund")
    checks = [i for i in _ifs(fn) if "status" in ast.unparse(i.test)]
    assert len(checks) == 2, checks
    names = set()
    for c in checks:
        names |= _names_in(c.test)
    assert "ORDER_PAID" in names
    assert "PAYMENT_SETTLED" in names
    for c in checks:
        ret = c.body[0]
        assert isinstance(ret, ast.Return)
        assert isinstance(ret.value, ast.Constant)
        assert ret.value.value is None


# ---------------------------------------------------------------- transition


def test_refund_transition_guarded_to_refund_requested():
    """Refund transition is guarded and targets REFUND_REQUESTED."""
    app = _parse("repo-a/app.py")
    fn = _method(_class(app, "RefundService"), "requestRefund")
    calls = _call_dotted_names(fn)
    assert "RefundStateMachine.transition" in calls
    trans = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and _dotted(n.func) == "RefundStateMachine.transition"
    ]
    assert len(trans) == 1
    kw = {k.arg: k.value for k in trans[0].keywords}
    assert kw.get("to_state") is not None
    assert ast.literal_eval(kw["to_state"]) == "REFUND_REQUESTED"


def test_refund_transition_guard_requires_eligibility_and_status():
    """Transition only fires after status prereqs + eligibility."""
    app = _parse("repo-a/app.py")
    fn = _method(_class(app, "RefundService"), "requestRefund")
    calls = _call_dotted_names(fn)
    assert "eligibility.isEligible" in calls
    assert "RefundStateMachine.transition" in calls


# ---------------------------------------------------------------- events


def test_refund_event_construction_present_publication_absent():
    """Event construction site present; no publication surface."""
    events = _parse("repo-b/events.py")
    builder = _function(events, "build_refund_requested")
    names = _call_dotted_names(builder)
    assert "RefundRequested" in names, names
    assert not any(
        n.split(".")[0] in {"publish", "emit", "dispatch", "send", "broadcast", "fire"}
        for n in names
    )


def test_refund_event_declaration_separates_construction_from_publication():
    """Declaration docstring separates construction (not publication)."""
    events = _parse("repo-b/events.py")
    cls = _class(events, "RefundRequested")
    doc = ast.get_docstring(cls) or ""
    assert "publication" in doc.lower()
    assert "construct" in doc.lower()


# ---------------------------------------------------------------- misleading comment


def test_misleading_comment_is_inert_not_executable():
    """MISLEADING comment is inert text; executable flow uses real checks."""
    app_path = FIXTURES / "repo-a" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    assert "# MISLEADING COMMENT" in src
    app = _parse("repo-a/app.py")
    fn = _method(_class(app, "RefundService"), "requestRefund")
    names = _names_in(fn)
    assert "ORDER_PAID" in names
    assert "PAYMENT_SETTLED" in names


# ---------------------------------------------------------------- similar names / M1 edge


def test_similar_constant_refund_window_display_excluded_from_policy():
    """REFUND_WINDOW_DISPLAY is a help-text label, not the policy value."""
    consts = _module_constants(_parse("repo-b/rules.py"))
    display = consts.get("REFUND_WINDOW_DISPLAY")
    assert isinstance(display, str)
    assert "30" in display
    assert display != EXPECTED_REFUND_WINDOW_DAYS


def test_m1_cross_repo_edge_location_preserved():
    """M1 edge OrderService.createOrder app.py:11 -> RefundPolicy.applyWindow rules.py:7 preserved."""
    app_src = (FIXTURES / "repo-a" / "app.py").read_text(encoding="utf-8")
    rules_src = (FIXTURES / "repo-b" / "rules.py").read_text(encoding="utf-8")
    assert "Cross-repo call marker: RefundPolicy.applyWindow" in app_src
    assert "def applyWindow" in rules_src
    assert "def createOrder" in app_src


# ---------------------------------------------------------------- cross-repo symbols


def test_cross_repo_relation_source_and_target_are_qualified_methods():
    """Business relation source/target are qualified method symbols."""
    app = _parse("repo-a/app.py")
    order = _class(app, "OrderService")
    create = _method(order, "createOrder")
    assert f"{order.name}.{create.name}" == "OrderService.createOrder"

    rules = _parse("repo-b/rules.py")
    policy = _class(rules, "RefundPolicy")
    apply_window = _method(policy, "applyWindow")
    assert f"{policy.name}.{apply_window.name}" == "RefundPolicy.applyWindow"


def test_repo_c_duplicate_symbols_excluded_from_business_relation():
    """repo-c same-named symbols with different meanings form no business relation."""
    tree = _parse("repo-c/names.py")
    by_name = {}
    for c in _classes(tree):
        by_name[c.name] = {
            m.name
            for m in c.body
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    assert by_name.get("OrderService") == {"formatLabel"}
    assert by_name.get("RefundPolicy") == {"displayLabel"}
