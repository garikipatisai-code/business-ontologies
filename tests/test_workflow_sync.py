"""Source-diff detection + re-ingestion (sync_manifest + sync CLI).

Covers ``bo_workflow`` sync over two synthetic Java repos (Gamma/DeltaSvc):
value change -> invalidate root + cascade, idempotent re-run, source-file
removal, whitespace/line-shift tolerance, and malformed-manifest negatives.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bo_ontology.store import OntologyStore
from bo_workflow import orchestrator as orch
from bo_workflow.orchestrator import (
    ingest_manifest,
    load_manifest,
    record_review,
    report,
    sync_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "bo_workflow.py"


# ---------------------------------------------------------------- fixtures


GAMMA_CONTROLLER = """\
package com.example.gamma;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/gamma")
@RestController
public class GammaController {

    @PostMapping("/create")
    public String createGamma() {
        return "ok";
    }
}
"""

GAMMA_SERVICE = """\
package com.example.gamma;

public class RefundService {
    void applyRefund(Order order, int days, int windowDays) {
        if (days > windowDays) {
            throw new IllegalStateException("outside window");
        }
        order.setRefundStatus("REFUNDED");
    }
}
"""

GAMMA_YAML = "windowDays: 30\n"

DELTA_CONTROLLER = """\
package com.example.delta;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DeltaController {

    private static final String PATH_CONST = "/dynamic";

    @GetMapping(PATH_CONST)
    public String byConst() {
        return "ok";
    }
}
"""

DELTA_SERVICE = """\
package com.example.delta;

