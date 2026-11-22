"""Java adapter contract tests (backend-neutral).

RED by design: ``src/bo_java`` does not exist yet, so the import below raises
ModuleNotFoundError at collection time. These tests freeze the behavior any
compliant Java resolution backend must show on the frozen fixtures:

* ``JavaAdapter(repo_paths).index()`` then ``resolve_definition(symbol)``
  returns ``file`` + ``line`` that byte-match the fixture text at that span;
* a supported call resolves with both caller ``file`` + ``line`` and callee
  ``file`` + ``line`` (controller -> service, service -> client);
* every unresolvable call appears in an explicit ``unresolved()`` list with a
  ``reason`` (the dynamic ``getMethod(hookName)`` / ``invoke`` probe is listed,
  never silently dropped, and never falsely reported as a resolved callee);
* re-indexing/resolving is deterministic: two runs are byte-identical.

No backend names, no network, no src imports beyond the adapter module under
test. Fixture text is read as text/bytes only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bo_java import java_adapter  # noqa: F401  (module missing -> collection error RED)

ROOT = Path(__file__).resolve().parents[1]
REPO_A = ROOT / "fixtures" / "java-repo-a"
REPO_B = ROOT / "fixtures" / "java-repo-b"
REPO_PATHS = [REPO_A, REPO_B]

REFUND_SERVICE = REPO_A / "src/main/java/com/example/refund/RefundService.java"
REFUND_CONTROLLER = REPO_A / "src/main/java/com/example/refund/RefundController.java"
ORDER_SERVICE = REPO_B / "src/main/java/com/example/orders/OrderService.java"
ORDER_CLIENT = REPO_B / "src/main/java/com/example/orders/OrderClient.java"


# ---------------------------------------------------------------- helpers


def _fresh():
    """Build an adapter and index the frozen repos; return (adapter, summary)."""
    adapter = java_adapter.JavaAdapter(REPO_PATHS)
    summary = adapter.index()
    return adapter, summary


@pytest.fixture(scope="module")
def adapter():
    indexed, _ = _fresh()
    return indexed


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


def _find(entries, path, needle):
    """First entry pointing at *path* on the line containing *needle*."""
    number = _line_number(path, needle)
    for entry in entries:
        if _same_file(entry.get("file"), path) and entry.get("line") == number:
            return entry
    return None


def _canonical(value):
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _snapshot(adapter):
    return {
        "definition": adapter.resolve_definition("RefundService.requestRefund"),
        "callers": adapter.resolve_callers("RefundService.requestRefund"),
        "callees": adapter.callees("RefundController.requestRefund"),
        "unresolved": adapter.unresolved(),
    }


# ---------------------------------------------------------------- API surface


def test_adapter_exposes_index_and_resolution_api():
    """The adapter exposes index + definition/caller/callee/unresolved resolution."""
    cls = getattr(java_adapter, "JavaAdapter", None)
    assert isinstance(cls, type), "JavaAdapter class missing"
    for name in ("index", "resolve_definition", "resolve_callers", "callees", "unresolved"):
        assert callable(getattr(cls, name, None)), name


# ---------------------------------------------------------------- definition


def test_resolve_definition_returns_file_and_line_byte_matching_fixture(adapter):
    """resolve_definition("RefundService.requestRefund") points at the fixture line."""
    record = adapter.resolve_definition("RefundService.requestRefund")
    assert isinstance(record, dict) and record, record
    assert _same_file(record.get("file"), REFUND_SERVICE), record
    assert isinstance(record.get("line"), int), record
    expected = _line_number(
        REFUND_SERVICE, "public RefundResult requestRefund(RefundRequest request)"
    )
    assert record["line"] == expected, record
    raw = _raw_lines(REFUND_SERVICE)
    assert raw[record["line"] - 1] == raw[expected - 1]


# ---------------------------------------------------------------- supported calls


def test_supported_call_resolves_caller_and_callee_file_line(adapter):
    """Controller -> service call resolves both caller and callee file + line."""
    callers = adapter.resolve_callers("RefundService.requestRefund")
    assert isinstance(callers, list) and callers, callers
    assert (
        _find(callers, REFUND_CONTROLLER, "refundService.requestRefund(request)") is not None
    ), callers

    callees = adapter.callees("RefundController.requestRefund")
    assert isinstance(callees, list) and callees, callees
    assert (
        _find(
            callees,
            REFUND_SERVICE,
            "public RefundResult requestRefund(RefundRequest request)",
        )
        is not None
    ), callees


def test_service_to_client_call_resolves_caller_and_callee_file_line(adapter):
    """OrderService.submitRefund -> OrderClient.requestRefund resolves both endpoints."""
    callers = adapter.resolve_callers("OrderClient.requestRefund")
    assert (
        _find(callers, ORDER_SERVICE, "orderClient.requestRefund(orderId)") is not None
    ), callers

    callees = adapter.callees("OrderService.submitRefund")
    assert (
        _find(callees, ORDER_CLIENT, "public String requestRefund(String orderId)") is not None
    ), callees


# ---------------------------------------------------------------- unresolved


def test_unresolved_list_is_explicit_with_file_line_and_reason(adapter):
    """unresolved() is an explicit list; every entry has file, line, reason."""
    entries = adapter.unresolved()
    assert isinstance(entries, list), entries
    assert entries, "unresolved list must be explicit, not empty"
    for entry in entries:
        assert isinstance(entry, dict), entry
        assert entry.get("file"), entry
        assert isinstance(entry.get("line"), int) and entry["line"] > 0, entry
        assert entry.get("reason"), entry


def test_dynamic_getmethod_hookname_invoke_is_listed_as_unresolved(adapter):
    """The dynamic getMethod(hookName)/invoke probe is listed, never dropped."""
    entries = adapter.unresolved()
    candidates = [
        entry
        for entry in entries
        if _same_file(entry.get("file"), ORDER_SERVICE) and entry.get("line") in range(20, 25)
    ]
    assert candidates, entries
    text = " ".join(
        " ".join(str(entry.get(key, "")) for key in ("reason", "name", "target", "symbol"))
        for entry in candidates
    ).lower()
    assert any(
        token in text for token in ("invoke", "hookname", "getmethod", "dynamic")
    ), candidates


def test_dynamic_invoke_is_not_reported_as_resolved_callee(adapter):
    """The dynamic invoke is never falsely reported as a resolved static target."""
    resolved = []
    for entries in (
        adapter.resolve_callers("OrderService.triggerPostCreateHook"),
        adapter.callees("OrderService.triggerPostCreateHook"),
    ):
        resolved.extend(entries or [])
    assert all(
        not (_same_file(entry.get("file"), ORDER_SERVICE) and entry.get("line") == 23)
        for entry in resolved
    ), resolved


# ---------------------------------------------------------------- negatives / determinism


def test_resolve_definition_unknown_symbol_does_not_fabricate_location(adapter):
    """An unknown symbol resolves to no fabricated file/line."""
    record = adapter.resolve_definition("NoSuchService.noSuchMethod")
    if record is None:
        return
    assert not record.get("file"), record
    assert not record.get("line"), record


def test_reruns_are_byte_identical():
    """Re-indexing and re-resolving twice yields byte-identical results."""
    first, first_summary = _fresh()
    second, second_summary = _fresh()
    assert _canonical(first_summary) == _canonical(second_summary)
    assert _canonical(_snapshot(first)) == _canonical(_snapshot(second))


def test_ambiguous_same_name_same_arity_recorded_unresolved_not_resolved(adapter):
    """Ambiguous same-name same-arity entries never appear in callers or callees."""
    unresolved = adapter.unresolved()
    ambiguous_entries = [
        entry for entry in unresolved
        if "ambiguous" in entry.get("reason", "")
    ]
    # If there are ambiguous entries, verify they don't appear in resolved results
    for entry in ambiguous_entries:
        callers = adapter.resolve_callers(entry.get("symbol") or "")
        assert not any(
            _same_file(c.get("file"), entry.get("file")) and c.get("line") == entry.get("line")
            for c in callers
        ), f"Ambiguous entry at {entry.get('file')}:{entry.get('line')} appears in callers"
        
        # For callees, we need to check based on the symbol if available
        symbol = entry.get("symbol")
        if symbol and "." in symbol:
            callees = adapter.callees(symbol)
            assert not any(
                _same_file(c.get("file"), entry.get("file")) and c.get("line") == entry.get("line")
                for c in callees
            ), f"Ambiguous entry at {entry.get('file')}:{entry.get('line')} appears in callees"


def test_synthetic_same_type_name_arity_ambiguity_records_unresolved_not_resolved(tmp_path):
    """Synthetic same-type/same-name/same-arity overloads trigger ambiguity, not first-match."""
    receiver_file = tmp_path / "Target.java"
    receiver_file.write_text(
        "public class Target {\n"
        "    public void run(String s) {\n"
        "    }\n"
        "\n"
        "    public void run(Integer i) {\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    caller_file = tmp_path / "Caller.java"
    caller_file.write_text(
        "public class Caller {\n"
        "    private Target target;\n"
        "\n"
        "    public void execute() {\n"
        "        target.run(\"x\");\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    call_line = _line_number(caller_file, 'target.run("x")')
    candidate_line_string = _line_number(receiver_file, "public void run(String s)")
    candidate_line_integer = _line_number(receiver_file, "public void run(Integer i)")

    adapter = java_adapter.JavaAdapter([tmp_path])
    adapter.index()

    unresolved = adapter.unresolved()
    site_entries = [
        entry
        for entry in unresolved
        if _same_file(entry.get("file"), caller_file) and entry.get("line") == call_line
    ]
    assert len(site_entries) == 1, site_entries
    entry = site_entries[0]
    assert "ambiguous" in str(entry.get("reason", "")).lower(), entry
    assert _same_file(entry.get("file"), caller_file), entry
    assert entry.get("line") == call_line, entry
    span = entry.get("span")
    if span is not None:
        assert span.get("start_line") == call_line, entry
        assert span.get("end_line") == call_line, entry

    # resolve_callers for either candidate symbol must not publish this call site
    callers = adapter.resolve_callers("Target.run")
    assert not any(
        _same_file(c.get("file"), caller_file) and c.get("line") == call_line
        for c in callers
    ), callers

    # callees for the caller symbol must not point at either candidate for this span
    callee_entries = adapter.callees("Caller.execute")
    assert not any(
        _same_file(c.get("file"), caller_file) and c.get("line") == call_line
        for c in callee_entries
    ), callee_entries
    assert not any(
        _same_file(c.get("file"), receiver_file)
        and c.get("line") in (candidate_line_string, candidate_line_integer)
        for c in callee_entries
    ), callee_entries
    assert not any(
        c.get("callee") == "Target.run"
        and _same_file(c.get("file"), receiver_file)
        and c.get("line") in (candidate_line_string, candidate_line_integer)
        for c in callee_entries
    ), callee_entries
