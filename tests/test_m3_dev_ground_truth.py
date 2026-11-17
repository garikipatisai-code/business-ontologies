"""M3 independent ground truth for the loyalty-points ontology (A1).

Authoritative human-labeled expectations read from the T3a fixtures (repo-d).
Self-contained by design: NO src imports here, so these labels cannot be
tuned by the extractor implementation (independent ground truth, A1).

Behavior names + one assertion focus per test. Label constants asserted
directly (expected threshold, boundary outcomes, prereqs, adjust, event
construction vs publication, misleading comment, similar names, constants).
"""

import ast
import operator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

EXPECTED_POINTS_THRESHOLD = 100
EXPECTED_POINTS_DISPLAY_LABEL = "100 points (help-text label, not the policy)"


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


def _assign_value(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"assign {name} not found in module")


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
    raise AssertionError(f"attr {name} not found on {cls.name}")


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


# ---------------------------------------------------------------- threshold label


def test_points_threshold_default_label_is_100_points():
    """Ground truth: configurable points threshold default label is 100 points."""
    rules = _parse("repo-d/rules.py")
    consts = _module_constants(rules)
    assert consts.get("POINTS_THRESHOLD") == EXPECTED_POINTS_THRESHOLD
    balance = _class(rules, "PointsBalance")
    assert _attr(balance, "points_threshold") == EXPECTED_POINTS_THRESHOLD


def test_points_threshold_is_configurable_via_threshold_override():
    """Threshold resolution: per-account threshold override with module default."""
    rules = _parse("repo-d/rules.py")
    fn = _method(_class(rules, "PointsEligibility"), "isEligible")
    assigns = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "threshold" for t in n.targets)
    ]
    assert len(assigns) == 1
    src = ast.unparse(assigns[0].value)
    assert "threshold" in src
    assert "POINTS_THRESHOLD" in src
    assert _module_constants(rules)["POINTS_THRESHOLD"] == EXPECTED_POINTS_THRESHOLD


def test_points_threshold_boundary_cases_99_100_101():
    """Boundary outcomes: points 99/100 eligible, points 101 ineligible (threshold 100)."""
    rules = _parse("repo-d/rules.py")
    fn = _method(_class(rules, "PointsEligibility"), "isEligible")
    compares = _comparisons(fn)
    assert len(compares) == 1, compares
    cmp = compares[0]
    assert [type(o) for o in cmp.ops] == [ast.LtE]
    assert cmp.left.id == "points"
    assert cmp.comparators[0].id == "threshold"
    pred = _CMP[type(cmp.ops[0])]
    threshold = _module_constants(rules)["POINTS_THRESHOLD"]
    assert threshold == EXPECTED_POINTS_THRESHOLD
    assert pred(99, threshold) is True
    assert pred(100, threshold) is True
    assert pred(101, threshold) is False


def test_points_eligibility_outcomes_eligible_then_ineligible():
    """Eligible for threshold points, ineligible for above-threshold points."""
    rules = _parse("repo-d/rules.py")
    fn = _method(_class(rules, "PointsEligibility"), "isEligible")
    compares = _comparisons(fn)
    pred = _CMP[type(compares[0].ops[0])]
    assert pred(50, EXPECTED_POINTS_THRESHOLD) is True
    assert pred(150, EXPECTED_POINTS_THRESHOLD) is False


# ---------------------------------------------------------------- adjust


def test_manager_adjust_requires_reason_and_adjusts():
    """Manager adjust is an exception path: requires a recorded reason."""
    rules = _parse("repo-d/rules.py")
    fn = _method(_class(rules, "PointsEligibility"), "isEligible")
    adjusts = [i for i in _ifs(fn) if "manager_adjust" in _names_in(i.test)]
    assert len(adjusts) == 1, adjusts
    cond = ast.unparse(adjusts[0].test)
    assert "manager_adjust" in cond
    assert "reason" in cond
    assert " and " in cond
    ret = adjusts[0].body[0]
    assert isinstance(ret, ast.Return)
    assert isinstance(ret.value, ast.Constant)
    assert ret.value.value is True


def test_manager_adjust_without_reason_uses_threshold_boundary():
    """Without an adjust reason the normal threshold boundary decides."""
    rules = _parse("repo-d/rules.py")
    fn = _method(_class(rules, "PointsEligibility"), "isEligible")
    adjusts = [i for i in _ifs(fn) if "manager_adjust" in _names_in(i.test)]
    assert len(adjusts) == 1
    assert "reason" in ast.unparse(adjusts[0].test)
    assert len(_comparisons(fn)) == 1


# ---------------------------------------------------------------- status prereqs


def test_points_status_prereqs_guard_points_transaction():
    """Status prereqs (ACCOUNT_ACTIVE) gate the points transaction."""
    app = _parse("repo-d/app.py")
    fn = _method(_class(app, "PointsTransaction"), "processTransaction")
    checks = [i for i in _ifs(fn) if "status" in ast.unparse(i.test)]
    assert len(checks) == 1, checks
    names = set()
    for c in checks:
        names |= _names_in(c.test)
    assert "ACCOUNT_ACTIVE" in names
    for c in checks:
        ret = c.body[0]
        assert isinstance(ret, ast.Return)
        assert isinstance(ret.value, ast.Constant)
        assert ret.value.value is None


# ---------------------------------------------------------------- transition


def test_points_transition_guarded_to_points_updated():
    """Points transition is guarded and targets POINTS_UPDATED."""
    app = _parse("repo-d/app.py")
    fn = _method(_class(app, "PointsTransaction"), "processTransaction")
    calls = _call_dotted_names(fn)
    assert "PointsStateMachine.transition" in calls
    trans = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and _dotted(n.func) == "PointsStateMachine.transition"
    ]
    assert len(trans) == 1
    kw = {k.arg: k.value for k in trans[0].keywords}
    assert kw.get("to_state") is not None
    assert ast.literal_eval(kw["to_state"]) == "POINTS_UPDATED"


