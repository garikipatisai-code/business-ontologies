"""Unified multi-repo workflow orchestrator (RED-first).

Covers the ``bo_workflow`` orchestrator + CLI over two synthetic Java repos
(Alpha/BetaSvc): ingest persists rules/transitions/operations with spans,
review preserves history, a param source-change cascades with trigger
reasons, report shows active/stale/abstentions/reviews, and negatives
(missing path, malformed manifest, unrelated change) fail cleanly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bo_ontology.store import OntologyStore
from bo_workflow.orchestrator import (
    WORKFLOW_VERSION,
    ingest_manifest,
    invalidate_root,
    load_manifest,
    record_review,
    report,
)

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "bo_workflow.py"


# ---------------------------------------------------------------- fixtures


ALPHA_CONTROLLER = """\
package com.example.alpha;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/alpha")
@RestController
public class AlphaController {

    @PostMapping("/create")
    public String createAlpha() {
        return "ok";
    }
}
"""

ALPHA_SERVICE = """\
package com.example.alpha;

public class RefundService {
    void applyRefund(Order order, int days, int windowDays) {
        if (days > windowDays) {
            throw new IllegalStateException("outside window");
        }
        order.setRefundStatus("REFUNDED");
    }
}
"""

ALPHA_MODEL = """\
package com.example.alpha;

import jakarta.validation.constraints.NotNull;

public class AlphaModel {

    @NotNull
    private String code;
}
"""

ALPHA_API = """\
package com.example.alpha;

public interface AlphaApi {
    String createAlpha(String name);
}
"""

ALPHA_YAML = "windowDays: 30\n"

ALPHA_SQL = """\
CREATE TABLE IF NOT EXISTS parts (
  id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS widgets (
  id INTEGER PRIMARY KEY,
  part_id INTEGER NOT NULL,
  FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE
);
"""

ALPHA_OPENAPI = """\
openapi: 3.0.0
info:
  title: Alpha
  version: 1.0.0
paths:
  /api/alpha/create:
    post:
      operationId: createAlpha
      responses:
        '200':
          description: ok
"""

BETA_CONTROLLER = """\
package com.example.beta;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class BetaController {

    private static final String PATH_CONST = "/dynamic";

    @GetMapping(PATH_CONST)
    public String byConst() {
        return "ok";
    }
}
"""

BETA_CLIENT = """\
package com.example.beta;

import org.springframework.web.client.RestTemplate;

public class BetaClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public String callAlpha() {
        String url = "http://alpha-svc.local/api/alpha/create";
        return restTemplate.postForObject(url, new Object(), String.class);
    }
}
"""

BETA_SERVICE = """\
package com.example.beta;

public class LedgerService {
    void postLedger(Ledger ledger) {
        if (ledger.getAmount() == null) {
            throw new IllegalStateException("no amount");
        }
        ledger.setLedgerStatus("POSTED");
    }
}
"""

BETA_SOLO = """\
package com.example.beta;

import jakarta.validation.constraints.NotNull;

public class Solo extends MissingBase {

