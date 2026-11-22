"""M3 A2 sealed held-out: repo-e seal integrity + extractor-output contract.

Authoritative labels are read from the fixtures via ast (independent ground
truth, M2 precedent): label constants are literals from repo-e source, never
hardcoded expectations tuned to the extractor.

repo-e is parsed, never imported or executed. This file imports
bo_extract.deterministic.extract only for section (b) (extractor-output
assertions on repo-e path ONLY); section (a) touches fixtures and the seal
doc only. No repo-e content is imported by src here.

RED status (pre-implementation, A2):
- seal integrity: GREEN now (seal matches fixtures).
- StoreCreditPolicy.credit_limit rule with evidence: GREEN now.
- StoreCreditAccount issue/redeem/expire decisions with evidence: RED now —
  the extractor emits no decision rules for repo-e yet.
- RefundPolicy same-name exclusion from business relations: GREEN now
  (no invokes edges; shape assertion guards it).
- CreditGatewayClient dynamic getattr -> no invokes edge (abstain): GREEN now.
- credit_limit vs MAX_CREDIT_BALANCE different-name pair linked/marked with
  evidence: RED now — no link edge exists; MAX_CREDIT_BALANCE is not even an
  extractor symbol yet.
"""

import ast
import hashlib
import json
from pathlib import Path

from bo_extract.deterministic import extract

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SEAL = ROOT / "docs" / "m3_heldout_seal.json"
REPO_E = FIXTURES / "repo-e"

SEAL_DOC = json.loads(SEAL.read_text(encoding="utf-8"))
SEAL_FILES = SEAL_DOC["files"]

# Extractor output on repo-e path ONLY (no other repo, no src import of repo-e).
NODES, EDGES, RULES = extract(str(REPO_E))

INVOKES = [e for e in EDGES if str(e.get("kind", "")).lower() == "invokes"]
RELATIONS = [
    e for e in EDGES
    if str(e.get("kind", "")).lower() in {"invokes", "relation", "business_relation"}
]


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
    raise AssertionError(f"class {name} not found")


def _method(cls, name):
    for m in cls.body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == name:
            return m
    raise AssertionError(f"method {name} not found")


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


def _assign_value(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"assign {name} not found in module")


# label constants read from fixtures (literals)
EXPECTED_CREDIT_LIMIT = 5000
EXPECTED_MAX_REDEEM_PER_DAY = 10
EXPECTED_MAX_CREDIT_BALANCE = 5000
EXPECTED_CREDIT_EXPIRY_DAYS = 90
EXPECTED_CREDIT_STALE_AFTER = "90 days"


# ---------------------------------------------------------------- (a) seal


def test_seal_hashes_recomputed_match_seal_json():
    """Seal integrity: sha256 of each sealed fixture equals its seal.json entry."""
    for relpath, expected_hash in SEAL_FILES.items():
        actual = hashlib.sha256((ROOT / relpath).read_bytes()).hexdigest()
        assert actual == expected_hash, f"seal hash mismatch for {relpath}"


def test_seal_covers_exactly_the_three_repo_e_fixtures():
    """The seal covers fixtures/repo-e/{app,rules,contracts}.py and nothing else."""
    assert set(SEAL_FILES) == {
        "fixtures/repo-e/app.py",
        "fixtures/repo-e/rules.py",
        "fixtures/repo-e/contracts.py",
    }


# ------------------------------------------- (b) extractor output on repo-e


def test_store_credit_credit_limit_rule_found_with_evidence():
    """Store-credit rule StoreCreditPolicy.credit_limit = 5000 found with evidence."""
    rules = [r for r in RULES if r.get("symbol") == "StoreCreditPolicy.credit_limit"]
    assert len(rules) == 1, rules
    rule = rules[0]
    assert rule.get("value") == EXPECTED_CREDIT_LIMIT
    ev = rule.get("evidence") or {}
    assert ev.get("file", "").endswith("rules.py")
    assert isinstance(ev.get("line"), int) and ev["line"] > 0
    assert "credit_limit" in str(ev.get("excerpt", ""))
    assert float(rule.get("confidence", 0) or 0) == 1.0


def test_store_credit_max_redeem_per_day_rule_found_with_evidence():
    """Store-credit rule StoreCreditPolicy.max_redeem_per_day = 10 found with evidence."""
    rules = [r for r in RULES if r.get("symbol") == "StoreCreditPolicy.max_redeem_per_day"]
    assert len(rules) == 1, rules
    rule = rules[0]
    assert rule.get("value") == EXPECTED_MAX_REDEEM_PER_DAY
    ev = rule.get("evidence") or {}
    assert ev.get("file", "").endswith("rules.py")
    assert isinstance(ev.get("line"), int) and ev["line"] > 0


def test_store_credit_account_decisions_found_with_evidence():
    """StoreCreditAccount issue/redeem/expire decisions found with evidence (RED now)."""
    app = _parse("repo-e/app.py")
    account = _class(app, "StoreCreditAccount")
    for method_name in ("issue", "redeem", "expire"):
        _method(account, method_name)  # fixture shape must exist
        decisions = [
            r for r in RULES
            if r.get("symbol") == f"StoreCreditAccount.{method_name}"
            and "predicate" in r
        ]
        assert len(decisions) == 1, f"no decision rule for StoreCreditAccount.{method_name}"
        d = decisions[0]
        ev = d.get("evidence") or {}
        assert ev.get("file", "").endswith("app.py")
        assert isinstance(ev.get("line"), int) and ev["line"] > 0
        assert "predicate" in d and "outcomes" in d


