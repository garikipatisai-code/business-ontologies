"""M2 review lifecycle contract (T2b grader; RED until review store lands)."""

import pytest

from bo_store.bo_ontology import (
    Review,
    record_review,
    transition_review,
)


def _make_review(status="proposed", **kwargs):
    data = dict(
        subject="refund_window_days",
        value=30,
        status=status,
        reviewer="human",
        reason="rationale",
        version=1,
    )
    data.update(kwargs)
    return data


def test_proposed_to_accepted_transition():
    """proposed -> accepted is a valid lifecycle transition."""
    review = _make_review(status="proposed")
    updated = transition_review(review, "accepted")
    assert updated["status"] == "accepted"


def test_proposed_to_rejected_transition():
    """proposed -> rejected is a valid lifecycle transition."""
    review = _make_review(status="proposed")
    updated = transition_review(review, "rejected")
    assert updated["status"] == "rejected"


def test_proposed_to_disputed_transition():
    """proposed -> disputed is a valid lifecycle transition."""
    review = _make_review(status="proposed")
    updated = transition_review(review, "disputed")
    assert updated["status"] == "disputed"


def test_review_records_identity_time_reason_version():
    """Review records reviewer identity, time, reason, version."""
    review = record_review(_make_review())
    assert review.get("reviewer") is not None
    assert review.get("reason") is not None
    assert review.get("version") == 1


def test_review_correction_preserves_original():
    """Correction keeps the original review context intact."""
    review = _make_review(status="accepted")
    corrected = transition_review(review, "disputed")
    assert corrected["status"] == "disputed"
    assert corrected.get("original_status") == "accepted"