    @NotNull
    private String sku;
}
"""

BETA_YAML = "betaLimit: 5\n"


def _build_repos(base: Path) -> tuple[Path, Path]:
    alpha = base / "Alpha"
    beta = base / "BetaSvc"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "AlphaController.java").write_text(ALPHA_CONTROLLER, encoding="utf-8")
    (alpha / "RefundService.java").write_text(ALPHA_SERVICE, encoding="utf-8")
    (alpha / "AlphaModel.java").write_text(ALPHA_MODEL, encoding="utf-8")
    (alpha / "AlphaApi.java").write_text(ALPHA_API, encoding="utf-8")
    (alpha / "application.yml").write_text(ALPHA_YAML, encoding="utf-8")
    (alpha / "schema.sql").write_text(ALPHA_SQL, encoding="utf-8")
    (alpha / "openapi.yaml").write_text(ALPHA_OPENAPI, encoding="utf-8")
    (beta / "BetaController.java").write_text(BETA_CONTROLLER, encoding="utf-8")
    (beta / "BetaClient.java").write_text(BETA_CLIENT, encoding="utf-8")
    (beta / "LedgerService.java").write_text(BETA_SERVICE, encoding="utf-8")
    (beta / "Solo.java").write_text(BETA_SOLO, encoding="utf-8")
    (beta / "application.yml").write_text(BETA_YAML, encoding="utf-8")
    return alpha, beta


def _manifest(tmp_path: Path, alpha: Path, beta: Path) -> Path:
    manifest = tmp_path / "repos.json"
    manifest.write_text(
        json.dumps(
            {
                "repos": [
                    {"name": "Alpha", "path": str(alpha)},
                    {"name": "BetaSvc", "path": str(beta)},
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _actions(store: OntologyStore, kind: str, record_id) -> list[str]:
    return [e.get("action") for e in store.history(kind, record_id)]


def _row_by_name(store: OntologyStore, kind: str, name: str) -> dict:
    rows = [r for r in store.all(kind) if r.get("name") == name]
    assert len(rows) == 1, f"expected one {kind} {name!r}, got {len(rows)}"
    return rows[0]


# ---------------------------------------------------------------- manifest negatives


class TestManifestValidation:
    def test_missing_repo_path_errors(self, tmp_path):
        manifest = tmp_path / "repos.json"
        manifest.write_text(
            json.dumps({"repos": [{"name": "Ghost", "path": str(tmp_path / "nope")}]}),
            encoding="utf-8",
        )
        with pytest.raises(FileNotFoundError, match="not found"):
            load_manifest(manifest)
        with pytest.raises(FileNotFoundError, match="not found"):
            ingest_manifest(manifest, tmp_path / "out.db")

    def test_malformed_manifest_errors(self, tmp_path):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="[Mm]alformed"):
            load_manifest(bad_json)

        no_repos = tmp_path / "no-repos.json"
        no_repos.write_text(json.dumps({"projects": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="[Mm]alformed|repos"):
            load_manifest(no_repos)

        missing_keys = tmp_path / "missing-keys.json"
        missing_keys.write_text(json.dumps({"repos": [{"name": "x"}]}), encoding="utf-8")
        with pytest.raises(ValueError, match="[Mm]alformed|path"):
            load_manifest(missing_keys)

    def test_yaml_manifest_rejected_json_only(self, tmp_path):
        manifest = tmp_path / "repos.yaml"
        manifest.write_text("repos: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON"):
            load_manifest(manifest)

    def test_remote_path_rejected_no_fetch(self, tmp_path):
        manifest = tmp_path / "remote.json"
        manifest.write_text(
            json.dumps(
                {"repos": [{"name": "Web", "path": "https://example.com/repo"}]}
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="[Rr]emote|locally"):
            load_manifest(manifest)

    def test_cli_missing_repo_fails_cleanly(self, tmp_path):
        manifest = tmp_path / "repos.json"
        manifest.write_text(
            json.dumps({"repos": [{"name": "Ghost", "path": str(tmp_path / "nope")}]}),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(CLI), "ingest", "--manifest", str(manifest),
             "--db", str(tmp_path / "out.db")],
            text=True, capture_output=True,
        )
        assert proc.returncode != 0
        assert "not found" in proc.stderr.lower()


# ---------------------------------------------------------------- end to end


class TestEndToEnd:
    def test_ingest_persists_rules_transitions_ops_with_spans(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "wf.db"

        summary = ingest_manifest(manifest, db)

        assert summary["counts"]["params"] == 2
        assert summary["counts"]["rules"] == 2
        assert summary["counts"]["transitions"] == 2
        assert summary["counts"]["operations"] == 2
        assert summary["counts"]["dependencies"] >= 2
        assert summary["abstentions"], "expected unlinkable/unverified abstentions"
        assert summary["gaps"], "expected inheritance/enforcement gaps"

        with OntologyStore(str(db)) as store:
            for rule in store.all("rules"):
                supporting = rule["supporting"]
                if isinstance(supporting, str):
                    supporting = json.loads(supporting)
                prov = supporting[0]
                assert prov["span"]["start_line"] >= 1
                assert prov["span"]["end_line"] >= prov["span"]["start_line"]
                assert prov["content_hash"] and prov["extractor_version"]
                assert rule["evidence"], "rule must carry file/line evidence"
            for trans in store.all("transitions"):
                prov = json.loads(trans["trigger"])["provenance"]
                assert prov["span"]["start_line"] >= 1
                assert prov["content_hash"] and prov["extractor_version"]
            for op in store.all("operations"):
                prov = json.loads(op["description"])["provenance"]
                assert prov["span"]["start_line"] >= 1
                assert prov["content_hash"] and prov["extractor_version"]
            for param in store.all("params"):
                prov = json.loads(param["description"])
                assert prov["span"]["start_line"] >= 1

    def test_review_invalidate_and_report(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)

        with OntologyStore(str(db)) as store:
            rule_names = sorted(r["name"] for r in store.all("rules"))
            alpha_rule = next(n for n in rule_names if n.startswith("Alpha:"))
            beta_rule = next(n for n in rule_names if n.startswith("BetaSvc:"))
            alpha_param = _row_by_name(store, "params", "Alpha:windowDays")
            beta_param = _row_by_name(store, "params", "BetaSvc:betaLimit")

        # Review the Alpha rule: history preserved, row untouched.
        review = record_review(
            db, alpha_rule, reviewer="qa", reason="guard matches policy"
        )
        assert review["id"]

        # Source change on the Alpha param cascades to its rule + transition.
        result = invalidate_root(
            db, "params", alpha_param["id"],
            reason="window 30->45", new_value=45,
        )
        invalidated = {tuple(t) for t in result["invalidated"]}
        with OntologyStore(str(db)) as store:
            alpha_rule_row = _row_by_name(store, "rules", alpha_rule)
            alpha_trans = [
                t for t in store.all("transitions")
                if json.loads(t["trigger"])["provenance"]["repo"] == "Alpha"
            ]
            assert len(alpha_trans) == 1
            assert ("params", alpha_param["id"]) in invalidated
            assert ("rules", alpha_rule_row["id"]) in invalidated
            assert ("transitions", alpha_trans[0]["id"]) in invalidated

            assert store.get("params", alpha_param["id"])["value"] == 45
            assert alpha_rule_row["status"] == "stale"
            assert alpha_trans[0]["status"] == "stale"
            for dep_id in (alpha_rule_row["id"], alpha_trans[0]["id"]):
                kind = "rules" if dep_id == alpha_rule_row["id"] else "transitions"
                details = [
                    json.loads(e["detail"]) if isinstance(e.get("detail"), str)
                    else e.get("detail", {})
                    for e in store.history(kind, dep_id)
                    if e.get("action") == "invalidate"
                ]
                assert len(details) == 1
                assert str(alpha_param["id"]) in details[0]["reason"]
                assert "window 30->45" in details[0]["reason"]

            # Unrelated Beta records stay valid.
            assert store.get("params", beta_param["id"])["value"] == "5"
            beta_rule_row = _row_by_name(store, "rules", beta_rule)
            assert beta_rule_row["status"] == "proposed"
            assert _actions(store, "params", beta_param["id"]) == ["insert"]

            # History traces insert -> invalidate on the root.
            assert _actions(store, "params", alpha_param["id"]) == [
                "insert", "invalidate",
            ]
            # Prior review stays inspectable.
            prior = store.reviews(alpha_rule)
            assert prior and any(r.get("reviewer") == "qa" for r in prior)

        data = report(db)
        stale_names = {
            (e["kind"], e.get("name") or e.get("subject")) for e in data["stale"]
        }
        assert ("rules", alpha_rule) in stale_names
        assert any(e.get("trigger") for e in data["stale"])
        active_names = {
            (e["kind"], e.get("name") or e.get("subject")) for e in data["active"]
        }
        assert ("rules", beta_rule) in active_names
        assert ("params", "BetaSvc:betaLimit") in active_names
        assert data["abstentions"]
        assert data["gaps"]
        assert any(r.get("reviewer") == "qa" for r in data["reviews"])

    def test_cli_ingest_and_report(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "cli.db"

        ingest = subprocess.run(
            [sys.executable, str(CLI), "ingest", "--manifest", str(manifest),
             "--db", str(db)],
            text=True, capture_output=True,
        )
        assert ingest.returncode == 0, ingest.stderr
        summary = json.loads(ingest.stdout)
        assert summary["counts"]["rules"] == 2
        assert summary["counts"]["operations"] == 2

        rep = subprocess.run(
            [sys.executable, str(CLI), "report", "--db", str(db)],
            text=True, capture_output=True,
        )
        assert rep.returncode == 0, rep.stderr
        data = json.loads(rep.stdout)
        assert len(data["active"]) >= 6
        assert data["abstentions"] and data["gaps"]


# ---------------------------------------------------------------- full binding


FULL_API = """\
package com.example.full;

