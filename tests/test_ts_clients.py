"""Deterministic TS/JS HTTP client extraction + cross-repo linking.

Covers ``extract_ts_client_urls`` over synthetic Order/Invoice TS services
(distinct names, no fixture reuse): Angular HttpClient get/post/put/delete/
patch with literal + template + base+path composition; fetch with an explicit
``{method}`` plus default GET; axios method forms plus a trivial config-object
form; single-assigned base field/const resolution (both spans cited);
negatives (reassigned base, dynamic/computed URL, dynamic/unknown verb,
non-HTTP ``.get()``, comments/strings that look like HTTP calls); cross-repo
linking via ``bo_workflow`` (tmp manifest with a TS frontend + a Java backend
repo) including wrong-method/ambiguous/unused negatives and sync
idempotency; PetClinic exposed regression (read-only /tmp, skip-if-absent).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bo_java.spring_sql_bindings import (
    emit_annotation_operations,
    extract_mapping_routes,
    extract_ts_client_urls,
    link_client_to_annotation,
)
from bo_workflow.orchestrator import ingest_manifest, report, sync_manifest


def _write(tmp_path: Path, name: str, src: str) -> Path:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return path


ORDER_SERVICE_TS = """\
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { environment } from '../environments/environment';

@Injectable({ providedIn: 'root' })
export class OrderService {
  private entityUrl = environment.REST_API_URL + '/api/orders';
  constructor(private http: HttpClient) {}
  findOrder(orderId: string) {
    return this.http.get(this.entityUrl + '/' + orderId);
  }
  createOrder(payload: unknown) {
    return this.http.post(this.entityUrl + '/create', payload);
  }
  updateOrder(orderId: string, payload: unknown) {
    return this.http.put(`${this.entityUrl}/${orderId}`, payload);
  }
  removeOrder(orderId: string) {
    return this.http.delete(this.entityUrl + '/' + orderId);
  }
  patchOrder(orderId: string, payload: unknown) {
    return this.http.patch(this.entityUrl + '/' + orderId, payload);
  }
}
"""

INVOICE_CLIENT_TS = """\
const INVOICE_BASE = "http://invoice-svc.local/api/invoices";
export function searchInvoices(status: string) {
  return fetch(INVOICE_BASE + '/search?status=' + status, { method: 'POST' });
}
export function pendingInvoices() {
  return fetch('/api/invoices/pending');
}
export function getInvoice(invoiceId: string) {
  return axios.get(INVOICE_BASE + '/' + invoiceId);
}
export function createInvoice(payload: unknown) {
  return axios.post(INVOICE_BASE, payload);
}
export function configInvoice(payload: unknown) {
  return axios({ method: 'post', url: INVOICE_BASE + '/cfg' });
}
"""

NEGATIVES_TS = """\
// this.http.get('/api/phantom/list');
const s = "axios.post('/api/phantom/create')";
const lonely = 'http.delete("/api/phantom/drop")';
let REASSIGNED = 'http://a.local/api/things';
REASSIGNED = 'http://b.local/api/things';
export function bad(cache: Map<string, string>, id: string) {
  cache.get(id);
  return http.get(REASSIGNED + '/' + id);
}
export function dyn(build: (x: string) => string, id: string) {
  return http.get(build(id));
}
export function dynMethod(url: string, m: string) {
  return fetch(url, { method: m });
}
export function tern(flag: boolean) {
  return http.get(flag ? '/a' : '/b');
}
export function unknownVerb(url: string) {
  return fetch(url, { method: 'OPTIONS' });
}
export function unknownBase(url: string) {
  return http.get(url);
}
export function dynConfig(m: string, u: string) {
  return axios({ method: m, url: u });
}
"""


# ---------------------------------------------------------------- Angular HttpClient


def test_httpclient_verbs_paths_and_spans(tmp_path):
    records = extract_ts_client_urls(
        _write(tmp_path, "order.service.ts", ORDER_SERVICE_TS)
    )
    assert len(records) == 5, records
    by_verb = {r["client_method"]: r for r in records}
    assert sorted(by_verb) == ["delete", "get", "patch", "post", "put"], records
    assert by_verb["get"]["resolved_path"] == "/api/orders/{orderId}", records
    assert by_verb["post"]["resolved_path"] == "/api/orders/create", records
    assert by_verb["put"]["resolved_path"] == "/api/orders/{orderId}", records
    assert by_verb["delete"]["resolved_path"] == "/api/orders/{orderId}", records
    assert by_verb["patch"]["resolved_path"] == "/api/orders/{orderId}", records
    for record in records:
        assert record["kind"] == "client_url", record
        assert record["ts"] is True, record
        assert record["url"] == "http://ts.local" + record["resolved_path"], record
        assert record["call_line"] >= 1, record
        assert record["call_text"], record
        assert record["receiver"] in ("this.http", "http"), record
        assert record["span"]["end_line"] == record["call_line"], record
        # single-assigned base field: both the base definition and the call
        # site are cited.
        assert record["base_span"] is not None, record
        assert record["base_span"]["start_line"] < record["call_line"], record
        assert "this.entityUrl" in (record["url_expr"] or ""), record


# ---------------------------------------------------------------- fetch + axios


def test_fetch_and_axios_forms(tmp_path):
    records = extract_ts_client_urls(
        _write(tmp_path, "invoice.client.ts", INVOICE_CLIENT_TS)
    )
    assert len(records) == 5, records
    by_call = {r["call_text"]: r for r in records}
    search = next(r for r in records if "searchInvoices" in r["call_text"]
                  or "'/search" in (r["url_expr"] or ""))
    assert search["client_method"] == "post", records
    assert search["resolved_path"] == "/api/invoices/search", records
    pending = next(r for r in records if r["resolved_path"]
                   == "/api/invoices/pending")
    assert pending["client_method"] == "get", records
    assert pending.get("default_method") is True, records
    assert "default" in str(pending.get("method_reason", "")).lower(), records
    getter = next(r for r in records
                  if r["resolved_path"] == "/api/invoices/{invoiceId}")
    assert getter["client_method"] == "get", records
    assert getter["receiver"] == "axios", records
    created = next(r for r in records if r["resolved_path"]
                   == "/api/invoices" and r["client_method"] == "post")
    assert created["receiver"] == "axios", records
    cfg = next(r for r in records if r["resolved_path"]
               == "/api/invoices/cfg")
    assert cfg["client_method"] == "post", records
    assert cfg["receiver"] == "axios", records
    assert all(r["url"].startswith("http://ts.local/") for r in records), records


# ---------------------------------------------------------------- negatives


def test_negatives_abstain_or_silent(tmp_path):
    records = extract_ts_client_urls(
        _write(tmp_path, "negatives.ts", NEGATIVES_TS)
    )
    assert records == [], records


def test_map_get_alone_never_emits(tmp_path):
    src = "export function f(cache: Map<string, string>, k: string) {\n  return cache.get(k);\n}\n"
    assert extract_ts_client_urls(_write(tmp_path, "map.ts", src)) == []


def test_comment_and_string_lookalikes_never_emit(tmp_path):
    src = ("// axios.get('http://x.local/api/nope')\n"
           "/* fetch('/api/nope', { method: 'POST' }) */\n"
           "const a = \"this.http.get('/api/nope')\";\n"
           "const b = 'http.post(\"/api/nope\")';\n"
           "export function f() { return 1; }\n")
    assert extract_ts_client_urls(_write(tmp_path, "lookalike.ts", src)) == []


# ---------------------------------------------------------------- cross-repo linking


ORDER_CONTROLLER_JAVA = """\
package com.example.tsorders;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @GetMapping("/{orderId}")
    public String find(@PathVariable String orderId) {
        return "ok";
    }

    @PostMapping("/create")
    public String create() {
        return "ok";
    }
}
"""

DUPLICATE_CONTROLLER_JAVA = """\
package com.example.tsorders;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class DuplicateOrderController {

    @GetMapping("/{orderId}")
    public String find(@PathVariable String orderId) {
        return "ok";
    }
}
"""

FRONTEND_TS = """\
import { HttpClient, Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class OrderClient {
  constructor(private http: HttpClient) {}
  findOrder(orderId: string) {
    return this.http.get('http://order-svc.local/api/orders/' + orderId);
  }
  wrongMethod(orderId: string) {
    return this.http.post('http://order-svc.local/api/orders/' + orderId, {});
  }
  unused() {
    return this.http.get('http://order-svc.local/api/shipments/9');
  }
}
"""

INVOICE_CONTROLLER_JAVA = """\
package com.example.tsinvoices;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/invoices")
public class InvoiceController {

    @GetMapping("/{invoiceId}")
    public String find(@PathVariable String invoiceId) {
        return "ok";
    }
}
"""

INVOICE_FRONTEND_TS = """\
export function getInvoice(invoiceId: string) {
  return axios.get('http://invoice-svc.local/api/invoices/' + invoiceId);
}
"""


def _manifest_two_repos(tmp_path: Path, with_duplicate: bool) -> Path:
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    (backend / "OrderController.java").write_text(
        ORDER_CONTROLLER_JAVA, encoding="utf-8")
    if with_duplicate:
        (backend / "DuplicateOrderController.java").write_text(
            DUPLICATE_CONTROLLER_JAVA, encoding="utf-8")
    (frontend / "order.service.ts").write_text(FRONTEND_TS, encoding="utf-8")
    manifest = tmp_path / "repos.json"
    manifest.write_text(
        json.dumps({"repos": [
            {"name": "Backend", "path": str(backend)},
            {"name": "Frontend", "path": str(frontend)},
        ]}),
        encoding="utf-8",
    )
    return manifest


def _links_by_path(db: Path) -> list[dict]:
    data = report(db)
    return [a for a in data["contract_links"]
            if isinstance(a.get("object"), dict)
            and a["object"].get("client_path") == "/api/orders/{orderId}"]


def test_cross_repo_ts_link_compatible_declared_only(tmp_path):
    manifest = _manifest_two_repos(tmp_path, with_duplicate=False)
    db = tmp_path / "ts.db"
    ingest_manifest(manifest, db)
    links = _links_by_path(db)
    assert links, report(db)["contract_links"]
    good = [a for a in links
            if a["object"].get("compatible") is True]
    assert good, links
    for link in good:
        assert link["object"].get("destination_verified") is False, link
        assert link["object"].get("equivalent") is False, link
    # wrong method vs the same annotation path is never compatible.
    for link in links:
        obj = link["object"]
        if obj.get("reason", "").startswith("incompatible contract: method mismatch"):
            assert obj.get("compatible") is False, link
    mismatches = [a for a in links
                  if "method mismatch" in a["object"].get("reason", "")]
    assert mismatches, links
    # unused path yields no links at all.
    data = report(db)
    assert not [a for a in data["contract_links"]
                if isinstance(a.get("object"), dict)
                and a["object"].get("client_path") == "/api/shipments/9"], data


def test_cross_repo_ts_link_ambiguous_abstains(tmp_path):
    manifest = _manifest_two_repos(tmp_path, with_duplicate=True)
    db = tmp_path / "ts_dup.db"
    ingest_manifest(manifest, db)
    links = _links_by_path(db)
    assert links, links
    assert all(a["object"].get("compatible") is False for a in links), links
    assert any("ambiguous destination" in a["object"].get("reason", "")
               for a in links), links


def test_cross_repo_ts_missing_base_abstains(tmp_path):
    backend = tmp_path / "b"
    frontend = tmp_path / "f"
    backend.mkdir()
    frontend.mkdir()
    (backend / "InvoiceController.java").write_text(
        INVOICE_CONTROLLER_JAVA, encoding="utf-8")
    (frontend / "broken.ts").write_text(
        "import { environment } from './env';\n"
        "export function f(http: any) {\n"
        "  return http.get(environment.INVOICE_API_URL);\n"
        "}\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "repos.json"
    manifest.write_text(
        json.dumps({"repos": [
            {"name": "B", "path": str(backend)},
            {"name": "F", "path": str(frontend)},
        ]}),
        encoding="utf-8",
    )
    db = tmp_path / "ts_missing.db"
    summary = ingest_manifest(manifest, db)
    assert summary["telemetry"]["client_urls"] == 0, summary
    data = report(db)
    assert data["counts"]["contract_links"] == 0, data


def test_ts_sync_rescan_is_idempotent(tmp_path):
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    (backend / "InvoiceController.java").write_text(
        INVOICE_CONTROLLER_JAVA, encoding="utf-8")
    (frontend / "invoice.client.js").write_text(
        INVOICE_FRONTEND_TS, encoding="utf-8")
    manifest = tmp_path / "repos.json"
    manifest.write_text(
        json.dumps({"repos": [
            {"name": "Backend", "path": str(backend)},
            {"name": "Frontend", "path": str(frontend)},
        ]}),
        encoding="utf-8",
    )
    db = tmp_path / "ts_sync.db"
    first_ingest = ingest_manifest(manifest, db)
    assert first_ingest["telemetry"]["client_urls"] == 1, first_ingest
    first = sync_manifest(manifest, db)
    second = sync_manifest(manifest, db)
    assert second["added"] == 0, second
    data = report(db)
    good = [a for a in data["contract_links"]
            if isinstance(a.get("object"), dict)
            and a["object"].get("client_path") == "/api/invoices/{invoiceId}"
            and a["object"].get("compatible") is True]
    assert good, data["contract_links"]
    assert all(a["object"].get("destination_verified") is False for a in good)
    _ = first


def test_ts_linker_unit_supported_and_wrong_method(tmp_path):
    backend = tmp_path / "OrderController.java"
    backend.write_text(ORDER_CONTROLLER_JAVA, encoding="utf-8")
    ops = emit_annotation_operations(extract_mapping_routes(backend))
    frontend = _write(tmp_path, "order.service.ts", FRONTEND_TS)
    from bo_java.spring_sql_bindings import extract_ts_client_urls as extract_ts
    records = extract_ts(frontend)
    assert len(records) == 3, records
    getter = next(r for r in records if r["client_method"] == "get")
    links = link_client_to_annotation(getter, ops)
    assert links, links
    assert any(link["compatible"] is True for link in links), links
    assert all(link["destination_verified"] is False for link in links), links
    assert all(link["equivalent"] is False for link in links), links
    poster = next(r for r in records if r["client_method"] == "post")
    bad = link_client_to_annotation(poster, ops)
    assert bad, bad
    assert all(link["compatible"] is False for link in bad), bad


# ---------------------------------------------------------------- PetClinic exposed regression


def _find_petclinic_ts():
    """Shape-based discovery (no literal filenames): ``*.ts`` files containing
    an HTTP-client marker (``HttpClient``/``fetch``/``axios``) plus ``*.java``
    files containing a Spring mapping marker. Callers filter further by
    route/path shape, never by filename."""
    ts_clients: list[Path] = []
    java_routes: list[Path] = []
    roots = [Path("/tmp") / "opencode" / "oss-eval-micro"]
    oss_eval = Path("/tmp") / "opencode" / "oss-eval"
    if oss_eval.is_dir():
        try:
            roots.extend(
                p for p in oss_eval.iterdir() if p.is_dir()
            )
        except OSError:
            pass
    for root in roots:
        for pattern, bucket, markers in (
            ("**/*.ts", ts_clients, ("HttpClient", "fetch(", "axios.")),
            ("**/*.java", java_routes, (
                "@GetMapping", "@PostMapping", "@PutMapping",
                "@DeleteMapping", "@PatchMapping", "@RequestMapping",
            )),
        ):
            try:
                paths = list(root.glob(pattern))
            except OSError:
                continue
            for path in paths:
                try:
                    if not path.is_file():
                        continue
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if any(marker in text for marker in markers):
                    bucket.append(path)
    seen: set[str] = set()
    unique_ts: list[Path] = []
    for path in ts_clients:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique_ts.append(path)
    seen_java: set[str] = set()
    unique_java: list[Path] = []
    for path in java_routes:
        key = str(path)
        if key not in seen_java:
            seen_java.add(key)
            unique_java.append(path)
    return {"ts": unique_ts, "java": unique_java}


def test_petclinic_ts_exposed_regression_readonly():
    """REGRESSION (read-only /tmp, no build): discovered HttpClient TS files
    extract verbs/paths; links against discovered routes stay declared-only
    where evidence supports them."""
    found = _find_petclinic_ts()
    ts_files = found["ts"]
    if not ts_files:
        pytest.skip("PetClinic TS clients absent under /tmp (no download attempted)")
    extracted = []
    for path in ts_files:
        try:
            records = extract_ts_client_urls(path)
        except Exception:
            continue
        for record in records:
            assert record["ts"] is True, record
            assert record["client_method"] in (
                "get", "post", "put", "delete", "patch"), record
            assert record["resolved_path"].startswith("/"), record
        extracted.extend(records)
    if not extracted:
        pytest.skip("PetClinic TS client URLs absent (no download attempted)")
    if not found["java"]:
        pytest.skip("Owner routes absent under /tmp (no download attempted)")
    ops = [
        op
        for backend in found["java"]
        for op in emit_annotation_operations(extract_mapping_routes(backend))
    ]
    if not ops:
        pytest.skip("Owner routes absent under /tmp (no download attempted)")
    links = []
    for record in extracted:
        links.extend(link_client_to_annotation(record, ops))
    for link in links:
        if link.get("compatible") is True:
            assert link["destination_verified"] is False, link
            assert link["equivalent"] is False, link


# ---------------------------------------------------------------- review fixes


SIBLING_SERVICE_TS = """\
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';

const ORDER_BASE = "http://order-svc.local/api/orders";

@Injectable({ providedIn: 'root' })
export class OrderService {
  constructor(private http: HttpClient) {}
  queryOrders(filter: string) {
    let url = ORDER_BASE + '/search';
    url += '&q=' + filter;
    return this.http.get(url);
  }
  createOrder(payload: unknown) {
    let url = ORDER_BASE + '/create';
    return this.http.post(url, payload);
  }
  findOrder(orderId: string) {
    const path = ORDER_BASE + '/' + orderId;
    return this.http.get(path);
  }
}
"""


def test_sibling_static_survives_dynamic_sibling(tmp_path):
    """REGRESSION: a reassigned/dynamic use of ``url`` in one method must not
    poison the statically resolvable ``url`` in a sibling method (same var
    name, different methods) nor an unrelated var in the same file."""
    records = extract_ts_client_urls(
        _write(tmp_path, "order.service.ts", SIBLING_SERVICE_TS)
    )
    by_line = {r["call_line"]: r for r in records}
    # queryOrders (line 12): ``url`` rebound via ``+=`` in the same flow.
    assert 12 not in by_line, records
    # createOrder (line 16): same var name, single-assigned here -> emits.
    create = by_line.get(16)
    assert create is not None, records
    assert create["client_method"] == "post", create
    assert create["resolved_path"] == "/api/orders/create", create
    # findOrder (line 20): different var, single-assigned -> emits.
    find = by_line.get(20)
    assert find is not None, records
    assert find["client_method"] == "get", find
    assert find["resolved_path"] == "/api/orders/{orderId}", find


def test_sibling_static_links_declared_only(tmp_path):
    backend = tmp_path / "OrderController.java"
    backend.write_text(ORDER_CONTROLLER_JAVA, encoding="utf-8")
    ops = emit_annotation_operations(extract_mapping_routes(backend))
    records = extract_ts_client_urls(
        _write(tmp_path, "order.service.ts", SIBLING_SERVICE_TS)
    )
    create = next(r for r in records if r["client_method"] == "post")
    links = link_client_to_annotation(create, ops)
    assert links, links
    good = [link for link in links if link.get("compatible") is True]
    assert good, links
    assert all(link["destination_verified"] is False for link in good), links
    assert all(link["equivalent"] is False for link in good), links


def test_httpclient_shadowed_receiver_never_emits(tmp_path):
    src = (
        "export function f(foohttp: any) {\n"
        "  return foohttp.get('/api/orders/9');\n"
        "}\n"
        "export function g(other: any) {\n"
        "  return other.http.get('/api/orders/9');\n"
        "}\n"
    )
    assert extract_ts_client_urls(_write(tmp_path, "shadow.ts", src)) == []


def test_fetch_x_method_header_defaults_to_get(tmp_path):
    src = (
        "const INVOICE_BASE = \"http://invoice-svc.local/api/invoices\";\n"
        "export function search(status: string) {\n"
        "  return fetch(INVOICE_BASE + '/search?status=' + status, "
        "{ headers: { \"X-method\": \"1\" } });\n"
        "}\n"
    )
    records = extract_ts_client_urls(_write(tmp_path, "xmethod.ts", src))
    assert len(records) == 1, records
    assert records[0]["client_method"] == "get", records
    assert records[0].get("default_method") is True, records
    assert "default" in str(records[0].get("method_reason", "")).lower(), records


def test_nested_generics_abstain_by_design(tmp_path):
    src = (
        "export function f(http: any) {\n"
        "  return http.get<Foo<Bar>>('/api/orders/9');\n"
        "}\n"
    )
    assert extract_ts_client_urls(_write(tmp_path, "generics.ts", src)) == []


def test_default_get_link_carries_method_reason(tmp_path):
    frontend = _write(
        tmp_path,
        "pending.ts",
        "export function pending() {\n"
        "  return fetch('/api/invoices/pending');\n"
        "}\n",
    )
    records = extract_ts_client_urls(frontend)
    assert len(records) == 1, records
    assert records[0]["client_method"] == "get", records
    assert records[0].get("default_method") is True, records
    backend = tmp_path / "InvoiceController.java"
    backend.write_text(INVOICE_CONTROLLER_JAVA, encoding="utf-8")
    ops = emit_annotation_operations(extract_mapping_routes(backend))
    links = link_client_to_annotation(records[0], ops)
    assert links, links
    good = [link for link in links if link.get("compatible") is True]
    assert good, links
    for link in good:
        assert link.get("default_method") is True, link
        assert "default" in str(link.get("method_reason", "")).lower(), link
    backend_dir = tmp_path / "b"
    frontend_dir = tmp_path / "f"
    backend_dir.mkdir()
    frontend_dir.mkdir()
    (backend_dir / "InvoiceController.java").write_text(
        INVOICE_CONTROLLER_JAVA, encoding="utf-8")
    (frontend_dir / "pending.ts").write_text(
        "export function pending() {\n"
        "  return fetch('/api/invoices/pending');\n"
        "}\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "repos.json"
    manifest.write_text(
        json.dumps({"repos": [
            {"name": "B", "path": str(backend_dir)},
            {"name": "F", "path": str(frontend_dir)},
        ]}),
        encoding="utf-8",
    )
    db = tmp_path / "ts_method_reason.db"
    ingest_manifest(manifest, db)
    data = report(db)
    stored = [a for a in data["contract_links"]
              if isinstance(a.get("object"), dict)
              and a["object"].get("client_path") == "/api/invoices/pending"
              and a["object"].get("compatible") is True]
    assert stored, data["contract_links"]
    for assertion in stored:
        assert assertion["object"].get("default_method") is True, assertion
        assert "default" in str(
            assertion["object"].get("method_reason", "")).lower(), assertion


# ---------------------------------------------------------------- recall fixes: scope union + typed assigns


OUTER_SHADOWED_TS = """\
const API_HOST = "http://outer.local/api";
export class WidgetService {
  API_HOST = "http://inner-class.local/api";
  constructor(private http: HttpClient) {}
  readWidget(widgetId: string) {
    return this.http.get(API_HOST + '/widgets/' + widgetId);
  }
}
"""


def test_outer_const_sibling_reassignment_abstains(tmp_path):
    """HIGH: a file-level ``const`` plus a same-class reassignment of the same
    base must abstain (2 sites -> dynamic), never resolve to one of them."""
    records = extract_ts_client_urls(
        _write(tmp_path, "widget.service.ts", OUTER_SHADOWED_TS)
    )
    assert records == [], records


LOCAL_SHADOW_TS = """\
const API_HOST = "http://outer.local/api";
export class GadgetService {
  constructor(private http: HttpClient) {}
  readGadget(gadgetId: string) {
    const API_HOST = "http://inner.local/api";
    return this.http.get(API_HOST + '/gadgets/' + gadgetId);
  }
}
"""


def test_local_assignment_shadows_outer_resolves_local(tmp_path):
    """HIGH guard: a method-local single assignment shadows the outer value
    (innermost scope wins) and still resolves."""
    records = extract_ts_client_urls(
        _write(tmp_path, "gadget.service.ts", LOCAL_SHADOW_TS)
    )
    assert len(records) == 1, records
    assert records[0]["resolved_path"] == "/api/gadgets/{gadgetId}", records[0]
    assert records[0]["url"] == (
        "http://ts.local/api/gadgets/{gadgetId}"), records[0]
    # The cited base is the method-local assignment (line 5), not the
    # file-level outer const (line 1).
    assert records[0]["base_span"]["start_line"] == 5, records[0]


TYPED_ASSIGN_TS = """\
const TYPED_BASE: string = "http://typed.local/api/things";
export function getThing(http: any, thingId: string) {
  let url: string = TYPED_BASE + '/' + thingId;
  return http.get(url);
}
"""

TYPED_FIELD_TS = """\
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
@Injectable({ providedIn: 'root' })
export class WidgetTypedService {
  private entityUrl: string = "http://typed.local/api/widgets";
  constructor(private http: HttpClient) {}
  getWidget(widgetId: string) {
    return this.http.get(this.entityUrl + '/' + widgetId);
  }
}
"""


def test_type_annotated_assignments_resolve(tmp_path):
    """MED: ``let x: string = ...`` / ``const X: string = ...`` / typed class
    fields count as assignment sites."""
    records = extract_ts_client_urls(
        _write(tmp_path, "typed.ts", TYPED_ASSIGN_TS)
    )
    assert len(records) == 1, records
    assert records[0]["resolved_path"] == "/api/things/{thingId}", records[0]
    field_records = extract_ts_client_urls(
        _write(tmp_path, "typed-field.service.ts", TYPED_FIELD_TS)
    )
    assert len(field_records) == 1, field_records
    assert field_records[0]["resolved_path"] == (
        "/api/widgets/{widgetId}"), field_records[0]
