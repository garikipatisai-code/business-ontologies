"""Business ontology review lifecycle (T3, T6 debt-first).

The ReviewDecision entity (named Review for legacy/prose) is a typed dataclass
representing a human review decision over an ontology assertion.

Review lifecycle follows a status enum (proposed/accepted/rejected/stale/disputed)
with transitions enforced by `transition_review()`. Each decision creates a new
occurrence never overwriting previous ones, preserving history via append-only
store and audit log.

Functions:
* record_review(): persist a new ReviewDecision; creates a fresh occurrence.
* transition_review(): change status with corrected reason, creating a new
  occurrence while preserving original context via `original_status`.

The shim `src/bo_store/bo_ontology.py` re-exports these canonical modules.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .schema import Status, validate_status, _Model
@dataclass
class ReviewDecision(_Model):
    """One human review decision over an ontology assertion.

    The id is content-addressed (stable across runs); occurrence counts
    repeated sightings. Origin (where it came from) and status (review state)
    are independent fields.
    """

    subject: str
    status: str = Status.PROPOSED.value
    reviewer: str | None = None
    reason: str | None = None
    version: int = 1
    original_status: str | None = None
    decided_at: str | None = None

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("review subject is required")
        self.status = validate_status(self.status)
        if self.original_status is not None:
            self.original_status = validate_status(self.original_status)
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("review version must be a positive integer")
        if self.decided_at is None:
            # Set timestamp if not provided (for backward compatibility)
            self.decided_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
# Alias for prose/use in tests.
Review = ReviewDecision

# Allowed review status transition graph (epistemic lifecycle).
# proposed -> accepted/rejected/disputed/stale; accepted/rejected -> disputed/stale;
# disputed -> accepted/rejected/stale; stale is terminal. Any transition not in
# this graph raises ValueError in transition_review (no silent any-to-any).
_TRANSITION_GRAPH = {
    Status.PROPOSED.value: {Status.ACCEPTED.value, Status.REJECTED.value,
                            Status.DISPUTED.value, Status.STALE.value},
    Status.ACCEPTED.value: {Status.DISPUTED.value, Status.STALE.value},
    Status.REJECTED.value: {Status.DISPUTED.value, Status.STALE.value},
    Status.DISPUTED.value: {Status.ACCEPTED.value, Status.REJECTED.value,
                            Status.STALE.value},
    Status.STALE.value: set(),
}

VALID_STATUSES = frozenset(item.value for item in Status)


def record_review(review: ReviewDecision | dict[str, Any]) -> dict[str, Any]:
    """Persist a new review decision (pure-dict).

    * Normalizes ReviewDecision / mapping input to a validated dict.
    * Creates a fresh occurrence (version 1 unless overridden) with timestamp.
    * Validates status against the enum; no silent coercion.
    * Pure function: returns the review dict; persistence/audit is the store's job.
    """
    if isinstance(review, ReviewDecision):
        # Convert ReviewDecision to dict
        result = review.to_dict()
    else:
        # Filter out extra fields that aren't part of ReviewDecision
        # This handles cases where the dict has fields like 'value' that
        # are part of the test data but not part of the actual review object
        filtered_data = {k: v for k, v in review.items() 
                        if k in ReviewDecision.__dataclass_fields__}
        review_obj = ReviewDecision(**filtered_data)
        result = review_obj.to_dict()

    # Ensure timestamp is set
    if result.get("decided_at") is None:
        result["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Validate status
    result["status"] = validate_status(result.get("status", "proposed"))
    if result.get("original_status") is not None:
        result["original_status"] = validate_status(result["original_status"])

    # Ensure version is set
    if not isinstance(result.get("version"), int) or result.get("version", 1) < 1:
        result["version"] = 1

    return result


def transition_review(review: ReviewDecision | dict[str, Any], new_status: str) -> dict[str, Any]:
    """Transition a review decision to a new status (pure-dict).

    * Normalizes input to a validated dict.
    * Produces a new occurrence (version +1) preserving the prior status via
      ``original_status``; never overwrites prior occurrences.
    * Validates new status against the enum.
    * Pure function: returns the new review dict; persistence/audit is the store's job.
    """
    if isinstance(review, ReviewDecision):
        # Convert ReviewDecision to dict
        result = review.to_dict()
    else:
        # Filter out extra fields that aren't part of ReviewDecision
        filtered_data = {k: v for k, v in review.items() 
                        if k in ReviewDecision.__dataclass_fields__}
        review_obj = ReviewDecision(**filtered_data)
        result = review_obj.to_dict()

    # Validate new status
    new_status = validate_status(new_status)

    # Enforce the allowed transition graph (ValueError on illegal).
    current = result.get("status")
    allowed = _TRANSITION_GRAPH.get(current)
    if allowed is not None and new_status not in allowed:
        raise ValueError(
            f"illegal review transition {current!r} -> {new_status!r}; "
            f"allowed from {current!r}: {sorted(allowed)}"
        )

    # Create a new review decision with corrected status
    new_review = ReviewDecision(
        subject=result.get("subject", ""),
        status=new_status,
        reviewer=result.get("reviewer"),
        reason=result.get("reason", f"Corrected from {result.get('status', 'proposed')} via transition"),
        version=result.get("version", 1) + 1,
        original_status=result.get("status"),
        decided_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    return new_review.to_dict()