public interface FullApi {
    String fetchItem(String id);
}
"""

FULL_SERVICE = """\
package com.example.full;

public class FullService implements FullApi {
    @Override
    public String fetchItem(String id) {
        return ResponseEntity.status(200).body("ok").toString();
    }
}
"""

FULL_OPENAPI = """\
openapi: 3.0.0
info:
  title: Full
  version: 1.0.0
paths:
  /api/full/item:
    get:
      operationId: fetchItem
      responses:
        '200':
          description: ok
  /api/full/orphan:
    get:
      operationId: orphanOp
      responses:
        '200':
          description: ok
"""


class TestBindingPassthrough:
    def test_full_binding_needs_no_abstention_with_evidence(self, tmp_path):
        repo = tmp_path / "Full"
        repo.mkdir()
        (repo / "FullApi.java").write_text(FULL_API, encoding="utf-8")
        (repo / "FullService.java").write_text(FULL_SERVICE, encoding="utf-8")
        (repo / "openapi.yaml").write_text(FULL_OPENAPI, encoding="utf-8")
        manifest = tmp_path / "repos.json"
        manifest.write_text(
            json.dumps({"repos": [{"name": "Full", "path": str(repo)}]}),
            encoding="utf-8",
        )
        summary = ingest_manifest(manifest, tmp_path / "full.db")

        assert summary["telemetry"]["interfaces"] == 1
        assert summary["telemetry"]["implementations"] == 1
        unresolved = [
            a for a in summary["abstentions"]
            if a.get("kind") == "unresolved_binding"
        ]
        # fetchItem has interface + arity-matched implementation with a
        # return-type corroboration and a declared 200 response: full bind,
        # so no abstention. orphanOp has no implementation: still abstains.
        assert "fetchItem" not in [a.get("operation_id") for a in unresolved]
        assert "orphanOp" in [a.get("operation_id") for a in unresolved]


# ---------------------------------------------------------------- inheritance gaps


class TestInheritanceGaps:
    def test_missing_superclass_gap_reported_and_persisted(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "wf.db"
        summary = ingest_manifest(manifest, db)

        inherited = [
            g for g in summary["gaps"] if g.get("kind") == "inheritance_gap"
        ]
        assert inherited, "expected an inheritance gap for Solo/MissingBase"
        solo = [g for g in inherited if g.get("superclass") == "MissingBase"]
        assert solo and solo[0]["repo"] == "BetaSvc"
        assert solo[0]["span"]["start_line"] >= 1

        data = report(db)
        persisted = [
            g for g in data["gaps"]
            if isinstance(g.get("object"), dict)
            and g["object"].get("superclass") == "MissingBase"
        ]
        assert persisted, "inheritance gap must reach the ledger"


# ---------------------------------------------------------------- empty key + rule names


EMPTY_A_POLICY = """\
package com.example.empty.a;

