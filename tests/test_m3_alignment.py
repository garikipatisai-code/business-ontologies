"""M3C alignment + workflow + read-only views + invalidation (C2).

RED: ``bo_ontology.alignment`` and ``bo_api.views_m3`` do not exist yet, so
importing this module fails at collection (exit != 0). These tests define the
M3C contract to be implemented; nothing here is implemented to make them pass.

Covers docs/plans/m3-consolidated.md C2:
* typed mappings equivalentTo / overlapsWith / distinctFrom / insufficient-evidence
  preserving local meanings (no cross-domain equivalence).
* compatibility != equivalence: equivalentTo requires a human-accepted mapping.
* workflow: ordered steps with evidence, >= 1 unresolved segment with reason.
* invalidation: supporting-rule change marks dependents stale, history preserved.
* read-only JSON views for concepts / alignment / workflow, no write verbs.

Dev fixtures only (repo-d loyalty + repo-b refund). Evaluator-only fixtures
are out of scope for this file. No network. Deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bo_extract.heuristic_concepts import discover
from bo_ontology.invalidation import invalidate_artifact
from bo_ontology.review import record_review, transition_review
from bo_ontology.store import OntologyStore

# --- modules under test (M3C; not implemented yet -> RED collection error) ---
from bo_ontology.alignment import align, build_workflow  # noqa: E402
from bo_api import views_m3  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
DEV_REPOS = [str(FIXTURES / "repo-d"), str(FIXTURES / "repo-b")]


# ------------------------------------------------------------------ helpers


def _dev_concepts_links():
    """Dev concepts/links from repo-d (loyalty) + repo-b (refund) heuristics."""
    dev = discover(DEV_REPOS)
    assert not dev["abstain"]
    assert dev["concepts"], "dev fixtures must yield proposed concepts"
    return dev["concepts"], dev["links"]


def _points_concept():
    return {
        "name": "Points",
        "domain": "loyalty",
        "scope": {"repo": "repo-d"},
        "origin": "heuristic",
        "status": "proposed",
    }


def _refund_window_concept():
    return {
        "name": "RefundWindow",
        "domain": "refund",
        "scope": {"repo": "repo-b"},
        "origin": "heuristic",
        "status": "proposed",
    }


def _pair(mappings, subject, object_):
    return [m for m in mappings if {m["subject"], m["object"]} == {subject, object_}]


# ---------------------------------------------------------------- distinctFrom


def test_distinct_from_loyalty_points_vs_refund_window_never_equivalent():
    """Points (loyalty, repo-d) vs RefundWindow (refund, repo-b): different
    domains -> distinctFrom, never equivalentTo (compatibility != equivalence)."""
    concepts = [_points_concept(), _refund_window_concept()]
    mappings = align(concepts, links=[])

    pair = _pair(mappings, "Points", "RefundWindow")
    assert pair, "expect a typed mapping between Points and RefundWindow"
    assert all(mapping["kind"] == "distinctFrom" for mapping in pair)
    assert not any(mapping["kind"] == "equivalentTo" for mapping in pair)


# ----------------------------------------------------------- insufficient-evidence


def test_insufficient_evidence_shared_table_name_only_stays_proposed():
    """Shared table name alone, no contract/client-server/event link ->
    insufficient-evidence; status stays proposed (compatibility != equivalence)."""
    concepts = [
        {
            "name": "PointsAccount",
            "domain": "loyalty",
            "scope": {"repo": "repo-d"},
            "tables": ["accounts_points"],
            "origin": "heuristic",
            "status": "proposed",
        },
        {
            "name": "RefundAccount",
            "domain": "refund",
            "scope": {"repo": "repo-b"},
            "tables": ["accounts_points"],
            "origin": "heuristic",
            "status": "proposed",
        },
    ]
    mappings = align(concepts, links=[])

    pair = _pair(mappings, "PointsAccount", "RefundAccount")
    assert pair, "expect a typed mapping for the shared-table-name pair"
    assert all(mapping["kind"] == "insufficient-evidence" for mapping in pair)
    assert all(mapping["status"] == "proposed" for mapping in pair)


# --------------------------------------------------------- equivalentTo guard


def test_equivalent_to_never_emitted_without_accepted_mapping():
    """align() with no accepted-mapping argument emits no equivalentTo, even
    for a same-name same-domain candidate pair (compatibility != equivalence)."""
    concepts = [
        {**_points_concept(), "scope": {"repo": "repo-d"}},
        {**_points_concept(), "scope": {"repo": "repo-b"}},
    ]
    mappings = align(concepts, links=[])  # accepted_mappings defaults to none

    assert not any(mapping["kind"] == "equivalentTo" for mapping in mappings)


# -------------------------------------------------------------------- workflow


def test_build_workflow_dev_concepts_ordered_steps_evidence_unresolved_segment():
    """build_workflow() on dev concepts: ordered steps, each carrying evidence,
    and >= 1 unresolved segment marked unresolved with a reason."""
    concepts, _links = _dev_concepts_links()
    steps = build_workflow(concepts)

    assert isinstance(steps, list) and len(steps) >= 1

    # ordered: order fields (or positions) increase monotonically
    orders = [step.get("order", index) for index, step in enumerate(steps)]
    assert orders == sorted(orders)

    # every step carries evidence
    assert all(isinstance(step.get("evidence"), (dict, list)) and step["evidence"] for step in steps)

    # exactly >= 1 unresolved segment, each marked unresolved with a reason
    unresolved = [step for step in steps if step.get("status") == "unresolved"]
    assert len(unresolved) >= 1
    assert all(step.get("reason") for step in unresolved)


# ---------------------------------------------------------------- invalidation


def test_supporting_rule_change_marks_artifact_stale_preserves_original_value():
    """Changed supporting rule value (30 -> 45): artifact stale + superseded,
    original value preserved, new value kept, no silent carry."""
    rule = {
        "subject": "refund_window_days",
        "value": 30,
        "status": "accepted",
        "origin": "deterministic",
        "repo": "repo-b",
    }
    # test-input correction: original supplied separately; previously the changed dict hid it
    invalidated = invalidate_artifact(rule, "supporting rule value changed 30 -> 45", new_value=45)

    assert invalidated["status"] == "stale"
    assert invalidated["superseded"] is True
    assert invalidated["original_value"] == 30
    assert invalidated["value"] == 45
    assert invalidated["invalidation_reason"] == "supporting rule value changed 30 -> 45"
    assert invalidated["invalidation_version"] == 1


def test_dependent_cross_repo_assertion_marked_stale_not_silently_carried():
    """Dependent cross-repo assertion (repo-a refund depends on repo-b rule)
    recomputed stale on supporting-rule change; acceptance not carried silently."""
    dependent = {
        "id": "ax-refund-request-depends-refund-window",
        "subject": "refund_request",
        "predicate": "depends_on",
        "object": {"rule": "refund_window_days", "repo": "repo-b"},
        "occurrence": 1,
        "origin": "deterministic",
        "status": "accepted",
        "confidence": 1.0,
        "evidence": [],
    }
    invalidated = invalidate_artifact(
        dict(dependent), reason="superseded by supporting rule change: refund_window_days 30 -> 45"
    )

    assert invalidated["status"] == "stale"
    assert invalidated["superseded"] is True
    assert invalidated["original_status"] == "accepted"
    assert invalidated["original_evidence"] == []


def test_invalidation_appends_history_and_preserves_original_row(tmp_path):
    """Original assertion row preserved; invalidation appends review history
    (accepted -> stale with original_status kept, version bumped)."""
    store = OntologyStore(tmp_path / "m3-invalidation.db")

    dependent = {
        "id": "ax-refund-request-depends-refund-window",
        "subject": "refund_request",
        "predicate": "depends_on",
        "object": {"rule": "refund_window_days", "repo": "repo-b"},
        "occurrence": 1,
        "origin": "deterministic",
        "status": "accepted",
        "confidence": 1.0,
        "evidence": [],
    }
    store.add_assertion(dependent)

    first = record_review(
        {
            "subject": dependent["id"],
            "status": "accepted",
            "reviewer": "human",
            "reason": "initial acceptance",
            "version": 1,
        }
    )
    store.record_review(first)
    superseded = record_review(transition_review(first, "stale"))
    store.record_review(superseded)

    rows = store.reviews(dependent["id"])
    assert [row["status"] for row in rows] == ["accepted", "stale"]
    assert rows[1]["original_status"] == "accepted"
    assert rows[1]["version"] == 2

    # original row preserved untouched
    assert store.get("assertions", dependent["id"])["status"] == "accepted"


# -------------------------------------------------------------------- views


def test_views_m3_concepts_json_is_read_only_json():
    """concepts_json() returns read-only JSON for concepts."""
    concepts, _links = _dev_concepts_links()
    payload = json.loads(views_m3.concepts_json(concepts))
    assert isinstance(payload, list)


def test_views_m3_alignment_json_is_read_only_json():
    """alignment_json() returns read-only JSON for typed mappings."""
    concepts, links = _dev_concepts_links()
    mappings = align(concepts, links)
    payload = json.loads(views_m3.alignment_json(mappings))
    assert isinstance(payload, list)


def test_views_m3_workflow_json_is_read_only_json():
    """workflow_json() returns read-only JSON for workflow steps."""
    concepts, _links = _dev_concepts_links()
    steps = build_workflow(concepts)
    payload = json.loads(views_m3.workflow_json(steps))
    assert isinstance(payload, (list, dict))


def test_views_m3_source_has_no_write_verbs():
    """views_m3 is mount-only read-only: no do_POST / do_PUT / do_DELETE."""
    source = Path(views_m3.__file__).read_text(encoding="utf-8")
    assert "do_POST" not in source
    assert "do_PUT" not in source
    assert "do_DELETE" not in source
