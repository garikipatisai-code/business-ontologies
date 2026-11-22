"""Store change-trace regression: a source change invalidates the old row.

Simulates ``windowDays`` changing out-of-band (30 -> 45): the stale
assertion is invalidated through the store (audit-log history preserves
``original_*``), unrelated rows (rule/param) are untouched, and the prior
review decision stays inspectable. Uses existing store kinds only
(assertions/rules/params/reviews); no new kind is introduced.
"""

from __future__ import annotations

import json
from pathlib import Path

from bo_ontology.schema import (
    Assertion,
    PolicyParameter,
    ReviewDecision,
    Rule,
)
from bo_ontology.store import OntologyStore


class TestStoreChangeTrace:
    """Source change -> invalidate old, history keeps originals, rest intact."""

    def test_window_change_invalidates_old_preserves_rest_and_reviews(
        self, tmp_path
    ):
        db = tmp_path / "trace.db"
        with OntologyStore(db) as store:
            aid = store.add_assertion(
                Assertion(
                    subject="refund_window_days",
                    predicate="allows",
                    object=30,
                    status="accepted",
                    origin="deterministic",
                )
            )
            rid = store.add_rule(
                Rule(
                    name="refund_window",
                    statement="refunds allowed within windowDays",
                    status="accepted",
                )
            )
            pid = store.add_parameter(
                PolicyParameter(name="windowDays", value=30)
            )
            store.record_review(
                ReviewDecision(
                    subject="refund_window_days",
                    status="accepted",
                    reviewer="qa",
                    reason="window matches policy",
                    original_status="proposed",
                )
            )

            # Source change out-of-band: windowDays 30 -> 45.
            result = store.invalidate(
                "assertions",
                aid,
                reason="source changed: windowDays 30 -> 45",
                new_value=45,
            )
            assert result["status"] == "stale"
            assert result["value"] == 45
            assert result.get("original_value") == 30
            assert result.get("original_status") == "accepted"

            hist = store.history("assertions", aid)
            actions = [entry.get("action") for entry in hist]
            assert "insert" in actions and "invalidate" in actions, (
                f"history must trace insert to invalidate: {actions!r}"
            )
            assert actions.index("insert") < actions.index("invalidate")
            inv = next(
                entry
                for entry in hist
                if entry.get("action") == "invalidate"
            )
            detail = inv.get("detail", {})
            if isinstance(detail, str):
                detail = json.loads(detail)
            assert detail.get("original_value") == 30, (
                f"invalidate entry must preserve original value: {detail!r}"
            )
            assert detail.get("original_status") == "accepted"

            # Unrelated rows untouched.
            assert store.get("rules", rid)["status"] == "accepted"
            assert store.get("params", pid)["value"] == 30

            # Prior review decision stays inspectable.
            prior = store.reviews("refund_window_days")
            assert prior, "prior review must remain inspectable"
            assert any(
                entry.get("reviewer") == "qa" for entry in prior
            ), f"prior review lost: {prior!r}"

        assert Path(db).exists()
