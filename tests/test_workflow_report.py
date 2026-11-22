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
    _md_cell,
    ingest_manifest,
    invalidate_root,
    record_review,
    report,
    report_markdown,
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
