# M3 Verification — A2 Thresholds (pre-eval record)

Recorded **before any eval run**. No results here — results are recorded later
by `verify_m3` (C2). This section freezes the M3A thresholds, denominators,
matching/scoring rules, and the held-out seal reference.

## Thresholds (frozen pre-eval)

| Threshold | Key | Value | Denominator (raw x/y) |
|---|---|---|---|
| Concept | `concept` | >= 0.75 | correct / total concept items |
| Rule | `rule` | >= 0.75 | correct / total rule items |
| Relation | `relation` | >= 0.70 | correct / total relation items |
| Citation format | `citation_format` | 1.0 | format-valid citations / total citations |
| Citation support | `citation_support` | >= 0.90 | supported citations / total cited items |
| Abstention | `abstention` | 1.0 | abstentions recorded / total abstention-eligible |

`THRESHOLDS` (in `src/bo_eval/harness.py`) is the single source:

```python
THRESHOLDS = {
    "concept": 0.75,
    "rule": 0.75,
    "relation": 0.70,
    "citation_format": 1.0,
    "citation_support": 0.90,
    "abstention": 1.0,
}
```

## Matching / scoring rules

- **correct**: items whose extractor output matches the independent,
  fixture-derived ground truth (labels read from fixture source via `ast`,
  never hardcoded to the extractor), per category.
- **total**: raw denominator — all items in the category in scope for this
  run, before exclusions.
- **precision**: `correct / total` derived from raw counts (only when
  `total > 0`).
- **proposed_excluded**: proposed-status items are excluded from the
  `correct`/`total` numerators; reported explicitly per category, and
  `correct + proposed_excluded <= total` always holds.
- **false positives**: reportable list; each entry has `{category, item,
  reason}`. Relation category allows **0 false equivalents** — any
  `false_equivalent` entry fails the relation threshold gate.
- **abstention**: counted explicitly as its own category (not omitted);
  dynamic dispatch (e.g. repo-e `CreditGatewayClient.call` via `getattr` on a
  variable name) abstains — no edge, no inferred relation.

## Compatibility != equivalence (contract resolver, `src/bo_resolve`)

`resolve(repo_paths)` returns `{call_edges, event_schema_links,
shared_table_links}`. Every link carries `compatibility =
{"compatible": True, "equivalent": False}` by default; a shared table,
matching event schema, or call edge never implies equivalence; no
`equivalentTo`/`equivalent` is True without a human-accepted mapping argument.

## Seal reference

Held-out inputs: `fixtures/repo-e/{app,rules,contracts}.py`, seal:
`docs/m3_heldout_seal.json` (sha256 per file, frozen pre-eval; tuning on this
set reclassifies it as dev). Seal integrity is verified independently
(`tests/test_m3_heldout_sealed.py` section (a)).

## M3A scope note

M3A = held-out repo-e + seal + contract resolver + eval harness + thresholds
+ frozen contract/eval tests. Out of A2 scope: extractor
(`src/bo_extract/deterministic.py`), alignment, API views, M3B plumbing, M3C.
The two known extractor-gap failures in `tests/test_m3_heldout_sealed.py`
(StoreCreditAccount decision rules; credit_limit/MAX_CREDIT_BALANCE link)
belong to `deterministic.py`, which is not in approved A2 scope — reported,
not fixed here.

## Verification command

```bash
python3 -m pytest tests/test_m3_contract.py tests/test_m3_eval.py -v
python3 -m pytest tests/test_m3_heldout_sealed.py -q   # 2 extractor-gap REDs expected
```

## Compliance

- stdlib only; no network, no import/exec of fixtures; read-only analysis.
- No results recorded yet — results come from runs (C2 `verify_m3`).

## Results (C2 audit run — interrupted dispatch completion)

Appended during the C2b audit/repair pass. Pre-existing content above preserved byte-for-byte (thresholds section frozen pre-eval, untouched).