def test_refund_policy_method_labels_read_from_fixture():
    """Same-name probe RefundPolicy exposes ui_label / ticket_code (fixture literals)."""
    app = _parse("repo-e/app.py")
    policy = _class(app, "RefundPolicy")
    ui_label = _method(policy, "ui_label")
    ticket_code = _method(policy, "ticket_code")
    rets = [n for n in ast.walk(ui_label) if isinstance(n, ast.Return)]
    assert len(rets) == 1 and isinstance(rets[0].value, ast.Constant)
    assert rets[0].value.value == "REFUND_TICKET_LABEL"
    rets = [n for n in ast.walk(ticket_code) if isinstance(n, ast.Return)]
    assert len(rets) == 1 and isinstance(rets[0].value, ast.Constant)
    assert rets[0].value.value == "RT-000"
    # shape 3: no repo-b refund semantics (applyWindow mention is inert comment text only)
    assert "applyWindow" not in _names_in(app)


def test_refund_policy_same_name_excluded_from_business_relations():
    """Same-name RefundPolicy is excluded from every business relation edge."""
    for rel in RELATIONS:
        assert "RefundPolicy" not in str(rel.get("source", "")), rel
        assert "RefundPolicy" not in str(rel.get("target", "")), rel


def test_credit_gateway_dynamic_dispatch_produces_no_invokes_edge():
    """CreditGatewayClient.call dispatches via getattr on a variable name:
    statically unresolvable -> abstain, NO invokes edge."""
    app = _parse("repo-e/app.py")
    gw = _class(app, "CreditGatewayClient")
    call_fn = _method(gw, "call")
    getattr_calls = [
        n for n in ast.walk(call_fn)
        if isinstance(n, ast.Call) and _dotted(n.func) == "getattr"
    ]
    assert len(getattr_calls) == 1, getattr_calls
    args = getattr_calls[0].args
    assert len(args) >= 2
    assert isinstance(args[1], ast.Name) and args[1].id == "method_name"
    for rel in RELATIONS:
        assert "CreditGatewayClient.call" not in str(rel.get("source", "")), rel
        assert "CreditGateway.applyCredit" not in str(rel.get("target", "")), rel
    assert INVOKES == [], INVOKES


def test_contracts_event_schema_and_shared_tables_labels():
    """EVENT_SCHEMA store_credit_event (issue/redeem/expire) + SHARED_TABLES
    constants read from fixtures as literals."""
    contracts = _parse("repo-e/contracts.py")
    schema = _assign_value(contracts, "EVENT_SCHEMA")
    entry = schema["store_credit_event"]
    fields = {f["name"]: f for f in entry["fields"]}
    assert set(fields) == {"account_id", "transaction_type", "amount", "reason"}
    txn_type = fields["transaction_type"]
    assert txn_type["type"] == "enum"
    assert set(txn_type["values"]) == {"issue", "redeem", "expire"}
    assert fields["reason"].get("optional") is True
    tables = _assign_value(contracts, "SHARED_TABLES")
    assert tables["account_credit"] == "account_store_credit"
    assert tables["credit_ledger"] == "store_credit_ledger"
    assert tables["credit_history"] == "store_credit_history"
    fn = _function(contracts, "sync_store_credit")
    calls = _call_dotted_names(fn)
    assert "store_credit_client.get_balance" in calls, calls
    assert "store_credit_server.credit_store_credit" in calls, calls


def test_different_name_pair_credit_limit_max_credit_balance_linked_with_evidence():
    """credit_limit = 5000 vs MAX_CREDIT_BALANCE = 5000: same threshold,
    different name; linked or explicitly marked with evidence (RED now)."""
    rules_tree = _parse("repo-e/rules.py")
    consts = _module_constants(rules_tree)
    assert consts.get("MAX_CREDIT_BALANCE") == EXPECTED_MAX_CREDIT_BALANCE
    assert consts.get("CREDIT_EXPIRY_DAYS") == EXPECTED_CREDIT_EXPIRY_DAYS
    assert consts.get("CREDIT_STALE_AFTER") == EXPECTED_CREDIT_STALE_AFTER
    credit = [r for r in RULES if r.get("symbol") == "StoreCreditPolicy.credit_limit"]
    assert len(credit) == 1 and credit[0].get("value") == EXPECTED_CREDIT_LIMIT
    links = [
        e for e in EDGES
        if "StoreCreditPolicy.credit_limit" in str(e.get("source", ""))
        or "StoreCreditPolicy.credit_limit" in str(e.get("target", ""))
        or "MAX_CREDIT_BALANCE" in str(e.get("source", ""))
        or "MAX_CREDIT_BALANCE" in str(e.get("target", ""))
    ]
    assert any("evidence" in e for e in links), (
        "credit_limit / MAX_CREDIT_BALANCE pair not linked or marked with evidence"
    )
