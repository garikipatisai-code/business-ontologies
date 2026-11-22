"""Business ontology artifact invalidation (T6 debt-first).

The Invalidate type is a typed decision that an artifact (rule, relation, flow,
step, or assertion) has changed out-of-band (manual/automated) and must be
marked stale/superseded.

Key constraints:
* Invalidations are append-only (history preserved via audit log).
* A changed artifact is marked stale/superseded; recomputation of affected
  dependents and preservation of unrelated artifacts are the store layer's
  responsibility (``OntologyStore.invalidate`` leaves unrelated rows
  untouched).
* No silent carry-over: old values must not survive recomputation.
* Superseded artifacts are marked stale+reason; reason links back to the
  invalidation decision via version.
* Invalidation never silently carries acceptance; prior acceptance is
  superseded.

Functions:
* invalidate_artifact(): apply invalidation to a single artifact dict;
  returns the invalidated artifact with stale/superseded status
  (dependents/preservation belong to the store).

The shim `src/bo_store/bo_ontology.py` re-exports these canonical modules.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from .schema import Status, validate_status
from .store import _ABSENT
@dataclass
class Invalidate:
    """Invalidation decision over an ontology artifact.

    Represents a manual or automated decision that an artifact has changed
    out-of-band, requiring recomputation and marking affected artifacts as
    stale/superseded.
    """

    subject: str
    value: Any | None = None
    condition: str | None = None
    evidence: str | None = None
    reason: str | None = None
    timestamp: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("invalidation subject is required")

        if self.timestamp is None:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("invalidation version must be a positive integer")

    @property
    def superseded(self) -> bool:
        """Return True if this invalidation supersedes a prior artifact."""
        return self.value is not None or self.condition is not None or self.evidence is not None

    @property
    def status(self) -> str:
        """Return the epistemic status for a superseded artifact."""
        return Status.STALE.value

    @property
    def is_unrelated_preserved(self) -> bool:
        """Return True if this invalidation affects unrelated artifacts only.

        Pure-dict shim: this function does not traverse artifact relationships,
        so unrelated preservation is not computed here and always reports
        False. The store layer owns relationship-aware preservation.
        """
        return False
"""Sentinel re-exported from ``bo_ontology.store`` so both modules share one
'new_value not supplied' identity (explicit None stays storable)."""


def invalidate_artifact(
    artifact: dict[str, Any],
    reason: str | None = None,
    new_value: Any = _ABSENT,
) -> dict[str, Any]:
    """Apply invalidation to a single artifact only.

    * Marks the supplied artifact dict as stale/superseded with the provided
      (or generated) reason, linking version back to the invalidation.
    * Touches only this dict: recomputation of affected dependents and
      preservation of unrelated artifacts are the store layer's
      responsibility (see ``OntologyStore.invalidate``), not done here.
    * Pure (no DB/audit): returns a new dict without touching any store or
      audit log; persistence/audit is the store layer's responsibility via
      ``OntologyStore.invalidate``.
    * Returns the invalidated artifact with stale status.

    Amendment 2: optional ``new_value`` supplies the post-invalidation value.
      * When omitted, behavior is unchanged (original_* captured from the
        supplied artifact, current value preserved).
      * When given, the returned record carries ``value=new_value`` while
        ``original_*`` fields are captured from the supplied artifact (never
        reconstructed). Explicit ``None`` is a storable value, not absence.
    """
    # Create a copy to avoid mutating the original
    result = dict(artifact)

    # Capture prior state BEFORE mutating so original_status reflects the
    # pre-invalidation status (e.g. "accepted"), not the new "stale".
    orig_status = artifact.get("status")

    # Set invalidation timestamp
    result["invalidation_timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Apply the invalidation reason
    if reason:
        result["invalidation_reason"] = reason
    else:
        result["invalidation_reason"] = "Artifact changed out-of-band (invalidated)"

    # Mark as superseded
    result["superseded"] = True

    # Mark as stale
    result["status"] = Status.STALE.value

    # Amendment 2: explicit post-invalidation value (when supplied)
    if new_value is not _ABSENT:
        result["value"] = new_value

    # Preserve original values for history/recomputation
    # Only set if not already set (to preserve original_value from changed artifact)
    if "original_value" not in result:
        result["original_value"] = artifact.get("value")

    if "original_condition" not in result:
        result["original_condition"] = artifact.get("condition")

    if "original_evidence" not in result:
        result["original_evidence"] = artifact.get("evidence")

    if "original_status" not in result:
        result["original_status"] = orig_status

    # Ensure version tracking
    if "invalidation_version" not in result:
        result["invalidation_version"] = 1

    return result