- Plan hash: `82b386f9fd964717e418e1e451d98e16080ddf061c60339508a11ccdd807eedb`
- Alignment suite (`tests/test_m3_alignment.py`): 10 passed, 1 failed after in-scope repair (shared-table insufficient-evidence mapping); remaining failure is in `src/bo_ontology/invalidation.py`, outside the C2b allowlist — reported, not hidden.
- M1/M2 regression suites: 25 passed (GREEN).
- M3A suites (`tests/test_m3_contract.py`, `tests/test_m3_eval.py`): 11 passed (GREEN).
- M3B protocol suite (`tests/test_m3b_protocol.py`): 11 passed (GREEN, mock-only).
- Held-out sealed (`tests/test_m3_heldout_sealed.py`): 2 failed, 8 passed — both known BLOCKED extractor gaps (StoreCreditAccount decision rules; credit_limit/MAX_CREDIT_BALANCE link), pending separate amendment.
- `python3 scripts/verify_m3.py`: exit 1; gate table printed (full table in script output). BLOCKED solely on the 2 known sealed extractor gaps; one real alignment failure noted above.

## Results (D4 final evidence run — 2026-11-17)

Appended during D4 (full gates, diff review, final evidence, docs finalize). Pre-existing content above preserved byte-for-byte (frozen thresholds + prior result sections kept as history).

### Final gate runs (D4)

- Full suite: `python3 -m pytest tests/ -q` → **162 passed in 3.42s**, exit `0` (collected=162, passed=162, failed=0, skipped=0).
- `python3 scripts/verify_m1.py` → **OVERALL: PASS**, exit `0` (6/6 checks PASS).
- `python3 scripts/verify_m2.py` → **OVERALL: PASS**, exit `0` (7/7 checks PASS).
- `python3 scripts/verify_m3.py` → **gates green, exit `0`** (plan-hash OK; files OK; M1-M2 GREEN; M3A GREEN; M3C alignment GREEN; seal OK; M3B protocol GREEN; M3A held-out GREEN; thresholds PRESENT; heuristic-table OK; demo-transcript OK; known BLOCKED sealed extractor gaps listed). Gate table printed; no FAIL/BLOCKED gate remained.

### Exposed-set regression note (held-out repo-e)

- Original exposed repo-e run: held-out sealed suite → **8/10 passed**, 2 failed — both known BLOCKED extractor gaps (repo-e StoreCreditAccount decision rules; credit_limit / MAX_CREDIT_BALANCE link); gate BLOCKED (exit 1).
- Post-fix exposed repo-e rerun: deterministic extractor extended (2 general shapes, no literals) → held-out sealed suite → **10/10 passed**, exit 0; verify_m3 `M3A held-out` GREEN.
- Per approval, the exposed repo-e rerun counts as **regression evidence only**; fresh-holdout acceptance **PENDING** (no independent second set created in scope).

### Final counts (D4)

- Full suite: **162 passed** (0 failed / 0 skipped), exit `0`.
- M1/M2 regression selection (`tests/test_m2_extractor.py`, `tests/test_m2_combined_index.py`, `tests/test_m2_ground_truth.py`): **25 passed** (GREEN).
- M3A suites (`tests/test_m3_contract.py`, `tests/test_m3_eval.py`): **11 passed** (GREEN).
- M3B protocol (`tests/test_m3b_protocol.py`): **11 passed** (GREEN, mock-only).
- M3C alignment (`tests/test_m3_alignment.py`): **11 passed** (GREEN).
- M3A dev ground truth (`tests/test_m3_dev_ground_truth.py`): **20 passed** (GREEN).
- M3A held-out (`tests/test_m3_heldout_sealed.py`): **10 passed** (GREEN post-fix; exposed set).

### Pending / blocked items (D4)

- Fresh-holdout acceptance: **PENDING** (independent second holdout set not created in scope; exposed repo-e rerun = regression evidence only, per approval).
- M3B execution: **BLOCKED** (live model arm requires separate auth record; protocol plumbing green mock-only).
- Post-M3 Java/Spring gate: **PENDING** (language honesty: Python-only bounded experiment does not satisfy Java/Spring/SQL).
