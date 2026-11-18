"""Review-correction regression batch (RED-first evidence-integrity defects).

Five defect classes, each proven by at least one test that FAILS on the
current tree. No src/ fixture/ seal/ verifier/ doc changes here -- production
fixes belong to ``implementer``. These tests pin the *corrected* contract:

1. ``TestGuardPrecedence`` -- only a *preceding* enforced guard may claim a
   status transition; a later (post-write) guard must not be back-attributed,
   and an unguarded write must carry ``guard None`` (no synthesized
   "window guard").
2. ``TestUrlContract`` -- contract compatibility compares path *and* method
   (the client method rides on the URL record); an unused URL (path matches
   nothing) yields no links; ``equivalent`` stays False.
3. ``TestM3DemoGating`` -- ``/m3/*`` must not serve hard-coded fixture rows
   for an arbitrary temp-db app; demo data requires explicit opt-in.
4. ``TestTypedApiFiltering`` -- typed/status endpoints must filter (or carry
   an explicit legacy/unfiltered label), never silently return all rows.
5. ``TestInvalidationStorePath`` -- invalidation must flow through the store
   (``OntologyStore.invalidate``) with an audit-log entry, superseding the
   prior status without silent carry-over while preserving unrelated rows.
   (The pure ``invalidate_artifact`` helper intentionally touches no DB; the
   store owns persistence/audit.)
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from bo_api.server import create_app
from bo_java.spring_sql_bindings import (
    extract_client_urls,
    extract_guards,
    extract_openapi,
    link_client_url,
)
from bo_ontology.schema import Assertion
from bo_ontology.store import OntologyStore
from bo_store.provenance import init_db, record_artifact


# ---------------------------------------------------------------- guards


_JAVA = """\
public class RefundService {
    boolean isEligible(String paidAt) {
        return paidAt != null;
    }
    void applyTrue(Order order) {
        if (!isEligible(order.getPaidAt())) {
            throw new IllegalStateException("not eligible");
        }
        order.setRefundStatus("REFUNDED"); // TRUE
    }
    void applyLate(Order order) {
        order.setRefundStatus("REFUNDED"); // LATE
        if (order.getTotal() == null) {
            throw new IllegalStateException("no total");
        }
    }
    void applyBare(Order order) {
        order.setRefundStatus("PENDING"); // BARE
    }
}
"""


def _transitions_by_marker(path: Path) -> dict[str, dict]:
    _, transitions = extract_guards(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    wanted: dict[str, dict] = {}
    for index, text in enumerate(lines, start=1):
        for marker in ("TRUE", "LATE", "BARE"):
            if "setRefundStatus" in text and marker in text:
                wanted[marker] = next(
                    t for t in transitions if t["line"] == index
                )
    assert set(wanted) == {"TRUE", "LATE", "BARE"}, wanted.keys()
    return wanted


class TestGuardPrecedence:
    """Only a preceding enforced guard claims a transition (src guard defect)."""

    def test_only_preceding_enforced_guard_claims_transition(
        self, tmp_path
    ):
        java = tmp_path / "RefundService.java"
        java.write_text(_JAVA, encoding="utf-8")
        found = _transitions_by_marker(java)

        true_t = found["TRUE"]
        assert true_t["guard"] == "isEligible"
        assert true_t["guard_if_line"] < true_t["line"]

        late_t = found["LATE"]
        assert late_t["guard"] is None or (
            late_t["guard_if_line"] is not None
            and late_t["guard_if_line"] < late_t["line"]
        ), f"post-write guard back-attributed: {late_t!r}"

        bare_t = found["BARE"]
        assert bare_t["guard"] is None, (
            f"unguarded write synthesized a guard: {bare_t!r}"
        )


# ---------------------------------------------------------------- URL contract


def _client_record(tmp_path: Path, url: str, method: str | None) -> dict:
    src = tmp_path / "Client.java"
    src.write_text(f'var u = "{url}";\n', encoding="utf-8")
    record = {"url": url, "file": str(src), "line": 1, "method": method}
    return record


def _openapi_record(tmp_path: Path, name: str, path: str, method: str) -> dict:
    doc = tmp_path / name
    doc.write_text("openapi: 3.0.0\n", encoding="utf-8")
    return {
        "openapi_path": path,
        "method": method,
        "file": str(doc),
        "line": 2,
    }


class TestUrlContract:
    """Compatibility = path AND method; unused URLs yield no links."""

    def test_method_mismatch_is_incompatible(self, tmp_path):
        url = _client_record(
            tmp_path, "http://refund-service.local/refunds", "post"
        )
        same = _openapi_record(tmp_path, "same.yaml", "/refunds", "post")
        other = _openapi_record(tmp_path, "other.yaml", "/refunds", "get")

        assert link_client_url(url, [same])[0]["compatible"] is True

        (link,) = link_client_url(url, [other])
        assert link["equivalent"] is False
        assert link["compatible"] is False, (
            f"same-path/different-method reported compatible: {link!r}"
        )
        assert "method" in link["reason"].lower()

    def test_unused_url_yields_no_links(self, tmp_path):
        url = _client_record(
            tmp_path, "http://refund-service.local/orders/9/refund", "post"
        )
        op = _openapi_record(tmp_path, "op.yaml", "/refunds", "post")
        assert link_client_url(url, [op]) == [], (
            "unused URL (path matches nothing) must yield no links"
        )


# ---------------------------------------------------------------- M3 demo gating


def _seed_db(tmp_path: Path, repo: str = "custom") -> Path:
    db = tmp_path / "prov.db"
    init_db(db)
    record_artifact(
        db,
        repo=repo,
        commit_sha="c" * 40,
        file_path="svc.py",
        line_start=1,
        line_end=1,
        content_hash="e" * 64,
        extractor="deterministic",
        version="1.0",
        run_id="run9",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=1.0,
        basis="sentinel temp-db row, never in fixtures",
        excerpt="TEMP-DB-SENTINEL-ROW",
    )
    return db


def _serve(db: Path):
    handler = create_app(db)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _get(httpd, path: str) -> tuple[int, object]:
    url = f"http://127.0.0.1:{httpd.server_port}{path}"
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _assert_gated(status: int, data: object, path: str) -> None:
    assert status in (403, 404) or (
        isinstance(data, dict) and data.get("status") == "abstain"
    ), f"{path} served demo/fixture data without explicit opt-in"


class TestM3DemoGating:
    """/m3/* must not serve hard-coded fixture rows to a temp-db app."""

    def test_m3_concepts_requires_explicit_demo_opt_in(self, tmp_path):
        db = _seed_db(tmp_path)
        httpd, thread = _serve(db)
        try:
            status, data = _get(httpd, "/m3/concepts")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        _assert_gated(status, data, "/m3/concepts")

    def test_m3_workflow_requires_explicit_demo_opt_in(self, tmp_path):
        db = _seed_db(tmp_path)
        httpd, thread = _serve(db)
        try:
            status, data = _get(httpd, "/m3/workflow")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        _assert_gated(status, data, "/m3/workflow")


# ---------------------------------------------------------------- typed API filtering


def _seed_mixed_status_db(tmp_path: Path) -> Path:
    db = tmp_path / "prov.db"
    init_db(db)
    for repo, status, excerpt in (
        ("repo-a", "proposed", "row-proposed"),
        ("repo-a", "accepted", "row-accepted"),
    ):
        record_artifact(
            db,
            repo=repo,
            commit_sha="a" * 40,
            file_path="app.py",
            line_start=1,
            line_end=1,
            content_hash="c" * 64,
            extractor="deterministic",
            version="1.0",
            run_id="run1",
            timestamp="2026-01-01T00:00:00Z",
            origin="deterministic",
            status=status,
            confidence=1.0,
            basis="mixed-status seed",
            excerpt=excerpt,
        )
    return db


def _assert_filtered_or_labelled(data: object, keep: object) -> None:
    """Lists must be filtered; dicts must carry an explicit filter label."""
    if isinstance(data, list):
        assert data == keep, f"silent all-rows response: {data!r}"
        return
    assert isinstance(data, dict) and (
        data.get("filter") or data.get("unfiltered") or data.get("legacy")
    ), f"unlabeled unfiltered response: {data!r}"


class TestTypedApiFiltering:
    """Typed/status endpoints filter or label; never silent all-rows."""

    def test_conflicts_never_returns_silent_all_rows(self, tmp_path):
        db = _seed_mixed_status_db(tmp_path)  # no disputed rows seeded
        httpd, thread = _serve(db)
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_port}/conflicts"
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        _assert_filtered_or_labelled(data, [])

    def test_status_param_filters_or_labels(self, tmp_path):
        db = _seed_mixed_status_db(tmp_path)
        httpd, thread = _serve(db)
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_port}/status?status=accepted"
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        if isinstance(data, list):
            assert data and all(
                r.get("status") == "accepted" for r in data
            ), f"?status= ignored, silent all-rows: {data!r}"
        else:
            _assert_filtered_or_labelled(data, data)


# ---------------------------------------------------------------- invalidation store path


def _seed_assertions(store: OntologyStore) -> tuple[str, str]:
    aid = store.add_assertion(
        Assertion(
            subject="refund_window_days",
            predicate="allows",
            object=30,
            status="accepted",
            origin="deterministic",
        )
    )
    bid = store.add_assertion(
        Assertion(
            subject="sla_days",
            predicate="allows",
            object=5,
            status="accepted",
            origin="deterministic",
        )
    )
    return aid, bid


class TestInvalidationStorePath:
    """Invalidation flows through the store with audit; unrelated preserved."""

    def test_store_invalidation_audits_and_preserves_unrelated(
        self, tmp_path
    ):
        with OntologyStore(tmp_path / "inv.db") as store:
            aid, bid = _seed_assertions(store)
            result = store.invalidate(
                "assertions", aid, reason="window changed out-of-band"
            )
            assert result["status"] == "stale"
            hist = store.history("assertions", aid)
            assert any(
                entry.get("action") == "invalidate" for entry in hist
            ), f"invalidation left no audit entry: {hist!r}"
            assert store.get("assertions", bid)["status"] == "accepted"

    def test_store_invalidation_supersedes_without_silent_carry(
        self, tmp_path
    ):
        with OntologyStore(tmp_path / "inv2.db") as store:
            (aid, _) = _seed_assertions(store)
            result = store.invalidate(
                "assertions",
                aid,
                reason="window changed out-of-band",
                new_value=45,
            )
            assert result["status"] == "stale"
            assert result["value"] == 45
            assert result.get("original_value") == 30
            assert result.get("original_status") == "accepted"


# ---------------------------------------------------------------- optional-branch guard abstention


class TestGuardOptionalBranch:
    """Prior guard inside an optional branch must not govern an outer write."""

    def test_optional_branch_guard_abstains(self, tmp_path):
        src = tmp_path / "OptionalGuardService.java"
        src.write_text(
            "public class OptionalGuardService {\n"
            "    boolean isEligible(String paidAt) {\n"
            "        return paidAt != null;\n"
            "    }\n"
            "    boolean isPaid(String paidAt) {\n"
            "        return paidAt != null;\n"
            "    }\n"
            "    void applyTop(Order order) {\n"
            "        if (!isPaid(order.getPaidAt())) {\n"
            '            throw new IllegalStateException("not paid");\n'
            "        }\n"
            '        order.setRefundStatus("TOP");\n'
            "    }\n"
            "    void applyOptional(Order order, boolean flag) {\n"
            "        if (flag) {\n"
            "            if (!isEligible(order.getPaidAt())) {\n"
            '                throw new IllegalStateException("not eligible");\n'
            "            }\n"
            "        }\n"
            '        order.setRefundStatus("OPTIONAL");\n'
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        enforced, transitions = extract_guards(src)
        nested_lines = {
            r["guard_if_line"]
            for r in enforced
            if r.get("guard") == "isEligible"
        }
        assert nested_lines, f"setup must yield nested enforced guard: {enforced!r}"
        nested_line = sorted(nested_lines)[0]
        by_status = {t["target_status"]: t for t in transitions}
        assert {"TOP", "OPTIONAL"} <= set(by_status), (
            f"expected TOP + OPTIONAL transitions: {transitions!r}"
        )

        top_t = by_status["TOP"]
        assert top_t["guard"] == "isPaid"
        assert top_t["guard_if_line"] is not None
        assert top_t["guard_if_line"] < top_t["line"]
        assert top_t["guard_if_line"] in top_t["enforcement_lines"], (
            f"top-level guard must cite enforcement: {top_t!r}"
        )

        opt_t = by_status["OPTIONAL"]
        assert opt_t["guard"] is None, (
            f"optional-branch guard must abstain, claimed: {opt_t!r}"
        )
        assert nested_line not in opt_t["enforcement_lines"], (
            f"outer write must not cite nested guard line: {opt_t!r}"
        )

    def test_sibling_branch_guard_does_not_attach(self, tmp_path):
        src = tmp_path / "SiblingGuardService.java"
        src.write_text(
            "public class SiblingGuardService {\n"
            "    boolean isOk(String paidAt) {\n"
            "        return paidAt != null;\n"
            "    }\n"
            "    void applySibling(Order order, boolean flag) {\n"
            "        if (flag) {\n"
            "            if (!isOk(order.getPaidAt())) {\n"
            '                throw new IllegalStateException("bad");\n'
            "            }\n"
            "        } else {\n"
            '            order.setRefundStatus("SIB");\n'
            "        }\n"
            "    }\n"
            "    void applyTop(Order order) {\n"
            "        if (!isOk(order.getPaidAt())) {\n"
            '            throw new IllegalStateException("bad");\n'
            "        }\n"
            '        order.setRefundStatus("TOP");\n'
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        enforced, transitions = extract_guards(src)
        assert any(
            r.get("guard") == "isOk" for r in enforced
        ), f"setup must yield enforced guard: {enforced!r}"
        by_status = {t["target_status"]: t for t in transitions}
        assert {"SIB", "TOP"} <= set(by_status), (
            f"expected SIB + TOP transitions: {transitions!r}"
        )
        sib_t = by_status["SIB"]
        assert sib_t["guard"] is None, (
            f"sibling-branch guard must abstain, claimed: {sib_t!r}"
        )
        assert sib_t["enforcement_lines"] == [], (
            f"sibling write must cite no enforcement: {sib_t!r}"
        )
        top_t = by_status["TOP"]
        assert top_t["guard"] == "isOk"
        assert top_t["guard_if_line"] is not None
        assert top_t["guard_if_line"] < top_t["line"]

# ---------------------------------------------------------------- positive-branch containment

_WPOS = """\
public class WidgetService {
    void approveWidget(Widget widget) {
        if (widget.getState() == WidgetState.PENDING) {
            widget.setWidgetStatus(WidgetState.ACTIVE);
        }
    }
    void parkWidget(Widget widget, boolean ready) {
        if (ready) {
            System.out.println("ready");
        }
        widget.setWidgetStatus(WidgetState.PARKED);
    }
}
"""


class TestGuardPositiveContainment:
    """A write INSIDE a guard's then-block is governed by that guard.

    Containment (guard_if < write <= guard_end with the write's parent
    block equal to the guard's then-block) governs; a preceding
    same-block ``if`` that does not contain the write must NOT be
    claimed as governing (no mere-co-occurrence attribution).
    """

    def test_contained_write_guarded_noncontaining_preceding_abstains(
        self, tmp_path
    ):
        src = tmp_path / "WidgetService.java"
        src.write_text(_WPOS, encoding="utf-8")
        enforced, transitions = extract_guards(src)
        assert enforced == [], (
            f"positive containment must not synthesize enforced rules: {enforced!r}"
        )
        by_status = {t["target_status"]: t for t in transitions}
        assert {"ACTIVE", "PARKED"} <= set(by_status), (
            f"expected ACTIVE + PARKED transitions: {transitions!r}"
        )

        active_t = by_status["ACTIVE"]
        assert active_t["guard"] == "widget.getState() == WidgetState.PENDING"
        assert active_t["guard_if_line"] is not None
        assert active_t["guard_if_line"] < active_t["line"]
        assert active_t["guard_if_line"] in active_t["enforcement_lines"], (
            f"contained write must cite its guard: {active_t!r}"
        )

        parked_t = by_status["PARKED"]
        assert parked_t["guard"] is None, (
            f"non-containing preceding if must not govern: {parked_t!r}"
        )
        assert parked_t["enforcement_lines"] == [], (
            f"uncontained write must cite no enforcement: {parked_t!r}"
        )


# ---------------------------------------------------------------- variable-held URL linking


_BILLING_BASE = "http://billing.local:8082"
_BILLING_PATH = "/invoices/submit"
_BILLING_URL = _BILLING_BASE + _BILLING_PATH


def _billing_openapi(tmp_path: Path) -> dict:
    doc = tmp_path / "openapi-billing.yaml"
    doc.write_text(
        "openapi: 3.0.0\n"
        "paths:\n"
        f"  {_BILLING_PATH}:\n"
        "    post:\n"
        "      operationId: submitInvoice\n",
        encoding="utf-8",
    )
    ops = extract_openapi(doc)
    assert len(ops) == 1, f"setup must yield one op: {ops!r}"
    return ops[0]


def _links_for_client(java_src: str, tmp_path: Path, name: str = "InvoiceClient.java"):
    src = tmp_path / name
    src.write_text(java_src, encoding="utf-8")
    urls = extract_client_urls(src)
    op = _billing_openapi(tmp_path)
    links: list[dict] = []
    for rec in urls:
        links.extend(link_client_url(rec, [op]))
    return src, urls, op, links


class TestVariableUrlLinking:
    """Variable-held URL (base + path concat, single assignment) linking.

    Narrow pattern only: ``String <v> = "<http...>"`` or
    ``<v> = <baseVar> + "<path>"`` where base resolves to a single
    literal in the same file with no reassignment, used in a supported
    invocation (postForObject/getForObject/exchange+HttpMethod) with
    the variable reaching the call in the same method. Ambiguous
    values, unknown calls, or unknown methods must never yield
    compatible True. Equivalent is always False.
    """

    def test_supported_var_held_post_links_with_three_spans(self, tmp_path):
        java = (
            "public class InvoiceClient {\n"
            "    private final RestTemplate restTemplate = new RestTemplate();\n"
            f'    private String billingBaseUrl = "{_BILLING_BASE}";\n'
            "    public void submitInvoice(Object request) {\n"
            '        String url = billingBaseUrl + "/invoices/submit";\n'
            "        restTemplate.postForObject(url, request, Void.class);\n"
            "    }\n"
            "}\n"
        )
        src, urls, op, links = _links_for_client(java, tmp_path)
        lines = src.read_text(encoding="utf-8").splitlines()
        binding = next(
            (i + 1 for i, t in enumerate(lines) if 'billingBaseUrl + "/invoices/submit"' in t),
            None,
        )
        call = next(
            (i + 1 for i, t in enumerate(lines) if "postForObject(url" in t),
            None,
        )
        assert binding is not None and call is not None, "setup must pin lines"
        target = [r for r in urls if r.get("url") == _BILLING_URL]
        assert target, (
            f"variable-held URL not resolved (pre-fix: no link): {urls!r}"
        )
        compat = [lk for lk in links if lk.get("compatible") is True]
        assert len(compat) == 1, f"expected one compatible POST link: {links!r}"
        link = compat[0]
        assert link["equivalent"] is False
        assert link["client_path"] == _BILLING_PATH
        # 3-span citation: binding line + invocation line + OpenAPI path+method.
        assert link["line"] == binding, f"binding line not cited: {link!r}"
        assert link.get("client_call_line") == call, (
            f"invocation line not cited: {link!r}"
        )
        assert link["openapi_line"] == op["line"], f"openapi not cited: {link!r}"
        reason = link.get("reason", "")
        assert str(binding) in reason and str(call) in reason, (
            f"reason must cite binding+call lines: {reason!r}"
        )
        assert "post" in reason.lower()

    def test_same_path_wrong_method_stays_incompatible(self, tmp_path):
        java = (
            "public class InvoiceClient {\n"
            "    private final RestTemplate restTemplate = new RestTemplate();\n"
            f'    private String billingBaseUrl = "{_BILLING_BASE}";\n'
            "    public String fetchInvoice(Long id) {\n"
            '        String url = billingBaseUrl + "/invoices/submit";\n'
            "        return restTemplate.getForObject(url, String.class);\n"
            "    }\n"
            "}\n"
        )
        _, _, _, links = _links_for_client(java, tmp_path)
        compat = [lk for lk in links if lk.get("compatible") is True]
        assert compat == [], f"GET vs POST must never be compatible: {links!r}"
        for lk in links:
            assert lk["equivalent"] is False
            assert lk["compatible"] is False
            assert "method" in lk.get("reason", "").lower()

    def test_unused_url_constant_yields_no_compatible_link(self, tmp_path):
        java = (
            "public class InvoiceClient {\n"
            "    private final RestTemplate restTemplate = new RestTemplate();\n"
            '    private static final String UNUSED_BILLING_URL = "http://billing.local:8082/invoices/legacy";\n'
            "    public void submitInvoice(Object request) {\n"
            f'        String url = "http://billing.local:8082{_BILLING_PATH}";\n'
            "    }\n"
            "}\n"
        )
        _, _, _, links = _links_for_client(java, tmp_path)
        compat = [lk for lk in links if lk.get("compatible") is True]
        assert compat == [], f"unused constant must yield no compatible link: {links!r}"

    def test_reassigned_ambiguous_url_abstains_never_picks_first(self, tmp_path):
        java = (
            "public class InvoiceClient {\n"
            "    private final RestTemplate restTemplate = new RestTemplate();\n"
            f'    private String billingBaseUrl = "{_BILLING_BASE}";\n'
            "    public void submitAmbiguous(Object request) {\n"
            '        String url = billingBaseUrl + "/invoices/submit";\n'
            '        url = billingBaseUrl + "/invoices/other";\n'
            "        restTemplate.postForObject(url, request, Void.class);\n"
            "    }\n"
            "}\n"
        )
        _, urls, _, links = _links_for_client(java, tmp_path)
        compat = [lk for lk in links if lk.get("compatible") is True]
        assert compat == [], (
            f"reassigned URL must abstain, never pick first: urls={urls!r} links={links!r}"
        )
        for lk in links:
            assert lk.get("compatible") is not True
        named = [
            lk
            for lk in links
            if "/invoices/submit" in str(lk.get("client_url", ""))
            or "/invoices/other" in str(lk.get("client_url", ""))
        ]
        assert not any(lk.get("compatible") for lk in named), (
            f"must NOT name either ambiguous value as compatible: {named!r}"
        )


# ---------------------------------------------------------------- link hardening


class TestLinkHardening:
    """Supported receiver + duplicate-destination ambiguity (link hardening).

    Compatible True requires a supported ``RestTemplate`` receiver
    (variable typed ``RestTemplate`` or field ``restTemplate``) and a
    supported call (``postForObject``/``getForObject``/``exchange`` with
    ``HttpMethod`` evidence); custom senders abstain. Duplicate
    destinations (same path+method in >1 operation) never yield
    compatible True (ambiguous destination).
    """

    def test_unsupported_receiver_never_compatible(self, tmp_path):
        src = tmp_path / "UnsupportedReceiverClient.java"
        src.write_text(
            "public class UnsupportedReceiverClient {\n"
            "    private final FakeSender sender = new FakeSender();\n"
            f'    private String billingBaseUrl = "{_BILLING_BASE}";\n'
            "    public void submitInvoice(Object request) {\n"
            '        String url = billingBaseUrl + "/invoices/submit";\n'
            "        sender.postForObject(url, request, Void.class);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        urls = extract_client_urls(src)
        target = [r for r in urls if r.get("url") == _BILLING_URL]
        assert target, f"setup must resolve variable-held URL: {urls!r}"
        doc = tmp_path / "openapi-hardening-billing.yaml"
        doc.write_text(
            "openapi: 3.0.0\n"
            "paths:\n"
            f"  {_BILLING_PATH}:\n"
            "    post:\n"
            "      operationId: submitInvoice\n",
            encoding="utf-8",
        )
        ops = extract_openapi(doc)
        assert len(ops) == 1, f"setup must yield one op: {ops!r}"
        links: list[dict] = []
        for rec in target:
            links.extend(link_client_url(rec, ops))
        assert links, "matching path must yield a link (abstain, not silence)"
        assert not any(lk.get("compatible") is True for lk in links), (
            f"unsupported receiver must never yield compatible True: {links!r}"
        )
        for lk in links:
            assert lk["equivalent"] is False

    def test_duplicate_destination_ambiguous_never_compatible(self, tmp_path):
        src = tmp_path / "DuplicateDestClient.java"
        src.write_text(
            "public class DuplicateDestClient {\n"
            "    private final RestTemplate restTemplate = new RestTemplate();\n"
            f'    private String billingBaseUrl = "{_BILLING_BASE}";\n'
            "    public void submitInvoice(Object request) {\n"
            '        String url = billingBaseUrl + "/invoices/submit";\n'
            "        restTemplate.postForObject(url, request, Void.class);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        urls = extract_client_urls(src)
        target = [r for r in urls if r.get("url") == _BILLING_URL]
        assert target, f"setup must resolve variable-held URL: {urls!r}"
        ops: list[dict] = []
        for name in ("openapi-hardening-alpha.yaml", "openapi-hardening-gamma.yaml"):
            doc = tmp_path / name
            doc.write_text(
                "openapi: 3.0.0\n"
                "paths:\n"
                f"  {_BILLING_PATH}:\n"
                "    post:\n"
                f"      operationId: submit-{name}\n",
                encoding="utf-8",
            )
            ops.extend(extract_openapi(doc))
        assert len(ops) == 2, f"setup must yield two duplicate ops: {ops!r}"
        links: list[dict] = []
        for rec in target:
            links.extend(link_client_url(rec, ops))
        assert len(links) == 2, f"duplicate destinations must both link: {links!r}"
        assert not any(lk.get("compatible") is True for lk in links), (
            f"duplicate destination must never yield compatible True: {links!r}"
        )
        for lk in links:
            assert lk["equivalent"] is False
            assert "ambiguous" in str(lk.get("reason", "")).lower(), (
                f"duplicate destination must cite ambiguity: {lk!r}"
            )
