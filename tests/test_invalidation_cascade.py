"""Explicit dependency-aware invalidation cascade (no name-similarity guessing).

Edges are created only via ``add_dependency`` (both endpoints must already
exist; duplicates rejected). ``invalidate_cascade`` walks the explicit edges
breadth-first reusing ``OntologyStore.invalidate``/``history`` — no new
subsystem, no new audit mechanism. The ``reviews`` table is never touched.
"""

from __future__ import annotations

import json

import pytest

from bo_ontology.schema import PolicyParameter, ReviewDecision, Rule, Transition
from bo_ontology.store import OntologyStore


def _detail(entry):
    detail = entry.get("detail", {})
    if isinstance(detail, str):
        return json.loads(detail)
    return detail


def _invalidates(store, kind, record_id):
    return [
        entry
        for entry in store.history(kind, record_id)
        if entry.get("action") == "invalidate"
    ]


def _actions(store, kind, record_id):
    return [entry.get("action") for entry in store.history(kind, record_id)]


def _param(store, name, value=30):
    return store.add_parameter(PolicyParameter(name=name, value=value))


def _rule(store, name, value="allow", status="accepted"):
    return store.add_rule(
        Rule(
            name=name,
            value=value,
            statement=f"{name} holds within policy",
            status=status,
        )
    )


def _transition(store, source="pending", target="shipped", status="accepted"):
    return store.add_transition(
        Transition(source=source, target=target, status=status)
    )


def _review(store, subject):
    return store.record_review(
        ReviewDecision(
            subject=subject,
            status="accepted",
            reviewer="qa",
            reason=f"{subject} matches policy",
            original_status="accepted",
        )
    )


class TestMultiStepCascade:
    """params P -> rules R -> transitions T; reviews preserved; rest intact."""

    def test_window_change_cascades_with_new_value_on_root_only(
        self, tmp_path
    ):
        with OntologyStore(tmp_path / "cascade.db") as store:
            pid = _param(store, "PWin", 30)
            rid = _rule(store, "RWin")
            tid = _transition(store, "pending", "TShip")
            uid = _param(store, "PUnrelated", 7)
            store.add_dependency("params", pid, "rules", rid, reason="RWin reads PWin")
            store.add_dependency("rules", rid, "transitions", tid)
            _review(store, "RWin")
            _review(store, "TShip")

            order = store.invalidate_cascade(
                "params", pid, reason="window 30->45", new_value=45
            )

            assert order[0] == ("params", pid)
            assert set(order) == {
                ("params", pid),
                ("rules", rid),
                ("transitions", tid),
            }

            # Root: new value stored, original preserved in audit.
            assert store.get("params", pid)["value"] == 45
            (pinv,) = _invalidates(store, "params", pid)
            pdetail = _detail(pinv)
            assert pdetail["original_value"] == 30
            assert pdetail["new_value"] == 45
            assert "window 30->45" in pdetail["reason"]

            # Dependents: stale, originals preserved, no new value carried.
            assert store.get("rules", rid)["status"] == "stale"
            assert store.get("rules", rid)["value"] == "allow"
            (rinv,) = _invalidates(store, "rules", rid)
            rdetail = _detail(rinv)
            assert rdetail["original_value"] == "allow"
            assert "new_value" not in rdetail
            assert str(pid) in rdetail["reason"]
            assert "window 30->45" in rdetail["reason"]

            assert store.get("transitions", tid)["status"] == "stale"
            (tinv,) = _invalidates(store, "transitions", tid)
            assert str(pid) in _detail(tinv)["reason"]

            # Prior reviews stay inspectable.
            prior_r = store.reviews("RWin")
            assert prior_r and any(e.get("reviewer") == "qa" for e in prior_r)
            prior_t = store.reviews("TShip")
            assert prior_t and any(e.get("reviewer") == "qa" for e in prior_t)

            # Unrelated row untouched.
            assert store.get("params", uid)["value"] == 7
            assert _invalidates(store, "params", uid) == []


