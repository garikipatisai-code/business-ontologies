"""Readable Markdown workflow report + ``--format/--out`` CLI (RED-first).

Covers the ``report_markdown`` renderer and the extended ``report``
subcommand over a small synthetic repo (App): default JSON output stays
byte-identical, ``--format md`` renders all six sections deterministically,
``--out`` writes the same bytes that go to stdout, table cells escape
pipes/newlines, and an empty store renders ``None`` sections.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bo_workflow.orchestrator import (
    CONTRACT_LINK,
    _ledger_key,
    _md_cell,
    _md_provenance,
    ingest_manifest,
    invalidate_root,
    record_review,
    report,
    report_markdown,
    sync_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "bo_workflow.py"

APP_SERVICE = """\
package com.example.app;

public class RefundService {
    void applyRefund(Order order, int days, int windowDays) {
        if (days > windowDays) {
            throw new IllegalStateException("outside window");
        }
        order.setRefundStatus("REFUNDED");
    }
}
"""

APP_CONTROLLER = """\
package com.example.app;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/app")
@RestController
public class AppController {

    @PostMapping("/create")
    public String createApp() {
        return "ok";
    }
}
"""

APP_CLIENT = """\
package com.example.app;

import org.springframework.web.client.RestTemplate;

public class AppClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public String callApp() {
        String url = "http://app.local/api/app/create";
        return restTemplate.postForObject(url, new Object(), String.class);
    }
}
"""

APP_YAML = "windowDays: 30\n"

APP_OPENAPI = """\
openapi: 3.0.0
info:
  title: App
  version: 1.0.0
paths:
  /api/app/create:
    post:
      operationId: createApp
      responses:
        '200':
          description: ok
