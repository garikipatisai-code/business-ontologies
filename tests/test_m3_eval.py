"""M3 A2 eval harness interface tests.

RED by design: src/bo_eval does not exist yet, so the module-level import
below raises ModuleNotFoundError at collection time (harness missing). These
tests specify the frozen interface once bo_eval.harness lands:

evaluate() -> per-category {correct, total, precision} with raw counts;
thresholds frozen: concept >= 0.75, rule >= 0.75, relation >= 0.70 (+ 0 false
equiv), citation 1.0 format / >= 0.90 support, abstention 1.0;
denominators + raw x/y reported; proposed-status items excluded from
numerators; abstention counted explicitly.
"""

from bo_eval.harness import evaluate, THRESHOLDS  # noqa: F401  (module missing -> collection error RED)

CATEGORIES = ("concept", "rule", "relation", "citation", "abstention")

EXPECTED_THRESHOLDS = {
    "concept": 0.75,
    "rule": 0.75,
    "relation": 0.70,
    "citation_format": 1.0,
    "citation_support": 0.90,
    "abstention": 1.0,
}


def test_evaluate_returns_per_category_correct_total_precision_with_raw_counts():
    """evaluate() returns per-category {correct, total, precision} with raw
    count denominators (x/y); precision derived from raw counts."""
    report = evaluate()
    assert set(report) >= set(CATEGORIES), report
    for cat in CATEGORIES:
        entry = report[cat]
        assert set(entry) >= {"correct", "total", "precision"}, entry
        assert isinstance(entry["correct"], int), entry
        assert isinstance(entry["total"], int), entry
        if entry["total"] > 0:
            assert entry["precision"] == entry["correct"] / entry["total"], entry


def test_thresholds_are_frozen_before_any_run():
    """Thresholds frozen pre-eval: 0.75 / 0.75 / 0.70 / 1.0 / 0.90 / 1.0."""
    assert THRESHOLDS == EXPECTED_THRESHOLDS, THRESHOLDS
    assert THRESHOLDS["concept"] == 0.75
    assert THRESHOLDS["rule"] == 0.75
    assert THRESHOLDS["relation"] == 0.70
    assert THRESHOLDS["citation_format"] == 1.0
    assert THRESHOLDS["citation_support"] == 0.90
    assert THRESHOLDS["abstention"] == 1.0


def test_relation_threshold_requires_zero_false_equivalents():
    """Relation category: >= 0.70 with 0 false equivalents allowed."""
    assert THRESHOLDS["relation"] == 0.70
    report = evaluate()
    fps = report.get("false_positives", [])
    assert isinstance(fps, list)
    false_equiv = [fp for fp in fps if fp.get("kind") == "false_equivalent"]
    assert false_equiv == [], false_equiv


def test_false_positive_list_is_reportable():
    """False-positive list exists as a reportable list (may be empty, must
    be able to be non-empty); each entry is describable."""
    report = evaluate()
    fps = report.get("false_positives", [])
    assert isinstance(fps, list)
    for fp in fps:
        assert set(fp) >= {"category", "item", "reason"}, fp


def test_proposed_status_items_excluded_from_numerators():
    """Proposed-status items are excluded from correct/total numerators."""
    report = evaluate()
    for cat in CATEGORIES:
        entry = report[cat]
        assert "proposed_excluded" in entry, entry
        assert isinstance(entry["proposed_excluded"], int), entry
        assert entry["correct"] + entry["proposed_excluded"] <= entry["total"], entry


def test_abstention_counted_explicitly():
    """Abstention is its own category, counted explicitly (not omitted)."""
    report = evaluate()
    entry = report["abstention"]
    assert set(entry) >= {"correct", "total", "precision"}, entry
    assert isinstance(entry["correct"], int), entry
    assert isinstance(entry["total"], int), entry
    if entry["total"] > 0:
        assert entry["precision"] == entry["correct"] / entry["total"], entry