class TestDiamondCascade:
    """A->B, A->C, B->D, C->D: all stale, D invalidated exactly once."""

    def test_diamond_invalidates_each_record_once(self, tmp_path):
        with OntologyStore(tmp_path / "diamond.db") as store:
            aid = _rule(store, "AWin")
            bid = _rule(store, "BWin")
            cid = _rule(store, "CWin")
            did = _rule(store, "DWin")
            store.add_dependency("rules", aid, "rules", bid)
            store.add_dependency("rules", aid, "rules", cid)
            store.add_dependency("rules", bid, "rules", did)
            store.add_dependency("rules", cid, "rules", did)

            order = store.invalidate_cascade("rules", aid, reason="diamond")

            assert order[0] == ("rules", aid)
            assert order[-1] == ("rules", did)
            assert sorted(order) == sorted(
                [
                    ("rules", aid),
                    ("rules", bid),
                    ("rules", cid),
                    ("rules", did),
                ]
            )
            for rid in (aid, bid, cid, did):
                assert store.get("rules", rid)["status"] == "stale"
                assert len(_invalidates(store, "rules", rid)) == 1, (
                    f"record {rid} must carry exactly one invalidate entry"
                )


class TestCycleCascade:
    """X->Y, Y->X: both stale, terminates, one invalidate each."""

    def test_cycle_terminates_without_duplicates(self, tmp_path):
        with OntologyStore(tmp_path / "cycle.db") as store:
            xid = _rule(store, "XWin")
            yid = _rule(store, "YWin")
            store.add_dependency("rules", xid, "rules", yid)
            store.add_dependency("rules", yid, "rules", xid)

            order = store.invalidate_cascade("rules", xid, reason="cycle")

            assert sorted(order) == [("rules", xid), ("rules", yid)]
            assert store.get("rules", xid)["status"] == "stale"
            assert store.get("rules", yid)["status"] == "stale"
            assert len(_invalidates(store, "rules", xid)) == 1
            assert len(_invalidates(store, "rules", yid)) == 1


class TestLookalikeUntouched:
    """Similar names never link: only explicit edges cascade."""

    def test_similar_name_without_edge_stays_valid(self, tmp_path):
        with OntologyStore(tmp_path / "lookalike.db") as store:
            pid = _param(store, "PWin", 30)
            like = _param(store, "PWin-lookalike", 30)
            rid = _rule(store, "RWin")
            store.add_dependency("params", pid, "rules", rid)

            store.invalidate_cascade("params", pid, reason="window", new_value=45)

            assert store.get("params", like)["value"] == 30
            assert _invalidates(store, "params", like) == []
            assert _actions(store, "params", like) == ["insert"]


class TestAlreadyStale:
    """Pre-staled B: no duplicate entry, traversal still reaches C."""

    def test_stale_middle_skipped_but_traversed(self, tmp_path):
        with OntologyStore(tmp_path / "stale.db") as store:
            aid = _rule(store, "AWin")
            bid = _rule(store, "BWin")
            cid = _rule(store, "CWin")
            store.add_dependency("rules", aid, "rules", bid)
            store.add_dependency("rules", bid, "rules", cid)
            store.invalidate("rules", bid, reason="earlier change")

            order = store.invalidate_cascade("rules", aid, reason="root change")

            assert order == [("rules", aid), ("rules", cid)]
            assert len(_invalidates(store, "rules", bid)) == 1
            assert store.get("rules", cid)["status"] == "stale"
            (cinv,) = _invalidates(store, "rules", cid)
            assert str(aid) in _detail(cinv)["reason"]


class TestReviewAndHistory:
    """Full insert -> invalidate order per record; prior review kept."""

    def test_history_traces_insert_to_invalidate(self, tmp_path):
        with OntologyStore(tmp_path / "hist.db") as store:
            pid = _param(store, "PWin", 30)
            rid = _rule(store, "RWin")
            store.add_dependency("params", pid, "rules", rid)
            _review(store, "RWin")

            store.invalidate_cascade("params", pid, reason="window", new_value=45)

            assert _actions(store, "params", pid) == ["insert", "invalidate"]
            assert _actions(store, "rules", rid) == [
                "insert",
                "status",
                "invalidate",
            ]
            prior = store.reviews("RWin")
            assert prior and any(e.get("reviewer") == "qa" for e in prior)


