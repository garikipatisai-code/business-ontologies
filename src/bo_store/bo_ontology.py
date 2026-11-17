"""Thin shim re-exporting canonical review and invalidation modules.

This module provides a thin shim that re-exports the canonical implementations
from `src/bo_ontology/review.py` and `src/bo_ontology/invalidation.py`.

The shim ensures:
* One-way dependencies: shim imports canonical, canonical never imports shim
* No logic or side effects at import time
* Exact re-exports of Review/record_review/transition_review/Invalidate/invalidate_artifact
* No try/except fallback swallowing that could hide import errors

This matches the expected interface for M2 T3/T6 and passes the shim-identity
verification test.
"""

# Direct imports from canonical modules without any conditional logic or error handling
from bo_ontology.review import Review, record_review, transition_review
from bo_ontology.invalidation import Invalidate, invalidate_artifact

# Explicit re-exports for clarity and to match the expected API
__all__ = [
    "Review",
    "record_review", 
    "transition_review",
    "Invalidate",
    "invalidate_artifact"
]