"""


def _build_repo(base: Path) -> Path:
    repo = base / "App"
    repo.mkdir()
    (repo / "RefundService.java").write_text(APP_SERVICE, encoding="utf-8")
    (repo / "AppController.java").write_text(APP_CONTROLLER, encoding="utf-8")
    (repo / "AppClient.java").write_text(APP_CLIENT, encoding="utf-8")
    (repo / "application.yml").write_text(APP_YAML, encoding="utf-8")
    (repo / "openapi.yaml").write_text(APP_OPENAPI, encoding="utf-8")
    return repo


def _manifest(tmp_path: Path, repo: Path) -> Path:
    manifest = tmp_path / "repos.json"
    manifest.write_text(
        json.dumps({"repos": [{"name": "App", "path": str(repo)}]}),
        encoding="utf-8",
    )
    return manifest


def _ingested_db(tmp_path: Path) -> Path:
    repo = _build_repo(tmp_path)
    manifest = _manifest(tmp_path, repo)
    db = tmp_path / "wf.db"
    ingest_manifest(manifest, db)
    return db


SECTIONS = [
    "## Summary",
    "## Active Claims",
    "## Stale Claims",
    "## Cross-Repo Contract Links",
    "## Abstentions & Gaps",
    "## Human Reviews",
]


class TestDefaultJsonUnchanged:
    def test_cli_default_report_is_json_byte_identical(self, tmp_path):
        db = _ingested_db(tmp_path)
        proc = subprocess.run(
            [sys.executable, str(CLI), "report", "--db", str(db)],
            text=True, capture_output=True,
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)  # stdout parses as JSON
        assert set(data) == {
            "db", "counts", "active", "stale",
            "abstentions", "gaps", "contract_links", "reviews",
        }
        expected = json.dumps(
            report(db), indent=2, sort_keys=True, default=str
        )
        assert proc.stdout == expected + "\n"


class TestMarkdownRenderer:
    def _seeded_db(self, tmp_path: Path) -> Path:
        db = _ingested_db(tmp_path)
        data = report(db)
        param = next(e for e in data["active"] if e["kind"] == "params")
        invalidate_root(
            db, "params", param["id"],
            reason="window 30->45", new_value=45,
        )
        record_review(
            db, "App:windowDays", reviewer="qa", reason="looks good"
        )
        return db

    def test_all_six_sections_present(self, tmp_path):
        md = report_markdown(self._seeded_db(tmp_path))
        assert md.startswith("# Workflow Report")
        for section in SECTIONS:
            assert section in md, f"missing section: {section}"

    def test_counts_active_stale_trigger_links_reviews(self, tmp_path):
        db = self._seeded_db(tmp_path)
        data = report(db)
        md = report_markdown(db)
        for metric, count in sorted(data["counts"].items()):
            assert f"| {metric} | {count} |" in md
        assert data["stale"], "seed must produce stale claims"
        for entry in data["stale"]:
            assert _md_cell(entry["name"]) in md
        assert "window 30-&gt;45" in md
        assert "window 30->45" not in md
        assert "destination_verified" in md
        assert "false" in md
        assert "declared-only" in md
        assert "equivalent" in md
        assert "qa" in md and "looks good" in md

    def test_active_grouped_by_kind_deterministic(self, tmp_path):
        db = self._seeded_db(tmp_path)
        first = report_markdown(db)
        second = report_markdown(db)
        assert first == second
        kinds = sorted({e["kind"] for e in report(db)["active"]})
        assert kinds, "expected active claims"
        positions = [first.index(f"### {kind}") for kind in kinds]
        assert positions == sorted(positions), "kinds must be sorted"
        assert "### " in first  # grouped subsections exist

    def test_cli_format_md_matches_api(self, tmp_path):
        db = self._seeded_db(tmp_path)
        proc = subprocess.run(
            [sys.executable, str(CLI), "report", "--db", str(db),
             "--format", "md"],
            text=True, capture_output=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == report_markdown(db) + "\n"

    def test_cli_out_writes_same_bytes_as_stdout(self, tmp_path):
        db = self._seeded_db(tmp_path)
        out = tmp_path / "report.md"
        proc = subprocess.run(
            [sys.executable, str(CLI), "report", "--db", str(db),
             "--format", "md", "--out", str(out)],
            text=True, capture_output=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()
        assert out.read_text(encoding="utf-8") == proc.stdout
        assert out.read_text(encoding="utf-8") == report_markdown(db) + "\n"

    def test_cli_json_out_writes_same_bytes_as_stdout(self, tmp_path):
        db = self._seeded_db(tmp_path)
        out = tmp_path / "report.json"
        proc = subprocess.run(
            [sys.executable, str(CLI), "report", "--db", str(db),
             "--out", str(out)],
            text=True, capture_output=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()
        assert out.read_text(encoding="utf-8") == proc.stdout
        json.loads(out.read_text(encoding="utf-8"))


class TestMarkdownEscaping:
    def test_pipes_backticks_newlines_do_not_break_tables(self, tmp_path):
        db = _ingested_db(tmp_path)
        record_review(
            db, "weird|name `with` ticks", reviewer="qa",
            reason="line one\nline two | pipe",
        )
        invalidate_root(
            db, "params",
            next(
                e["id"] for e in report(db)["active"]
                if e["kind"] == "params"
            ),
            reason="trig|ger\nnewline `tick`", new_value=45,
        )
        md = report_markdown(db)
        assert "weird\\|name `with` ticks" in md
        assert "`with`" in md
        assert "`tick`" in md
        assert "trig\\|ger<br/>newline `tick`" in md
        assert "line one<br/>line two \\| pipe" in md
        for line in md.splitlines():
            if line.startswith("|") and line.endswith("|"):
                assert "\n" not in line
                assert line.count("`") % 2 == 0  # backticks kept raw
        assert _md_cell("[REDACTED]") == "[REDACTED]"

    def test_link_image_html_neutralized_redacted_preserved(self, tmp_path):
        db = _ingested_db(tmp_path)
        record_review(
            db, "link [x](http://evil) test", reviewer="qa",
            reason="[REDACTED] ok",
        )
        record_review(
            db, "img ![p](http://evil/img.png) test", reviewer="qa",
            reason="<img src=x onerror=alert(1)>",
        )
        md = report_markdown(db)
        assert "[x](http://evil)" not in md
        assert "[p](http://evil/img.png)" not in md
        assert "![p]" not in md
        assert "<img" not in md
        assert "\\[x\\]\\(http://evil\\)" in md
        assert "\\[p\\]\\(http://evil/img.png\\)" in md
        assert "&lt;img" in md
        assert "[REDACTED]" in md
        assert "\\[REDACTED\\]" not in md
        for line in md.splitlines():
            if line.startswith("|") and line.endswith("|"):
                assert "\n" not in line


class TestEmptyStore:
    def test_empty_db_renders_none_sections(self, tmp_path):
        db = tmp_path / "fresh.db"
        data = report(db)
        assert all(v == 0 for v in data["counts"].values())
        md = report_markdown(db)
        assert md.startswith("# Workflow Report")
        for section in SECTIONS:
            assert section in md
        assert "None" in md
        assert "| active | 0 |" in md


def _md_section_table_data_rows(md: str, start: str, end: str | None) -> list[str]:
    inside = False
    rows: list[str] = []
    for line in md.splitlines():
        if line.strip() == start:
            inside = True
            continue
        if end is not None and line.strip() == end and inside:
            break
        if not inside:
            continue
        text = line.strip()
        if not (text.startswith("|") and text.endswith("|")):
            continue
        if "---" in text:
            continue
        # Skip header rows (ID/Name/Status, Kind/ID/..., Subject/..., Metric/...)
        lowered = text.lower()
        if lowered.startswith("| metric |") or lowered.startswith("| id |"):
            continue
        if lowered.startswith("| kind |") and "trigger" in lowered:
            continue
        if lowered.startswith("| client |") or lowered.startswith("| subject |"):
            continue
        rows.append(text)
    return rows


class TestExplicitProvenance:
    def test_active_stale_entries_have_provenance_key(self, tmp_path):
        db = _ingested_db(tmp_path)
        data = report(db)
        assert data["active"], "seed must produce active claims"
        for entry in data["active"] + data["stale"]:
            assert "provenance" in entry, f"missing provenance: {entry}"
            prov = entry["provenance"]
            assert prov is None or isinstance(prov, dict)
            if prov is not None:
                assert "repo" in prov and "file" in prov

    def test_provenance_present_for_rules_ops_params(self, tmp_path):
        db = _ingested_db(tmp_path)
        data = report(db)
        by_kind: dict[str, list[dict]] = {}
        for entry in data["active"]:
            by_kind.setdefault(entry["kind"], []).append(entry)
        for kind in ("params", "rules", "operations"):
            assert kind in by_kind, f"missing kind {kind}"
            assert any(
                e["provenance"] is not None for e in by_kind[kind]
            ), f"{kind} should carry provenance"

    def test_markdown_shows_provenance_or_unavailable(self, tmp_path):
        db = _ingested_db(tmp_path)
        data = report(db)
        md = report_markdown(db)
        assert "Provenance" in md
        assert data["active"], "seed must produce active claims"
        for entry in data["active"] + data["stale"]:
            rendered = _md_provenance(entry.get("provenance"))
            assert rendered in md
            assert ":" in rendered or rendered == "source provenance unavailable"

    def test_legacy_row_without_provenance_shows_unavailable(self, tmp_path):
        from bo_ontology import schema as _schema
        from bo_ontology.store import OntologyStore as _Store

        db = _ingested_db(tmp_path)
        with _Store(str(db)) as store:
            store.add_parameter(
                _schema.PolicyParameter(
                    name="Legacy:key", value="1", description="legacy plain"
                )
            )
            store.add_parameter(
                _schema.PolicyParameter(
                    name="Secret:key",
                    value="sk-test-secret-abc12345",
                    description="legacy plain",
                )
            )
        record_review(
            db, "Secret:key", reviewer="qa",
            reason="sk-test-secret-abc12345",
        )
        data = report(db)
        legacy = [e for e in data["active"] if e["name"] == "Legacy:key"]
        assert legacy, "legacy param must appear in active"
        assert legacy[0]["provenance"] is None
        md = report_markdown(db)
        assert "source provenance unavailable" in md
        assert "[REDACTED]" in md
        assert "\\[REDACTED\\]" not in md


class TestCountsAgreement:
    def test_counts_equal_rows_json_and_markdown(self, tmp_path):
        db = _ingested_db(tmp_path)
        param = next(e for e in report(db)["active"] if e["kind"] == "params")
        invalidate_root(
            db, "params", param["id"], reason="window 30->45", new_value=45
        )
        record_review(db, "App:windowDays", reviewer="qa", reason="looks good")
        data = report(db)
        assert data["counts"]["active"] == len(data["active"])
        assert data["counts"]["stale"] == len(data["stale"])
        assert data["counts"]["abstentions"] == len(data["abstentions"])
        assert data["counts"]["gaps"] == len(data["gaps"])
        assert data["counts"]["contract_links"] == len(data["contract_links"])
        assert data["counts"]["reviews"] == len(data["reviews"])
        md = report_markdown(db)
        active_rows = _md_section_table_data_rows(
            md, "## Active Claims", "## Stale Claims"
        )
        assert len(active_rows) == len(data["active"])
        stale_rows = _md_section_table_data_rows(
            md, "## Stale Claims", "## Cross-Repo Contract Links"
        )
        assert len(stale_rows) == len(data["stale"])
        link_rows = _md_section_table_data_rows(
            md, "## Cross-Repo Contract Links", "## Abstentions & Gaps"
        )
        # Links section renders "None" when empty; otherwise one row per link.
        if data["contract_links"]:
            assert len(link_rows) == len(data["contract_links"])
        else:
            assert link_rows == []
        abst_rows = _md_section_table_data_rows(
            md, "## Abstentions & Gaps", "## Human Reviews"
        )
        assert len(abst_rows) == len(data["abstentions"]) + len(data["gaps"])
        review_rows = _md_section_table_data_rows(md, "## Human Reviews", None)
        assert len(review_rows) == len(data["reviews"])


class TestSecretLedgerDedup:
    def test_secret_contract_link_no_duplicate_across_syncs(self, tmp_path):
        secret_path = "/api/sk-test-secret-abc12345"
        repo = tmp_path / "App"
        repo.mkdir()
        (repo / "RefundService.java").write_text(APP_SERVICE, encoding="utf-8")
        (repo / "AppController.java").write_text(
            APP_CONTROLLER, encoding="utf-8"
        )
        (repo / "AppClient.java").write_text(
            APP_CLIENT.replace("/api/app/create", secret_path),
            encoding="utf-8",
        )
        (repo / "application.yml").write_text(APP_YAML, encoding="utf-8")
        (repo / "openapi.yaml").write_text(
            APP_OPENAPI.replace("/api/app/create", secret_path),
            encoding="utf-8",
        )
        manifest = _manifest(tmp_path, repo)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)
        raw = {
            "client_path": secret_path,
            "openapi_path": secret_path,
            "compatible": True,
        }
        stored = {
            "client_path": "[REDACTED]",
            "openapi_path": "[REDACTED]",
            "compatible": True,
        }
        assert _ledger_key(CONTRACT_LINK, raw, {}) == _ledger_key(
            CONTRACT_LINK, stored, {}
        )
        first = report(db)
        assert len(first["contract_links"]) == 1
        sync_manifest(manifest, db)
        second = report(db)
        assert len(second["contract_links"]) == 1
        sync_manifest(manifest, db)
        third = report(db)
        assert len(third["contract_links"]) == 1


# ---------------------------------------------------------------- backing liveness (additive, fail-closed)


BK_SERVICE = APP_SERVICE
BK_CONTROLLER_TMPL = """\
package com.example.bk;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/bk")
@RestController
public class BkController {{