class TestDependencyValidation:
    """Unknown kinds, missing records, and duplicate edges are rejected."""

    def test_duplicate_unknown_and_missing_rejected(self, tmp_path):
        with OntologyStore(tmp_path / "valid.db") as store:
            pid = _param(store, "PWin", 30)
            rid = _rule(store, "RWin")

            store.add_dependency("params", pid, "rules", rid)
            with pytest.raises(ValueError):
                store.add_dependency("params", pid, "rules", rid)

            with pytest.raises(ValueError):
                store.add_dependency("nope", pid, "rules", rid)
            with pytest.raises(ValueError):
                store.direct_dependents("nope", pid)

            with pytest.raises(KeyError):
                store.add_dependency("params", 9999, "rules", rid)
            with pytest.raises(KeyError):
                store.add_dependency("params", pid, "rules", 9999)

            deps = store.direct_dependents("params", pid)
            assert [(d["dst_kind"], d["dst_id"]) for d in deps] == [
                ("rules", rid)
            ]


class TestStaleRootIdempotent:
    """Repeat cascade re-audits nothing: one invalidate entry per record."""

    def test_status_root_cascade_twice_single_invalidate_each(self, tmp_path):
        with OntologyStore(tmp_path / "stale-root.db") as store:
            aid = _rule(store, "AWin")
            bid = _rule(store, "BWin")
            store.add_dependency("rules", aid, "rules", bid)

            store.invalidate_cascade("rules", aid, reason="root change")
            store.invalidate_cascade("rules", aid, reason="root change")

            assert len(_invalidates(store, "rules", aid)) == 1
            assert len(_invalidates(store, "rules", bid)) == 1

    def test_status_less_root_cascade_twice_single_invalidate_each(
        self, tmp_path
    ):
        with OntologyStore(tmp_path / "stale-params-root.db") as store:
            pid = _param(store, "PWin", 30)
            rid = _rule(store, "RWin")
            store.add_dependency("params", pid, "rules", rid)

            store.invalidate_cascade("params", pid, reason="window", new_value=45)
            store.invalidate_cascade("params", pid, reason="window")

            assert len(_invalidates(store, "params", pid)) == 1
            assert len(_invalidates(store, "rules", rid)) == 1
            assert store.get("params", pid)["value"] == 45

    def test_status_less_invalidate_result_carries_no_fabricated_keys(
        self, tmp_path
    ):
        with OntologyStore(tmp_path / "no-fabricated.db") as store:
            pid = _param(store, "PWin", 30)
            result = store.invalidate("params", pid, reason="window", new_value=45)
            assert "status" not in result
            assert "superseded" not in result
            assert result["value"] == 45

            rid = _rule(store, "RWin")
            rresult = store.invalidate("rules", rid, reason="change")
            assert rresult["status"] == "stale"
            assert rresult["superseded"] is True


class TestReviewsEdgeRejected:
    """Reviews are audit history: no dependency edge may touch them."""

    def test_add_dependency_rejects_reviews_endpoints(self, tmp_path):
        with OntologyStore(tmp_path / "reviews-edge.db") as store:
            pid = _param(store, "PWin", 30)
            rid = _rule(store, "RWin")
            vid = _review(store, "RWin")
            with pytest.raises(ValueError):
                store.add_dependency("params", pid, "reviews", vid)
            with pytest.raises(ValueError):
                store.add_dependency("reviews", vid, "rules", rid)
            with pytest.raises(ValueError):
                store.add_dependency("ReviewDecision", vid, "rules", rid)


class TestDependencyReasonRedaction:
    """Secret-looking edge reasons are redacted before INSERT."""

    def test_secret_reason_redacted_in_stored_row(self, tmp_path):
        with OntologyStore(tmp_path / "reason-redact.db") as store:
            pid = _param(store, "PWin", 30)
            rid = _rule(store, "RWin")
            secret_reason = "rotation needed sk-test-secret-abcdefghij123456"
            store.add_dependency("params", pid, "rules", rid, reason=secret_reason)
            row = store.conn.execute(
                "SELECT reason FROM dependencies WHERE src_id = ? AND dst_id = ?",
                (str(pid), str(rid)),
            ).fetchone()
            stored = row["reason"]
            assert "sk-test-secret" not in stored
            assert "[REDACTED]" in stored
            deps = store.direct_dependents("params", pid)
            assert "sk-test-secret" not in deps[0]["reason"]