public class LedgerService {
    void postLedger(Ledger ledger, int amount, int limit) {
        if (amount > limit) {
            throw new IllegalStateException("over limit");
        }
        ledger.setLedgerStatus("POSTED");
    }
}
"""

DELTA_YAML = "limit: 9\n"


def _build_repos(base: Path) -> tuple[Path, Path]:
    gamma = base / "Gamma"
    delta = base / "DeltaSvc"
    gamma.mkdir()
    delta.mkdir()
    (gamma / "GammaController.java").write_text(
        GAMMA_CONTROLLER, encoding="utf-8")
    (gamma / "RefundService.java").write_text(GAMMA_SERVICE, encoding="utf-8")
    (gamma / "application.yml").write_text(GAMMA_YAML, encoding="utf-8")
    (delta / "DeltaController.java").write_text(
        DELTA_CONTROLLER, encoding="utf-8")
    (delta / "LedgerService.java").write_text(DELTA_SERVICE, encoding="utf-8")
    (delta / "application.yml").write_text(DELTA_YAML, encoding="utf-8")
    return gamma, delta


def _manifest(tmp_path: Path, gamma: Path, delta: Path) -> Path:
    manifest = tmp_path / "repos.json"
    manifest.write_text(
        json.dumps(
            {
                "repos": [
                    {"name": "Gamma", "path": str(gamma)},
                    {"name": "DeltaSvc", "path": str(delta)},
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _row_by_name(store: OntologyStore, kind: str, name: str) -> dict:
    rows = [r for r in store.all(kind) if r.get("name") == name]
    assert len(rows) == 1, f"expected one {kind} {name!r}, got {len(rows)}"
    return rows[0]


def _actions(store: OntologyStore, kind: str, record_id) -> list[str]:
    return [e.get("action") for e in store.history(kind, record_id)]


def _counts(db: Path) -> dict[str, int]:
    with OntologyStore(str(db)) as store:
        out = {
            k: len(store.all(k))
            for k in ("params", "rules", "transitions",
                      "operations", "assertions")
        }
        out["history"] = len(store.history())
        out["reviews"] = len(store.reviews())
    return out


def _gamma_names(store: OntologyStore) -> tuple[str, str]:
    rule_names = sorted(r["name"] for r in store.all("rules"))
    gamma_rule = next(n for n in rule_names if n.startswith("Gamma:"))
    return gamma_rule, next(
        n for n in rule_names if n.startswith("DeltaSvc:"))


# ---------------------------------------------------------------- E2E value change


class TestSyncValueChange:
    def test_param_edit_invalidates_root_and_cascades(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)

        with OntologyStore(str(db)) as store:
            gamma_rule, delta_rule = _gamma_names(store)
            gamma_param = _row_by_name(store, "params", "Gamma:windowDays")
            delta_param = _row_by_name(store, "params", "DeltaSvc:limit")
            gamma_trans = [
                t for t in store.all("transitions")
                if json.loads(t["trigger"])["provenance"]["repo"] == "Gamma"
            ]
            assert len(gamma_trans) == 1

        review = record_review(
            db, gamma_rule, reviewer="qa", reason="guard matches policy")
        assert review["id"]

        (gamma / "application.yml").write_text(
            "windowDays: 45\n", encoding="utf-8")
        summary = sync_manifest(manifest, db)

        assert summary["added"] == 1
        roots = {(r["kind"], r["id"]) for r in summary["invalidated_roots"]}
        assert ("params", gamma_param["id"]) in roots
        root_reasons = {
            (r["kind"], r["id"]): r["reason"]
            for r in summary["invalidated_roots"]
        }
        reason = root_reasons[("params", gamma_param["id"])]
        assert "sync:" in reason and "application.yml" in reason

        cascaded = {(c["kind"], c["id"]) for c in summary["cascaded_stale"]}
        with OntologyStore(str(db)) as store:
            gamma_rule_row = _row_by_name(store, "rules", gamma_rule)
            assert ("rules", gamma_rule_row["id"]) in cascaded
            assert ("transitions", gamma_trans[0]["id"]) in cascaded
            assert gamma_rule_row["status"] == "stale"
            assert store.get(
                "transitions", gamma_trans[0]["id"])["status"] == "stale"

            # New generation carries the fresh value and stays active.
            fresh_params = [r for r in store.all("params")
                            if r.get("name") == "Gamma:windowDays"]
            assert len(fresh_params) == 2
            fresh = next(
                r for r in fresh_params if r["id"] != gamma_param["id"])
            assert fresh["value"] == "45"
            assert _actions(store, "params", fresh["id"]) == ["insert"]

            # Root history traces insert -> invalidate.
            assert _actions(store, "params", gamma_param["id"]) == [
                "insert", "invalidate"]

            # Prior review stays inspectable; unrelated rows stay active.
            prior = store.reviews(gamma_rule)
            assert prior and any(r.get("reviewer") == "qa" for r in prior)
            assert store.get("params", delta_param["id"])["value"] == "9"
            delta_rule_row = _row_by_name(store, "rules", delta_rule)
            assert delta_rule_row["status"] == "proposed"
            assert _actions(store, "params", delta_param["id"]) == ["insert"]

        data = report(db)
        stale = {(e["kind"], e.get("name")): e.get("trigger")
                 for e in data["stale"]}
        assert ("rules", gamma_rule) in stale
        assert stale[("rules", gamma_rule)]
        assert any(r.get("reviewer") == "qa" for r in data["reviews"])

    def test_cli_ingest_sync_report(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "cli.db"

        ingest = subprocess.run(
            [sys.executable, str(CLI), "ingest", "--manifest", str(manifest),
             "--db", str(db)],
            text=True, capture_output=True,
        )
        assert ingest.returncode == 0, ingest.stderr

        (gamma / "application.yml").write_text(
            "windowDays: 45\n", encoding="utf-8")
        sync = subprocess.run(
            [sys.executable, str(CLI), "sync", "--manifest", str(manifest),
             "--db", str(db)],
            text=True, capture_output=True,
        )
        assert sync.returncode == 0, sync.stderr
        summary = json.loads(sync.stdout)
        assert summary["added"] == 1
        assert summary["invalidated_roots"]

        rep = subprocess.run(
            [sys.executable, str(CLI), "report", "--db", str(db)],
            text=True, capture_output=True,
        )
        assert rep.returncode == 0, rep.stderr
        data = json.loads(rep.stdout)
        assert data["stale"]
        assert any(e.get("trigger") for e in data["stale"])


# ---------------------------------------------------------------- idempotency


class TestSyncIdempotent:
    def test_clean_rerun_changes_nothing(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)
        before = _counts(db)

        first = sync_manifest(manifest, db)
        assert first["added"] == 0
        assert first["invalidated_roots"] == []
        assert first["cascaded_stale"] == []
        assert first["unchanged"] > 0

        second = sync_manifest(manifest, db)
        assert second["added"] == 0
        assert second["invalidated_roots"] == []
        assert second["cascaded_stale"] == []

        assert _counts(db) == before


# ---------------------------------------------------------------- removal


class TestSyncRemoval:
    def test_deleted_source_file_invalidates_roots(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)
        with OntologyStore(str(db)) as store:
            gamma_rule, delta_rule = _gamma_names(store)
            delta_rows = {
                k: [r for r in store.all(k)
                    if (r.get("name") or "").startswith("DeltaSvc:")
                    or (k == "transitions" and json.loads(
                        r["trigger"])["provenance"]["repo"] == "DeltaSvc")]
                for k in ("params", "rules", "transitions", "operations")
            }
            delta_rule_id = _row_by_name(
                store, "rules", delta_rule)["id"]
            delta_trans_id = [
                t for t in store.all("transitions")
                if json.loads(t["trigger"])["provenance"]["repo"]
                == "DeltaSvc"
            ][0]["id"]
        before_assertions = _counts(db)["assertions"]

        (delta / "LedgerService.java").unlink()
        summary = sync_manifest(manifest, db)

        roots = {(r["kind"], r["id"]) for r in summary["invalidated_roots"]}
        assert ("rules", delta_rule_id) in roots
        assert ("transitions", delta_trans_id) in roots
        assert any("removed" in r["reason"]
                   for r in summary["invalidated_roots"])

        with OntologyStore(str(db)) as store:
            assert store.get("rules", delta_rule_id)["status"] == "stale"
            assert store.get(
                "transitions", delta_trans_id)["status"] == "stale"
            # Others untouched: Gamma rule active, Delta param (yml kept) live.
            assert _row_by_name(store, "rules", gamma_rule)["status"] \
                == "proposed"
            delta_param = _row_by_name(store, "params", "DeltaSvc:limit")
            assert _actions(store, "params", delta_param["id"]) == ["insert"]
        # Ledger is append-only: no new assertion rows for a removal.
        assert _counts(db)["assertions"] == before_assertions


# ---------------------------------------------------------------- whitespace tolerance


class TestSyncWhitespace:
    def test_line_shift_without_span_change_is_noop(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)
        before = _counts(db)

        service = gamma / "RefundService.java"
        service.write_text(
            "\n\n// license header added above the fact\n" + GAMMA_SERVICE,
            encoding="utf-8",
        )
        (delta / "DeltaController.java").write_text(
            DELTA_CONTROLLER + "\n\n// trailing note\n", encoding="utf-8")

        summary = sync_manifest(manifest, db)
        assert summary["added"] == 0
        assert summary["invalidated_roots"] == []
        assert summary["cascaded_stale"] == []
        assert _counts(db) == before


# ---------------------------------------------------------------- negatives


class TestSyncNegatives:
    def test_malformed_manifest_leaves_db_untouched(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)
        before = _counts(db)

        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="[Mm]alformed"):
            sync_manifest(bad, db)

        ghost = tmp_path / "ghost.json"
        ghost.write_text(
            json.dumps(
                {"repos": [{"name": "Ghost",
                            "path": str(tmp_path / "nope")}]}),
            encoding="utf-8",
        )
        with pytest.raises(FileNotFoundError, match="not found"):
            sync_manifest(ghost, db)

        with pytest.raises(ValueError, match="JSON"):
            sync_manifest(tmp_path / "repos.yaml", db)

        assert _counts(db) == before

    def test_cli_sync_bad_manifest_exits_2(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "cli.db"
        ingest_manifest(manifest, db)

        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(CLI), "sync", "--manifest", str(bad),
             "--db", str(db)],
            text=True, capture_output=True,
        )
        assert proc.returncode == 2

        ghost = tmp_path / "ghost.json"
        ghost.write_text(
            json.dumps(
                {"repos": [{"name": "Ghost",
                            "path": str(tmp_path / "nope")}]}),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(CLI), "sync", "--manifest", str(ghost),
             "--db", str(db)],
            text=True, capture_output=True,
        )
        assert proc.returncode == 2
        assert "not found" in proc.stderr.lower()


# ---------------------------------------------------------------- secret redaction


class TestSyncSecretRedaction:
    def test_param_secret_never_in_reason_or_summary(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        secret_old = "sk-abcdefgh12345678XYZ"
        secret_new = "sk-zzzzzzzz87654321XYZ"
        (gamma / "application.yml").write_text(
            f"windowDays: {secret_old}\n", encoding="utf-8")
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)

        (gamma / "application.yml").write_text(
            f"windowDays: {secret_new}\n", encoding="utf-8")
        summary = sync_manifest(manifest, db)

        blob = json.dumps(summary, default=str)
        assert "sk-" not in blob
        assert secret_old not in blob and secret_new not in blob
        for root in summary["invalidated_roots"]:
            assert "sk-" not in root["reason"]
            assert "value changed" in root["reason"]

        with OntologyStore(str(db)) as store:
            for row in store.all("params"):
                for entry in store.history("params", row["id"]):
                    detail = entry.get("detail", "")
                    assert secret_old not in str(detail)
                    assert secret_new not in str(detail)
                    assert "sk-" not in str(detail)


# ---------------------------------------------------------------- symlink + absolute cap


class TestSyncRepoTreeGuards:
    def test_symlink_java_rejected_fail_closed(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        link = gamma / "Link.java"
        try:
            link.symlink_to(gamma / "GammaController.java")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported on this platform")
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        with pytest.raises(ValueError, match="symlink"):
            ingest_manifest(manifest, db)
        with pytest.raises(ValueError, match="symlink"):
            sync_manifest(manifest, db)

    def test_absolute_paths_supported_but_capped(
            self, tmp_path, monkeypatch):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        # Absolute manifest paths keep working per the module contract.
        ingest_manifest(manifest, db)
        # Cap the walk fail-closed (monkeypatched for speed).
        monkeypatch.setattr(orch, "_MAX_REPO_FILES", 1)
        with pytest.raises(ValueError, match="too many files"):
            ingest_manifest(manifest, tmp_path / "capped.db")


# ---------------------------------------------------------------- fingerprints / identity / ledger


class TestSyncFingerprintsAndKeys:
    def test_rule_fingerprint_covers_guard_and_outcome(self):
        base = {"condition": "days > windowDays",
                "guard": "isAdmin", "failure_outcome": "rejection"}
        same = dict(base)
        diff_guard = dict(base, guard="otherGuard")
        diff_outcome = dict(base, failure_outcome="allow")
        assert (orch._fresh_fingerprint("rules", {"record": base})
                == orch._fresh_fingerprint("rules", {"record": same}))
        assert (orch._fresh_fingerprint("rules", {"record": base})
                != orch._fresh_fingerprint("rules", {"record": diff_guard}))
        assert (orch._fresh_fingerprint("rules", {"record": base})
                != orch._fresh_fingerprint(
                    "rules", {"record": diff_outcome}))

    def test_transition_fingerprint_covers_prereqs_and_coverage(self):
        base = {"target_status": "POSTED", "guard": "amount > limit",
                "prerequisites": [{"guard": "isAdmin"}],
                "coverage": "contained-with-prerequisites"}
        diff_cov = dict(base, coverage="contained")
        diff_pre = dict(
            base, prerequisites=[{"guard": "other"}])
        assert (orch._fresh_fingerprint("transitions", {"record": base})
                != orch._fresh_fingerprint(
                    "transitions", {"record": diff_cov}))
        assert (orch._fresh_fingerprint("transitions", {"record": base})
                != orch._fresh_fingerprint(
                    "transitions", {"record": diff_pre}))

    def test_condition_line_markers_stripped_in_fingerprint(self):
        a = {"condition": "x > L10", "guard": "g", "failure_outcome": "o"}
        b = {"condition": "x > L20", "guard": "g", "failure_outcome": "o"}
        assert (orch._fresh_fingerprint("rules", {"record": a})
                == orch._fresh_fingerprint("rules", {"record": b}))

    def test_empty_target_normalized_to_unknown(self):
        prov = {"file": "/r/a.java"}
        fresh_empty = orch._sync_identity(
            "transitions", "R", "/r", {"target_status": ""}, prov)
        fresh_none = orch._sync_identity(
            "transitions", "R", "/r", {"target_status": None}, prov)
        assert fresh_empty[3] == "unknown"
        assert fresh_none[3] == "unknown"

    def test_none_handling_never_fabricates_none_string(self):
        prov = {"file": "/r/a.java"}
        assert orch._sync_identity(
            "params", "R", "/r", {"key": None}, {"file": "/r/a.java"}
        ) is None
        assert orch._sync_identity(
            "rules", "R", "/r", {"condition": None}, prov
        ) is None
        row = {"name": "R:k", "description": json.dumps({"repo": "R"})}
        # Missing key falls back to name split; missing file -> "" rel.
        ident = orch._stored_identity("params", row, {"R": "/r"})
        assert ident == ("params", "R", "", "k")
        bad = {"name": "NoColon", "description": json.dumps({})}
        assert orch._stored_identity("params", bad, {"R": "/r"}) is None

    def test_redacted_rule_regex_non_greedy(self):
        row = {
            "name": "Gamma:foo.java:L10:cond:L20:tail",
            "value": "cond:L20:tail",
            "supporting": json.dumps(
                [{"repo": "Gamma", "file": "[REDACTED]"}]),
        }
        ident = orch._stored_identity(
            "rules", row, {"Gamma": "/repo"})
        assert ident[2] == "foo.java"

    def test_ledger_keys_discriminate_distinct_facts(self):
        paths = {"R": "/r"}
        unguarded_a = {"kind": "unguarded_transition", "repo": "R",
                       "target": "POSTED", "file": "/r/a.java",
                       "coverage": "contained", "prerequisites": []}
        unguarded_b = dict(unguarded_a, coverage="none")
        ka, _ = orch._ledger_key("abstention", unguarded_a, paths)
        kb, _ = orch._ledger_key("abstention", unguarded_b, paths)
        assert ka != kb
        route_a = {"kind": "unlinkable_route", "repo": "R",
                   "handler": "h", "file": "/r/a.java",
                   "path": "/a", "http_methods": ["GET"]}
        route_b = dict(route_a, path="/b")
        ra, _ = orch._ledger_key("abstention", route_a, paths)
        rb, _ = orch._ledger_key("abstention", route_b, paths)
        assert ra != rb

    def test_fileless_fallback_matches_redacted_stored(self, tmp_path):
        gamma, delta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, gamma, delta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)
        with OntologyStore(str(db)) as store:
            params = [r for r in store.all("params")
                      if r.get("name") == "Gamma:windowDays"]
            assert len(params) == 1
            fresh_ident = orch._sync_identity(
                "params", "Gamma", str(gamma),
                {"key": "windowDays"},
                {"file": str(gamma / "application.yml"), "key": "windowDays"},
            )
            stored_ident = orch._stored_identity(
                "params", params[0], {"Gamma": str(gamma)})
            assert fresh_ident is not None and stored_ident is not None
            # Simulate a redacted stored file: file-less keys must agree.
            redacted = (stored_ident[0], stored_ident[1],
                        "[REDACTED]") + stored_ident[3:]
            assert (orch._nofile_identity(fresh_ident)
                    == orch._nofile_identity(redacted))
        # A clean re-run after real redaction must not churn: covered by
        # the idempotency test above plus the nofile unit assertion here.