public class Policy {
    void applyPolicy(Order order, int days, int quota) {
        if (days > quota) {
            throw new IllegalStateException("over quota");
        }
        order.setRefundStatus("REFUNDED");
    }
}
"""

EMPTY_B_POLICY = """\
package com.example.empty.b;

public class Policy {
    void applyPolicy(Order order, int days, int quota) {
        if (days > quota) {
            throw new IllegalStateException("over quota");
        }
        order.setRefundStatus("REFUNDED");
    }
}
"""

EMPTY_YML = ": 999\nquota: 7\n"


class TestEmptyKeyAndRuleNames:
    def _ingest(self, tmp_path):
        repo = tmp_path / "Empty"
        (repo / "a").mkdir(parents=True)
        (repo / "b").mkdir(parents=True)
        (repo / "a" / "Policy.java").write_text(
            EMPTY_A_POLICY, encoding="utf-8")
        (repo / "b" / "Policy.java").write_text(
            EMPTY_B_POLICY, encoding="utf-8")
        (repo / "application.yml").write_text(EMPTY_YML, encoding="utf-8")
        manifest = tmp_path / "repos.json"
        manifest.write_text(
            json.dumps({"repos": [{"name": "Empty", "path": str(repo)}]}),
            encoding="utf-8",
        )
        db = tmp_path / "empty.db"
        ingest_manifest(manifest, db)
        return db

    def test_empty_yaml_key_creates_no_spurious_link(self, tmp_path):
        db = self._ingest(tmp_path)
        with OntologyStore(str(db)) as store:
            empty_param = _row_by_name(store, "params", "Empty:")
            quota_param = _row_by_name(store, "params", "Empty:quota")
            assert store.direct_dependents("params", empty_param["id"]) == []
            assert store.direct_dependents("params", quota_param["id"])

    def test_rule_names_use_repo_relative_paths(self, tmp_path):
        db = self._ingest(tmp_path)
        with OntologyStore(str(db)) as store:
            names = sorted(r["name"] for r in store.all("rules"))
        assert len(names) == 2
        assert names[0] != names[1], f"basename collision: {names}"
        assert any("a/Policy.java" in n for n in names)
        assert any("b/Policy.java" in n for n in names)


# ---------------------------------------------------------------- contract evidence


class TestContractEvidence:
    def test_links_carry_client_and_openapi_evidence(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)

        with OntologyStore(str(db)) as store:
            links = [
                a for a in store.all("assertions")
                if a.get("predicate") == "contract_link"
            ]
        compatible = [
            a for a in links
            if isinstance(a.get("object"), dict)
            and a["object"].get("compatible") is True
        ]
        assert compatible, "expected a compatible Beta->Alpha link"
        assert any(
            any("openapi.yaml" in (e.get("path") or "") for e in (a.get("evidence") or []))
            for a in compatible
        ), "expected an openapi.yaml-backed compatible link"
        for link in compatible:
            evidence = link.get("evidence") or []
            assert len(evidence) >= 2, f"single-sided evidence: {evidence}"
            paths = [e.get("path") for e in evidence]
            assert any("BetaClient.java" in p for p in paths)
            assert len(set(paths)) >= 2, f"both sides identical: {paths}"
            assert {e.get("version") for e in evidence} == {WORKFLOW_VERSION}


# ---------------------------------------------------------------- manifest confinement


class TestManifestConfinement:
    def _manifest_with(self, tmp_path, raw_path):
        manifest = tmp_path / "repos.json"
        manifest.write_text(
            json.dumps({"repos": [{"name": "Evil", "path": raw_path}]}),
            encoding="utf-8",
        )
        return manifest

    def test_dotdot_traversal_rejected_before_read(self, tmp_path):
        manifest = self._manifest_with(tmp_path, "../evil")
        with pytest.raises(ValueError, match="[Tt]raversal|[Ee]scape|[Cc]onfin"):
            load_manifest(manifest)
        with pytest.raises(ValueError, match="[Tt]raversal|[Ee]scape|[Cc]onfin"):
            ingest_manifest(manifest, tmp_path / "out.db")

    def test_nested_dotdot_rejected(self, tmp_path):
        (tmp_path / "sub").mkdir()
        manifest = self._manifest_with(tmp_path, "sub/../../evil")
        with pytest.raises(ValueError, match="[Tt]raversal|[Ee]scape|[Cc]onfin"):
            load_manifest(manifest)

    def test_cli_traversal_exits_2(self, tmp_path):
        manifest = self._manifest_with(tmp_path, "../evil")
        proc = subprocess.run(
            [sys.executable, str(CLI), "ingest", "--manifest", str(manifest),
             "--db", str(tmp_path / "out.db")],
            text=True, capture_output=True,
        )
        assert proc.returncode == 2

    def test_relative_inside_manifest_dir_allowed(self, tmp_path):
        repo = tmp_path / "Inner"
        repo.mkdir()
        (repo / "application.yml").write_text("k: 1\n", encoding="utf-8")
        manifest = tmp_path / "repos.json"
        manifest.write_text(
            json.dumps({"repos": [{"name": "Inner", "path": "Inner"}]}),
            encoding="utf-8",
        )
        repos = load_manifest(manifest)
        assert repos == [{"name": "Inner", "path": str(repo)}]


# ---------------------------------------------------------------- CLI review/invalidate


class TestCliReviewInvalidate:
    def test_cli_review_invalidate_and_report_trigger(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "cli2.db"

        ingest = subprocess.run(
            [sys.executable, str(CLI), "ingest", "--manifest", str(manifest),
             "--db", str(db)],
            text=True, capture_output=True,
        )
        assert ingest.returncode == 0, ingest.stderr

        with OntologyStore(str(db)) as store:
            rule_names = sorted(r["name"] for r in store.all("rules"))
            alpha_rule = next(n for n in rule_names if n.startswith("Alpha:"))
            alpha_param = _row_by_name(store, "params", "Alpha:windowDays")

        rev = subprocess.run(
            [sys.executable, str(CLI), "review", "--db", str(db),
             "--subject", alpha_rule, "--reviewer", "qa",
             "--reason", "looks good"],
            text=True, capture_output=True,
        )
        assert rev.returncode == 0, rev.stderr
        assert json.loads(rev.stdout)["id"]

        inv = subprocess.run(
            [sys.executable, str(CLI), "invalidate", "--db", str(db),
             "--kind", "params", "--id", str(alpha_param["id"]),
             "--reason", "window 30->45", "--new-value", "45"],
            text=True, capture_output=True,
        )
        assert inv.returncode == 0, inv.stderr
        assert ["params", alpha_param["id"]] in json.loads(inv.stdout)["invalidated"]

        rep = subprocess.run(
            [sys.executable, str(CLI), "report", "--db", str(db)],
            text=True, capture_output=True,
        )
        assert rep.returncode == 0, rep.stderr
        data = json.loads(rep.stdout)
        stale = {
            (e["kind"], e.get("name")): e.get("trigger") for e in data["stale"]
        }
        assert ("rules", alpha_rule) in stale
        assert stale[("rules", alpha_rule)] and \
            "window 30->45" in stale[("rules", alpha_rule)]

    def test_cli_bad_invalidate_exits_2(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "cli3.db"
        ingest_manifest(manifest, db)

        bad_kind = subprocess.run(
            [sys.executable, str(CLI), "invalidate", "--db", str(db),
             "--kind", "nope", "--id", "1"],
            text=True, capture_output=True,
        )
        assert bad_kind.returncode == 2

        bad_id = subprocess.run(
            [sys.executable, str(CLI), "invalidate", "--db", str(db),
             "--kind", "params", "--id", "999999"],
            text=True, capture_output=True,
        )
        assert bad_id.returncode == 2


# ---------------------------------------------------------------- unrelated change


class TestUnrelatedChange:
    def test_invalidate_unrelated_root_touches_only_root(self, tmp_path):
        alpha, beta = _build_repos(tmp_path)
        manifest = _manifest(tmp_path, alpha, beta)
        db = tmp_path / "wf.db"
        ingest_manifest(manifest, db)

        with OntologyStore(str(db)) as store:
            beta_param = _row_by_name(store, "params", "BetaSvc:betaLimit")
            others = [
                (k, r["id"])
                for k in ("params", "rules", "transitions", "operations")
                for r in store.all(k)
                if not (k == "params" and r["id"] == beta_param["id"])
            ]

        result = invalidate_root(
            db, "params", beta_param["id"], reason="unrelated bump", new_value=6
        )
        assert result["invalidated"] == [["params", beta_param["id"]]]

        with OntologyStore(str(db)) as store:
            assert store.get("params", beta_param["id"])["value"] == 6
            for kind, rid in others:
                row = store.get(kind, rid)
                if "status" in row:
                    assert row["status"] == "proposed", (kind, rid)
                assert [
                    e.get("action") for e in store.history(kind, rid)
                ] == ["insert"], (kind, rid)
