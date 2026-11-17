"""M3 A2 eval harness: frozen thresholds + per-category report.

``evaluate()`` returns per-category ``{correct, total, precision}`` with raw
count denominators (x/y), plus ``proposed_excluded`` counts and a
``false_positives`` list.

Thresholds are frozen before any run:
  concept >= 0.75, rule >= 0.75, relation >= 0.70 (+ 0 false equivalents),
  citation_format >= 1.0, citation_support >= 0.90, abstention == 1.0.

This module records no run results — results are recorded later by
``verify_m3`` (C2). The baseline here is the pre-eval shape: raw 0/0
denominators per category, ``proposed_excluded`` = 0, empty false-positives
list, abstention counted explicitly as its own category.
"""

from __future__ import annotations

THRESHOLDS = {
    "concept": 0.75,
    "rule": 0.75,
    "relation": 0.70,
    "citation_format": 1.0,
    "citation_support": 0.90,
    "abstention": 1.0,
}

CATEGORIES = ("concept", "rule", "relation", "citation", "abstention")


def _entry(correct=0, total=0, proposed_excluded=0):
    """Raw-count category entry; precision derived from raw counts."""
    correct = int(correct)
    total = int(total)
    return {
        "correct": correct,
        "total": total,
        "precision": float(correct / total) if total else 0.0,
        "proposed_excluded": int(proposed_excluded),
    }


def evaluate():
    """Evaluate against the frozen thresholds (pre-eval baseline).

    No scored items are available to this module (ground truth is supplied
    externally by C2/verify_m3), so every category reports a raw 0/0
    denominator, ``proposed_excluded`` = 0, and an empty
    ``false_positives`` list. Abstention is its own counted category.
    """
    report = {cat: _entry() for cat in CATEGORIES}
    report["false_positives"] = []
    return report
