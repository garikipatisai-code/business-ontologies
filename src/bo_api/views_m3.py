"""M3C read-only JSON views.

Provides concepts_json(), alignment_json(), workflow_json() for server mount.
All views are JSON serializable and carry no write verbs.
"""

from __future__ import annotations

import json
from typing import Any

# --------------------------------------------------- concepts_json

def concepts_json(concepts: list[dict[str, Any]]) -> str:
    """Return read-only JSON for *concepts*.

    Each concept carries its name, domain, scope, origin, status and
    supporting evidence. The output is idempotent and JSON‑serializable.
    """
    # Filter out any non‑serializable fields; preserve all original fields
    out = []
    for c in concepts:
        # Ensure json.dumps can serialize this dict
        try:
            json.dumps(c)
            out.append(c)
        except (TypeError, ValueError):
            # Strip problematic fields
            safe = {k: v for k, v in c.items() if k not in {"lambda", "func"}}
            out.append(safe)
    return json.dumps(out, default=str)

# --------------------------------------------------- alignment_json

def alignment_json(mappings: list[dict[str, Any]]) -> str:
    """Return read-only JSON for typed mappings *mappings*.

    Includes subject, object, kind and evidence. No write operations possible.
    """
    # Validate mapping structure
    for m in mappings:
        if "kind" not in m or "subject" not in m or "object" not in m:
            raise ValueError("mapping missing required fields: kind, subject, object")
    return json.dumps(mappings, default=str)

# --------------------------------------------------- workflow_json

def workflow_json(steps: list[dict[str, Any]]) -> str:
    """Return read-only JSON for workflow *steps*.

    Steps contain order, status, evidence, and reason (for unresolved).
    JSON serializable and immutable for client consumption.
    """
    for s in steps:
        if "order" not in s:
            raise ValueError("step missing required field: order")
    return json.dumps(steps, default=str)

# --------------------------------------------------- __all__

__all__ = ["concepts_json", "alignment_json", "workflow_json"]