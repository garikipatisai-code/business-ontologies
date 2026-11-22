"""Business-slice tests for the Java refund workflow (RED where the slice is missing).

These tests compose the refund business slice from the two pinned extractors
(``bo_java.java_adapter`` and ``bo_java.spring_sql_bindings``) over the frozen
fixtures ``java-repo-a`` + ``java-repo-b`` and assert the business-level
behavior the slice must expose:

* local concepts Refund and Order, defined from service roles, scoped to
  repo-a / repo-b;
* one enforced rule (window guard: paid-at age compared to configured
  window-days; failure outcome = rejection or false return) citing enforcement
  (guard-if) lines, not declarations;
* a guarded transition to REFUND_REQUESTED with the guard named;
* a cross-repo contract (OrderClient POST /refunds vs repo-a OpenAPI POST
  /refunds) recorded compatible and explicitly not equivalent;
* one unresolved item (dynamic hookName invoke) with its recorded reason;
* one rejected mapping (incompatible contract pair) with an insufficiency
  reason;
* epistemic labels: static and configured present, observed absent.

Tests import only bo_java modules and stdlib; fixture text is read as text
only, never imported. No composed slice records exist yet, so the rule and
transition tests fail RED on the missing slice pieces (bo_java emits no
enforced_rule or transition records); the raw-evidence tests pass where the
extractors already produce the records.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bo_java import java_adapter
from bo_java import spring_sql_bindings

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REPO_A = FIXTURES / "java-repo-a"
REPO_B = FIXTURES / "java-repo-b"
REPO_PATHS = [REPO_A, REPO_B]

REFUND_SERVICE = REPO_A / "src/main/java/com/example/refund/RefundService.java"
REFUND_CONTROLLER = REPO_A / "src/main/java/com/example/refund/RefundController.java"
REFUND_YML = REPO_A / "src/main/resources/application.yml"
REFUND_SQL = REPO_A / "src/main/resources/db/migration/V1__refund_schema.sql"
REFUND_OPENAPI = REPO_A / "openapi.yaml"

ORDER_SERVICE = REPO_B / "src/main/java/com/example/orders/OrderService.java"
ORDER_CLIENT = REPO_B / "src/main/java/com/example/orders/OrderClient.java"
ORDER_OPENAPI = REPO_B / "openapi.yaml"


# ---------------------------------------------------------------- helpers


def _fresh_adapter():
    adapter = java_adapter.JavaAdapter(REPO_PATHS)
    adapter.index()
    return adapter


@pytest.fixture(scope="module")
def adapter():
    return _fresh_adapter()


@pytest.fixture(scope="module")
def bindings():
    return spring_sql_bindings.index(REPO_PATHS)


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def _raw_lines(path):
    return path.read_bytes().split(b"\n")


def _same_file(reported, expected):
    return Path(reported).resolve() == expected.resolve()


def _line_number(path, needle):
    for number, line in enumerate(_lines(path), start=1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not found in {path}")


def _all_records(bindings):
    flat = []
    for group in bindings["records"].values():
        flat.extend(group)
    return flat


def _snapshot(adapter):
    return {
        "definition": adapter.resolve_definition("RefundService.requestRefund"),
        "callers": adapter.resolve_callers("RefundService.requestRefund"),
        "callees": adapter.callees("RefundController.requestRefund"),
        "unresolved": adapter.unresolved(),
    }


def _canonical(value):
    import json

    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


# ---------------------------------------------------------------- imports


def test_adapter_and_bindings_modules_importable():
    """bo_java exposes the adapter and bindings modules (stdlib + bo_java only)."""
    assert callable(getattr(java_adapter, "JavaAdapter", None))
    assert callable(getattr(spring_sql_bindings, "index", None))


# ---------------------------------------------------------------- determinism


def test_indexing_is_deterministic_byte_identical():
    """Re-indexing and re-resolving twice yields byte-identical results."""
    first = _fresh_adapter()
    second = _fresh_adapter()
    assert _canonical(first.index()) == _canonical(second.index())
    assert _canonical(_snapshot(first)) == _canonical(_snapshot(second))


# ---------------------------------------------------------------- local concepts: Refund and Order


def test_refund_concept_definition_composed_from_service_roles_scoped_to_repo_a(adapter, bindings):
    """Refund concept: RefundService.requestRefund definition (byte match) plus the
    @PostMapping("/refunds") controller operation; both scoped to repo-a."""
    definition = adapter.resolve_definition("RefundService.requestRefund")
    assert isinstance(definition, dict) and definition, definition
    assert _same_file(definition.get("file"), REFUND_SERVICE), definition
    expected = _line_number(
        REFUND_SERVICE, "public RefundResult requestRefund(RefundRequest request)"
    )
    assert definition["line"] == expected, definition
    raw = _raw_lines(REFUND_SERVICE)
    assert raw[definition["line"] - 1] == raw[expected - 1], definition
    span = definition.get("span") or {}
    assert span.get("start_line") == expected, definition

    operations = [
        r
        for r in bindings["records"]["annotations"]
        if r.get("kind") == "operation"
    ]
    refund_ops = [
        r
        for r in operations
        if _same_file(r.get("file"), REFUND_CONTROLLER) and r.get("path") == "/refunds"
    ]
    assert refund_ops, refund_ops
    op = refund_ops[0]
    assert _same_file(op.get("file"), REFUND_CONTROLLER), op
    assert op.get("operation") == "post", op
    assert op.get("path") == "/refunds", op
    assert op.get("span", {}).get("start_line") == _line_number(
        REFUND_CONTROLLER, '@PostMapping("/refunds")'
    ), op


def test_order_concept_definition_composed_from_service_roles_scoped_to_repo_b(adapter):
    """Order concept: OrderService.createOrder, OrderService.submitRefund and
    OrderClient.requestRefund definitions resolve, all scoped to repo-b."""
    for symbol, path in (
        ("OrderService.createOrder", ORDER_SERVICE),
        ("OrderService.submitRefund", ORDER_SERVICE),
        ("OrderClient.requestRefund", ORDER_CLIENT),
    ):
        record = adapter.resolve_definition(symbol)
        assert record is not None, symbol
        assert _same_file(record.get("file"), path), record


# ---------------------------------------------------------------- configured policy (basis for the guard)


def test_window_days_policy_configured_to_thirty_in_repo_a(bindings):
    """Configured refund window policy: window-days = 30 (repo-a application.yml)."""
    policies = bindings["records"]["policies"]
    window = [
        r
        for r in policies
        if _same_file(r.get("file"), REFUND_YML) and r.get("key") == "window-days"
    ]
    assert window, policies
    assert window[0].get("configured") is True, window[0]
    assert window[0].get("value") == "30", window[0]


# ---------------------------------------------------------------- enforced rule: window guard (RED: slice records missing)


def test_enforced_window_guard_rule_record_missing_slice_piece(adapter, bindings):
    """RED: no exposed enforced_rule record for the window guard.

    Expected: one rule whose condition compares the paid-at age to configured
    window-days and whose failure outcome is rejection or false return.
    bo_java emits no rule records, so this is a missing slice piece.
    """
    rule_records = [
        r
        for group in bindings["records"].values()
        for r in group
        if r.get("kind") == "enforced_rule"
    ]
    assert rule_records, (
        "missing slice piece: no enforced_rule record for the window guard; "
        "expected condition = paid-at age vs configured window-days, "
        "failure outcome = rejection / false return; bo_java emits only "
        "declarations, calls, annotations, policy, persistence and contract "
        "records"
    )


def test_window_guard_enforcement_lines_cite_guard_if_lines_not_declarations(
    adapter, bindings
):
    """RED: enforcement lines must be the guard-if body lines, not declarations.

    Expected: a rule record citing enforcement lines such as the
    ``if (!isEligible(request.getPaidAt()))`` check in ``requestRefund`` and
    the ``days <= windowDays`` comparison in ``isEligible``. Only method
    declaration spans exist, so enforcement lines are not cited anywhere.
    """
    enforcement_records = [
        r
        for group in bindings["records"].values()
        for r in group
        if r.get("enforcement_lines") or "enforcement" in str(r.keys())
    ]
    assert enforcement_records, (
        "missing slice piece: no record cites enforcement lines; only "
        "declaration spans (method start/end line) are exposed. The window "
        "guard's enforcement lines are body guard-if lines, not the "
        "``public boolean isEligible(...)`` declaration."
    )


def test_window_guard_failure_false_return_is_derivable(adapter):
    """GREEN from raw evidence: guard failure is a false return
    (``return days <= windowDays;``) with a rejection in requestRefund."""
    definition = adapter.resolve_definition("RefundService.isEligible")
    assert definition is not None, "RefundService.isEligible definition missing"
    assert _same_file(definition.get("file"), REFUND_SERVICE), definition
    body = REFUND_SERVICE.read_text(encoding="utf-8")
    assert "return days <= windowDays;" in body, body
    assert "if (!isEligible(request.getPaidAt()))" in body, body
    assert (
        'throw new IllegalArgumentException("Refund not eligible: outside configured window")'
        in body
    ), body


# ---------------------------------------------------------------- guarded transition (RED: slice record missing)


def test_guarded_transition_to_refund_requested_named_guard_missing_slice_piece(
    adapter, bindings
):
    """RED: no exposed guarded transition to REFUND_REQUESTED with a named guard.

    Expected: a transition record (target status REFUND_REQUESTED, guard
    named, enforcement lines). bo_java emits no transition records, so this is
    a missing slice piece.
    """
    transitions = [
        r
        for group in bindings["records"].values()
        for r in group
        if r.get("kind") == "transition" or "REFUND_REQUESTED" in str(r.values())
    ]
    assert transitions, (
        "missing slice piece: no guarded-transition record; expected "
        "transition to REFUND_REQUESTED guarded by the named window/status "
        "guard with enforcement lines. bo_java exposes only raw declarations "
        "and calls."
    )


# ---------------------------------------------------------------- cross-repo contract


def test_cross_repo_contract_order_client_post_refunds_compatible_not_equivalent(
    bindings,
):
    """OrderClient POST http://refund-service.local/refunds vs repo-a OpenAPI
    POST /refunds: contract link compatible=True, equivalent=False (never
    equivalent). Cross-repo: client in repo-b, OpenAPI in repo-a."""
    client_urls = bindings["records"]["client_urls"]
    refund_urls = [
        r
        for r in client_urls
        if _same_file(r.get("file"), ORDER_CLIENT)
        and "/refunds" in r.get("url", "")
    ]
    assert refund_urls, client_urls
    url_record = refund_urls[0]
    assert url_record["url"] == "http://refund-service.local/refunds", url_record

    links = bindings["records"]["contract_links"]
    matched = [
        l
        for l in links
        if _same_file(l.get("file"), ORDER_CLIENT)
        and l.get("client_path") == "/refunds"
        and l.get("openapi_path") == "/refunds"
        and _same_file(l.get("openapi_file"), REFUND_OPENAPI)
    ]
    assert matched, links
    link = matched[0]
    assert link["compatible"] is True, link
    assert link["equivalent"] is False, link
    assert _same_file(link.get("openapi_file"), REFUND_OPENAPI), link


# ---------------------------------------------------------------- unresolved item


def test_unresolved_dynamic_hookname_invoke_recorded_with_reason(adapter):
    """The dynamic getMethod(hookName)/invoke probe is listed unresolved,
    with file, line and a recorded reason; never a resolved callee."""
    entries = adapter.unresolved()
    candidates = [
        e
        for e in entries
        if _same_file(e.get("file"), ORDER_SERVICE) and e.get("name") == "invoke"
    ]
    assert candidates, entries
    entry = candidates[0]
    assert entry["line"] == _line_number(ORDER_SERVICE, "method.invoke(this)"), entry
    reason = str(entry.get("reason", ""))
    assert "dynamic invocation probe" in reason, entry
    assert "getMethod(hookName)" in reason, entry
    assert "never as a resolved callee" in reason, entry


def test_unresolved_invoke_never_reported_as_resolved_callee(adapter):
    """The dynamic invoke is never falsely reported as a resolved static target."""
    line = _line_number(ORDER_SERVICE, "method.invoke(this)")
    resolved = []
    resolved.extend(adapter.resolve_callers("OrderService.triggerPostCreateHook") or [])
    resolved.extend(adapter.callees("OrderService.triggerPostCreateHook") or [])
    assert all(
        not (_same_file(e.get("file"), ORDER_SERVICE) and e.get("line") == line)
        for e in resolved
    ), resolved


# ---------------------------------------------------------------- rejected mapping


def test_rejected_mapping_incompatible_contract_pair_with_insufficiency_reason(
    bindings,
):
    """OrderClient /refunds vs repo-b OpenAPI /orders/{orderId}/refund: an
    incompatible contract pair; the insufficiency reason is the path mismatch
    (client path /refunds != order-scoped path template), equivalent=False."""
    links = bindings["records"]["contract_links"]
    client_links = [
        l
        for l in links
        if _same_file(l.get("file"), ORDER_CLIENT)
        and l.get("client_path") == "/refunds"
    ]
    assert client_links, links
    rejected = [
        l
        for l in client_links
        if l.get("openapi_path") == "/orders/{orderId}/refund"
    ]
    assert rejected, client_links
    link = rejected[0]
    assert _same_file(link.get("openapi_file"), ORDER_OPENAPI), link
    assert link["compatible"] is False, link
    assert link["equivalent"] is False, link
    assert link["client_path"] != link["openapi_path"], link  # insufficiency reason


# ---------------------------------------------------------------- epistemic labels


def test_epistemic_labels_static_and_configured_present_observed_absent(bindings):
    """static (persistence) and configured (policy) labels present;
    observed absent."""
    flat = _all_records(bindings)
    static = [r for r in flat if r.get("static") is True]
    configured = [r for r in flat if r.get("configured") is True]
    assert static, "static persistence label missing"
    assert configured, "configured policy label missing"
    observed = [r for r in flat if r.get("observed") is True or "observed" in r]
    assert not observed, f"observed label must be absent: {observed}"


# ---------------------------------------------------------------- persistence


def test_refund_persistence_schema_is_static_evidence(bindings):
    """refunds table is static persistence evidence (repo-a SQL migration)."""
    persistence = bindings["records"]["persistence"]
    tables = [
        r
        for r in persistence
        if r.get("kind") == "create_table" and r.get("table") == "refunds"
    ]
    assert tables, persistence
    table = tables[0]
    assert _same_file(table.get("file"), REFUND_SQL), table
    assert table.get("static") is True, table
    assert table.get("evidence") == "persistence", table
    columns = {c["name"] for c in table.get("columns", [])}
    assert {"order_id", "status"} <= columns, columns
    assert table.get("span", {}).get("start_line") == 1, table