    @PostMapping("/{route}")
    public String handleBk() {{
        return "ok";
    }}
}}
"""

BK_CLIENT_TMPL = """\
package com.example.bk;

import org.springframework.web.client.RestTemplate;

public class BkClient {{

    private final RestTemplate restTemplate = new RestTemplate();

    public String callBk() {{
        String url = "http://bk.local/api/bk/{route}";
        return restTemplate.postForObject(url, new Object(), String.class);
    }}
}}
"""

BK_OPENAPI_TMPL = """\
openapi: 3.0.0
info:
  title: Bk
  version: 1.0.0
paths:
  /api/bk/{route}:
    post:
      operationId: handleBk
      responses:
        '200':
          description: ok
"""


def _write_bk_repo(base: Path, dirname: str, route: str) -> Path:
    repo = base / dirname
    repo.mkdir()
    (repo / "BkService.java").write_text(BK_SERVICE, encoding="utf-8")
    (repo / "BkController.java").write_text(
        BK_CONTROLLER_TMPL.format(route=route), encoding="utf-8"
    )
    (repo / "BkClient.java").write_text(
        BK_CLIENT_TMPL.format(route=route), encoding="utf-8"
    )
    (repo / "application.yml").write_text(APP_YAML, encoding="utf-8")
    (repo / "openapi.yaml").write_text(
        BK_OPENAPI_TMPL.format(route=route), encoding="utf-8"
    )
    return repo


def _manifest_named(tmp_path: Path, *named_repos: tuple[str, Path]) -> Path:
    manifest = tmp_path / "bk_repos.json"
    manifest.write_text(
        json.dumps(
            {"repos": [{"name": n, "path": str(p)} for n, p in named_repos]}
        ),
        encoding="utf-8",
    )
    return manifest


def _backings(data: dict) -> list[dict]:
    return [a.get("backing") or {} for a in data["contract_links"]]


class TestBackingActive:
    def test_active_backing_ingest_no_changes(self, tmp_path):
        repo = _write_bk_repo(tmp_path, "BkActiveRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkActive", repo))
        db = tmp_path / "bk_active.db"
        ingest_manifest(manifest, db)
        data = report(db)
        assert data["contract_links"], "expected contract links"
        for entry in data["contract_links"]:
            assert "backing" in entry, f"missing backing: {entry}"
            backing = entry["backing"]
            assert backing.get("status") == "active", backing
            assert backing.get("reason")
            # stored historical fields preserved
            obj = entry["object"] if isinstance(entry.get("object"), dict) else {}
            assert obj.get("compatible") is True
            assert obj.get("destination_verified") is False
            assert obj.get("equivalent") is False


class TestBackingRenameReproB:
    def test_route_rename_sync_original_stale_new_does_not_validate_old(
        self, tmp_path
    ):
        repo = _write_bk_repo(tmp_path, "BkRenameRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkRename", repo))
        db = tmp_path / "bk_rename.db"
        ingest_manifest(manifest, db)
        before = report(db)
        assert any(
            (a.get("backing") or {}).get("status") == "active"
            for a in before["contract_links"]
        )
        old_objs = [
            dict(a["object"]) for a in before["contract_links"]
            if isinstance(a.get("object"), dict)
        ]
        # rename route: controller + openapi + client all move to renamed
        (repo / "BkController.java").write_text(
            BK_CONTROLLER_TMPL.format(route="renamed"), encoding="utf-8"
        )
        (repo / "openapi.yaml").write_text(
            BK_OPENAPI_TMPL.format(route="renamed"), encoding="utf-8"
        )
        (repo / "BkClient.java").write_text(
            BK_CLIENT_TMPL.format(route="renamed"), encoding="utf-8"
        )
        sync_manifest(manifest, db)
        after = report(db)
        # original links retained (no deletion)
        assert len(after["contract_links"]) >= len(before["contract_links"])
        # every link claiming the OLD path must not be active via the new route
        for entry in after["contract_links"]:
            obj = entry.get("object") or {}
            if obj.get("openapi_path") == "/api/bk/create":
                assert (entry.get("backing") or {}).get("status") != "active", entry
        # at least one old-path link is now stale-or-missing
        assert any(
            (a.get("object") or {}).get("openapi_path") == "/api/bk/create"
            and (a.get("backing") or {}).get("status") == "stale-or-missing"
            for a in after["contract_links"]
        ), after["contract_links"]
        # historical compatible values unchanged for retained old links
        for old in old_objs:
            if old.get("openapi_path") == "/api/bk/create":
                retained = [
                    a for a in after["contract_links"]
                    if isinstance(a.get("object"), dict)
                    and a["object"].get("client_path") == old.get("client_path")
                    and a["object"].get("openapi_path") == old.get("openapi_path")
                ]
                assert retained
                for r in retained:
                    assert r["object"].get("compatible") == old.get("compatible")
                    assert r["object"].get("destination_verified") is False
                    assert r["object"].get("equivalent") is False


class TestBackingDeletion:
    def test_route_deletion_sync_stale(self, tmp_path):
        repo = _write_bk_repo(tmp_path, "BkDeleteRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkDelete", repo))
        db = tmp_path / "bk_delete.db"
        ingest_manifest(manifest, db)
        assert any(
            (a.get("backing") or {}).get("status") == "active"
            for a in report(db)["contract_links"]
        )
        (repo / "BkController.java").unlink()
        (repo / "openapi.yaml").unlink()
        sync_manifest(manifest, db)
        data = report(db)
        assert data["contract_links"]
        for entry in data["contract_links"]:
            obj = entry.get("object") or {}
            if obj.get("openapi_path") == "/api/bk/create":
                assert (entry.get("backing") or {}).get("status") == (
                    "stale-or-missing"
                ), entry


class TestBackingExplicitInvalidateReproC:
    def test_invalidate_operation_stale_json_and_markdown(self, tmp_path):
        from bo_ontology.store import OntologyStore as _Store

        repo = _write_bk_repo(tmp_path, "BkInvalRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkInval", repo))
        db = tmp_path / "bk_inval.db"
        ingest_manifest(manifest, db)
        data = report(db)
        assert any(
            (a.get("backing") or {}).get("status") == "active"
            for a in data["contract_links"]
        )
        with _Store(str(db)) as store:
            ops = store.all("operations")
            assert ops, "expected operations"
            # Deterministic: select the op backing the targeted link via
            # the same repo+path+method identity the implementation uses
            # (not ops[0], which assumes a single op).
            link_obj = next(
                (a.get("object") or {})
                for a in data["contract_links"]
                if isinstance(a.get("object"), dict)
                and isinstance((a.get("object") or {}).get("operation_repo"), str)
                and (a.get("object") or {}).get("operation_repo")
                and isinstance((a.get("object") or {}).get("openapi_path"), str)
                and (a.get("object") or {}).get("openapi_path")
                and isinstance((a.get("object") or {}).get("operation_method"), str)
                and (a.get("object") or {}).get("operation_method").strip()
            )
            want_repo = link_obj["operation_repo"]
            want_path = link_obj["openapi_path"]
            want_method = link_obj["operation_method"].strip().lower()

            def _op_matches(row: dict) -> bool:
                try:
                    body = json.loads(row.get("description") or "{}")
                except (TypeError, ValueError):
                    return False
                if not isinstance(body, dict):
                    return False
                prov = body.get("provenance") or {}
                if not isinstance(prov, dict):
                    return False
                meths = [
                    m.strip().lower()
                    for m in (body.get("http_methods") or [])
                    if isinstance(m, str) and m.strip()
                ]
                return (
                    prov.get("repo") == want_repo
                    and body.get("path") == want_path
                    and meths == [want_method]
                )

            cands = [o for o in ops if _op_matches(o)]
            assert cands, f"no op matches targeted link {link_obj}"
            target = sorted(cands, key=lambda r: r["id"])[0]
            op_id = target["id"]
        invalidate_root(db, "operations", op_id, reason="bk explicit retire")
        after = report(db)
        assert after["contract_links"]
        for entry in after["contract_links"]:
            assert (entry.get("backing") or {}).get("status") == (
                "stale-or-missing"
            ), entry
            assert (entry.get("backing") or {}).get("reason")
        md = report_markdown(db)
        assert "stale-or-missing" in md

    def test_two_stale_candidates_join_triggers(self, tmp_path):
        from bo_ontology.store import OntologyStore as _Store

        # Two stale ops share one repo+path+method: joined triggers stay
        # deterministic and complete (both causes cited, sorted).
        repo = tmp_path / "BkJoinRepo"
        repo.mkdir()
        (repo / "BkService.java").write_text(BK_SERVICE, encoding="utf-8")
        (repo / "BkControllerOne.java").write_text(
            BK_CONTROLLER_TMPL.format(route="create").replace(
                "BkController", "BkControllerOne"
            ),
            encoding="utf-8",
        )
        (repo / "BkControllerTwo.java").write_text(
            BK_CONTROLLER_TMPL.format(route="create").replace(
                "BkController", "BkControllerTwo"
            ),
            encoding="utf-8",
        )
        (repo / "BkClient.java").write_text(
            BK_CLIENT_TMPL.format(route="create"), encoding="utf-8"
        )
        (repo / "application.yml").write_text(APP_YAML, encoding="utf-8")
        (repo / "openapi.yaml").write_text(
            BK_OPENAPI_TMPL.format(route="create"), encoding="utf-8"
        )
        manifest = _manifest_named(tmp_path, ("BkJoin", repo))
        db = tmp_path / "bk_join.db"
        ingest_manifest(manifest, db)
        with _Store(str(db)) as store:
            ops = sorted(store.all("operations"), key=lambda r: r["id"])
            assert len(ops) >= 2, "expected two ops same route"
            targets = ops[:2]
        invalidate_root(
            db, "operations", targets[0]["id"], reason="bk retire one"
        )
        invalidate_root(
            db, "operations", targets[1]["id"], reason="bk retire two"
        )
        data = report(db)
        assert data["contract_links"]
        for entry in data["contract_links"]:
            reason = (entry.get("backing") or {}).get("reason") or ""
            assert (entry.get("backing") or {}).get("status") == (
                "stale-or-missing"
            ), entry
            assert "bk retire one" in reason, reason
            assert "bk retire two" in reason, reason


class TestBackingCrossRepoDuplicate:
    def test_same_route_other_repo_does_not_rescue(self, tmp_path):
        repo_a = _write_bk_repo(tmp_path, "BkDupARepo", "create")
        repo_b = _write_bk_repo(tmp_path, "BkDupBRepo", "create")
        web = tmp_path / "BkDupWebRepo"
        web.mkdir()
        (web / "BkClient.java").write_text(
            BK_CLIENT_TMPL.format(route="create"), encoding="utf-8"
        )
        (web / "application.yml").write_text(APP_YAML, encoding="utf-8")
        manifest = _manifest_named(
            tmp_path,
            ("BkDupA", repo_a),
            ("BkDupB", repo_b),
            ("BkDupWeb", web),
        )
        db = tmp_path / "bk_dup.db"
        ingest_manifest(manifest, db)
        before = report(db)
        assert before["contract_links"]
        # pin: find links whose backing points at BkDupA
        from bo_ontology.store import OntologyStore as _Store

        with _Store(str(db)) as store:
            ops = store.all("operations")
            op_a = next(o for o in ops if "BkDupA" in (o.get("name") or ""))
            op_a_id = op_a["id"]
        invalidate_root(db, "operations", op_a_id, reason="retire BkDupA route")
        after = report(db)
        # links pinned to BkDupA must be stale even though BkDupB identical active
        pinned_stale = [
            a for a in after["contract_links"]
            if isinstance(a.get("object"), dict)
            and a["object"].get("operation_repo") == "BkDupA"
        ]
        assert pinned_stale, after["contract_links"]
        for entry in pinned_stale:
            assert (entry.get("backing") or {}).get("status") == (
                "stale-or-missing"
            ), entry


class TestBackingReplacementNoTransfer:
    def test_replacement_only_backs_matching_links_reviews_not_transferred(
        self, tmp_path
    ):
        repo = _write_bk_repo(tmp_path, "BkReplRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkRepl", repo))
        db = tmp_path / "bk_repl.db"
        ingest_manifest(manifest, db)
        before = report(db)
        old_link = next(
            a for a in before["contract_links"]
            if (a.get("object") or {}).get("openapi_path") == "/api/bk/create"
        )
        old_obj = dict(old_link["object"])
        record_review(
            db, "BkRepl:windowDays", reviewer="qa", reason="old review intact"
        )
        # rename route + repoint client (replacement same repo+method new path)
        (repo / "BkController.java").write_text(
            BK_CONTROLLER_TMPL.format(route="renamed"), encoding="utf-8"
        )
        (repo / "openapi.yaml").write_text(
            BK_OPENAPI_TMPL.format(route="renamed"), encoding="utf-8"
        )
        (repo / "BkClient.java").write_text(
            BK_CLIENT_TMPL.format(route="renamed"), encoding="utf-8"
        )
        sync_manifest(manifest, db)
        after = report(db)
        # old link retained but stale (no validity transfer via replacement)
        old_retained = [
            a for a in after["contract_links"]
            if isinstance(a.get("object"), dict)
            and a["object"].get("openapi_path") == "/api/bk/create"
        ]
        assert old_retained
        for entry in old_retained:
            assert (entry.get("backing") or {}).get("status") == (
                "stale-or-missing"
            ), entry
            assert entry["object"].get("compatible") == old_obj.get("compatible")
        # replacement's own new links (re-pointed client) are separate rows
        new_links = [
            a for a in after["contract_links"]
            if isinstance(a.get("object"), dict)
            and a["object"].get("openapi_path") == "/api/bk/renamed"
        ]
        assert new_links, "expected new links for re-pointed client"
        # human reviews never transfer to new subjects
        assert after["reviews"]
        assert all(
            r.get("subject") != "BkRepl:renamed-route" for r in after["reviews"]
        )
        from bo_ontology.store import OntologyStore as _Store

        with _Store(str(db)) as store:
            assert store.reviews("BkRepl:renamed-route") == []
            assert store.reviews("BkRepl:windowDays")


class TestBackingMissingAmbiguousFailClosed:
    def test_missing_identity_fails_closed(self, tmp_path):
        from bo_ontology import schema as _schema
        from bo_ontology.store import OntologyStore as _Store

        repo = _write_bk_repo(tmp_path, "BkMissRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkMiss", repo))
        db = tmp_path / "bk_miss.db"
        ingest_manifest(manifest, db)
        with _Store(str(db)) as store:
            store.add_assertion(
                _schema.Assertion(
                    subject="link:legacy:/api/bk/create:9",
                    predicate=CONTRACT_LINK,
                    object={
                        "client_path": "/api/bk/create",
                        "openapi_path": "/api/bk/create",
                        "compatible": True,
                        "equivalent": False,
                        "destination_verified": False,
                        "reason": "legacy without operation ref",
                    },
                )
            )
        data = report(db)
        legacy = [
            a for a in data["contract_links"]
            if a.get("subject") == "link:legacy:/api/bk/create:9"
        ]
        assert legacy, "legacy link must be retained"
        backing = legacy[0].get("backing") or {}
        assert backing.get("status") == "stale-or-missing", backing
        assert backing.get("reason")

    def test_ambiguous_duplicate_same_repo_fails_closed(self, tmp_path):
        repo = tmp_path / "BkAmbigRepo"
        repo.mkdir()
        (repo / "BkService.java").write_text(BK_SERVICE, encoding="utf-8")
        # two controllers same repo same path+method (different files)
        (repo / "BkControllerOne.java").write_text(
            BK_CONTROLLER_TMPL.format(route="create").replace(
                "BkController", "BkControllerOne"
            ),
            encoding="utf-8",
        )
        (repo / "BkControllerTwo.java").write_text(
            BK_CONTROLLER_TMPL.format(route="create").replace(
                "BkController", "BkControllerTwo"
            ),
            encoding="utf-8",
        )
        (repo / "BkClient.java").write_text(
            BK_CLIENT_TMPL.format(route="create"), encoding="utf-8"
        )
        (repo / "application.yml").write_text(APP_YAML, encoding="utf-8")
        (repo / "openapi.yaml").write_text(
            BK_OPENAPI_TMPL.format(route="create"), encoding="utf-8"
        )
        manifest = _manifest_named(tmp_path, ("BkAmbig", repo))
        db = tmp_path / "bk_ambig.db"
        ingest_manifest(manifest, db)
        data = report(db)
        assert len(data["contract_links"]) >= 1
        # with two active ops same repo+path+method, pinned links are ambiguous
        assert any(
            (a.get("backing") or {}).get("status") == "stale-or-missing"
            and "ambiguous" in ((a.get("backing") or {}).get("reason") or "")
            for a in data["contract_links"]
        ), [a.get("backing") for a in data["contract_links"]]

    def test_redacted_identity_fails_closed_distinct(self, tmp_path):
        from bo_ontology import schema as _schema
        from bo_ontology.store import OntologyStore as _Store

        secret_path = "/api/sk-test-secret-abc12345"
        repo = _write_bk_repo(tmp_path, "BkRedactRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkRedact", repo))
        db = tmp_path / "bk_redact.db"
        ingest_manifest(manifest, db)
        with _Store(str(db)) as store:
            store.add_assertion(
                _schema.Assertion(
                    subject="link:legacy-secret:/api/bk/create:9",
                    predicate=CONTRACT_LINK,
                    object={
                        "client_path": secret_path,
                        "openapi_path": secret_path,
                        "operation_repo": "BkRedact",
                        "operation_method": "post",
                        "compatible": True,
                        "equivalent": False,
                        "destination_verified": False,
                        "reason": "secret-shaped path link",
                    },
                )
            )
        data = report(db)
        legacy = [
            a for a in data["contract_links"]
            if a.get("subject") == "link:legacy-secret:/api/bk/create:9"
        ]
        assert legacy, "secret-shaped link must be retained"
        backing = legacy[0].get("backing") or {}
        assert backing.get("status") == "stale-or-missing", backing
        reason = backing.get("reason") or ""
        assert "redacted operation identity" in reason, reason
        assert "fail closed" in reason, reason
        assert "missing operation identity" not in reason, reason
        assert "sk-test-secret" not in reason, reason
        md = report_markdown(db)
        assert "sk-test-secret" not in md
        assert "redacted operation identity" in md


class TestBackingCountsDeterministicFields:
    def test_counts_agree_deterministic_fields_preserved(self, tmp_path):
        repo = _write_bk_repo(tmp_path, "BkCountRepo", "create")
        manifest = _manifest_named(tmp_path, ("BkCount", repo))
        db = tmp_path / "bk_count.db"
        ingest_manifest(manifest, db)
        first = report(db)
        second = report(db)
        assert first["counts"]["contract_links"] == len(first["contract_links"])
        assert second["counts"]["contract_links"] == len(second["contract_links"])
        # deterministic modulo db path (same db -> byte-identical sans db key)
        assert json.dumps(first, sort_keys=True, default=str) == json.dumps(
            second, sort_keys=True, default=str
        )
        md_first = report_markdown(db)
        md_second = report_markdown(db)
        assert md_first == md_second
        # Markdown shows backing explicitly on every link row
        assert "Backing" in md_first
        assert "stale-or-missing" in md_first or "active" in md_first
        # Per-row: every contract-link table row carries a non-empty
        # Backing cell (counts stable: one row per link).
        section = md_first.split("## Cross-Repo Contract Links", 1)[1]
        tail = section.split("\n## ", 1)[0]
        table_lines = [ln for ln in tail.splitlines() if ln.startswith("| ")]
        assert len(table_lines) >= 2, table_lines
        header = [
            h.strip() for h in table_lines[0].strip().strip("|").split("|")
        ]
        assert header[-1] == "Backing", header
        body_rows = table_lines[2:]
        assert len(body_rows) == len(first["contract_links"]), (
            len(body_rows),
            len(first["contract_links"]),
        )
        for row in body_rows:
            inner = row.strip().strip("|").replace("\\|", "\x00")
            cells = [c.strip().replace("\x00", "\\|") for c in inner.split("|")]
            assert len(cells) == len(header) == 7, row
            backing_cell = cells[-1]
            assert backing_cell, row
            assert (
                "active" in backing_cell or "stale-or-missing" in backing_cell
            ), row
        # prior JSON fields retain values
        for entry in first["contract_links"]:
            obj = entry.get("object") or {}
            assert "compatible" in obj
            assert "destination_verified" in obj
            assert "equivalent" in obj