def test_points_transition_guard_requires_eligibility_and_status():
    """Transition only fires after status prereqs + eligibility."""
    app = _parse("repo-d/app.py")
    fn = _method(_class(app, "PointsTransaction"), "processTransaction")
    calls = _call_dotted_names(fn)
    assert "eligibility.isEligible" in calls
    assert "PointsStateMachine.transition" in calls


# ---------------------------------------------------------------- events


def test_points_event_construction_present_publication_absent():
    """Event construction site present; no publication surface."""
    app = _parse("repo-d/app.py")
    fn = _method(_class(app, "PointsTransaction"), "processTransaction")
    calls = _call_dotted_names(fn)
    assert "build_points_transaction" in calls, calls
    for c in calls:
        assert c.split(".")[0] not in {"publish", "emit", "dispatch", "send", "broadcast", "fire"}, c


def test_points_event_schema_includes_earn_redeem_expire():
    """EVENT_SCHEMA declares earn/redeem/expire transaction event types."""
    contracts = _parse("repo-d/contracts.py")
    schema = _assign_value(contracts, "EVENT_SCHEMA")
    assert isinstance(schema, dict)
    entry = schema.get("points_transaction")
    assert isinstance(entry, dict)
    fields = {f.get("name"): f for f in entry.get("fields") or []}
    txn_type = fields.get("transaction_type")
    assert isinstance(txn_type, dict)
    assert set(txn_type.get("values") or []) == {"earn", "redeem", "expire"}


def test_points_event_declaration_separates_construction_from_publication():
    """Declaration docstring separates construction (not publication)."""
    app = _parse("repo-d/app.py")
    tx = _class(app, "PointsTransaction")
    doc = ast.get_docstring(tx) or ""
    lowered = doc.lower()
    assert "event" in lowered
    assert "publish" not in lowered
    assert "emit" not in lowered
    assert "dispatch" not in lowered
    assert "send" not in lowered


# ---------------------------------------------------------------- misleading comment


def test_misleading_comment_is_inert_not_executable():
    """MISLEADING comment is inert text; executable flow uses real checks."""
    app_path = FIXTURES / "repo-d" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    assert "# MISLEADING COMMENT" in src
    app = _parse("repo-d/app.py")
    fn = _method(_class(app, "PointsTransaction"), "processTransaction")
    names = _names_in(fn)
    assert "ACCOUNT_ACTIVE" in names


# ---------------------------------------------------------------- similar names / M3 edge


def test_similar_constant_points_display_label_excluded_from_policy():
    """POINTS_DISPLAY_LABEL is a help-text label, not the policy value."""
    consts = _module_constants(_parse("repo-d/rules.py"))
    display = consts.get("POINTS_DISPLAY_LABEL")
    assert isinstance(display, str)
    assert "100" in display
    assert display != EXPECTED_POINTS_THRESHOLD


# ---------------------------------------------------------------- contracts constants


def test_points_window_size_constant_exists_vs_refund_window():
    """POINTS_WINDOW_SIZE is points expiry window, different from REFUND_WINDOW_SIZE."""
    consts = _module_constants(_parse("repo-d/contracts.py"))
    points_window = consts.get("POINTS_WINDOW_SIZE")
    assert points_window == 7
    refund_window = consts.get("REFUND_WINDOW_SIZE")
    assert refund_window == 30
    assert refund_window != points_window


def test_different_name_related_meaning_constants_exist():
    """POINTS_EXPIRY_PERIOD and REFUND_ELIGIBILITY_WINDOW are related but different."""
    consts = _module_constants(_parse("repo-d/contracts.py"))
    expiry = consts.get("POINTS_EXPIRY_PERIOD")
    assert expiry == "7 days"
    refund_window = consts.get("REFUND_ELIGIBILITY_WINDOW")
    assert refund_window == "30 days"


# ---------------------------------------------------------------- contracts


def test_points_balance_client_exists_with_get_balance():
    """PointsBalanceClient stub exposes the balance lookup used by sync."""
    contracts = _parse("repo-d/contracts.py")
    client = _class(contracts, "PointsBalanceClient")
    get_balance = _method(client, "get_balance")
    rets = [n for n in ast.walk(get_balance) if isinstance(n, ast.Return)]
    assert len(rets) == 1
    assert isinstance(rets[0].value, ast.Dict)


def test_points_balance_server_exists_with_credit_points():
    """PointsBalanceServer stub exposes the credit operation used by sync."""
    contracts = _parse("repo-d/contracts.py")
    server = _class(contracts, "PointsBalanceServer")
    credit_points = _method(server, "credit_points")
    rets = [n for n in ast.walk(credit_points) if isinstance(n, ast.Return)]
    assert len(rets) == 1
    assert isinstance(rets[0].value, ast.Dict)


def test_points_balance_sync_points_balance_present():
    """sync_points_balance pairs client balance lookup with server credit."""
    contracts = _parse("repo-d/contracts.py")
    fn = _function(contracts, "sync_points_balance")
    calls = _call_dotted_names(fn)
    assert "points_balance_client.get_balance" in calls, calls
    assert "points_balance_server.credit_points" in calls, calls


def test_points_shared_tables_constants_exist():
    """Shared table name constants map logical names to stored tables."""
    contracts = _parse("repo-d/contracts.py")
    tables = _assign_value(contracts, "SHARED_TABLES")
    assert isinstance(tables, dict)
    assert tables.get("account_points") == "accounts_points"
    assert tables.get("transaction_log") == "points_transactions"
    assert tables.get("balance_history") == "points_balance_history"