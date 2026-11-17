# M2 Verification

Run from repository root.

## M2 Baseline Header — M2 T1 Reconciliation (2026-11-17)

- Account: console-openrouter2 | Task: M2 T1 (Reconcile baseline + close security/review debt) | Effort: medium | Thinking: on
- Source baseline: M1 walking skeleton — pytest `30 passed in 3.59s`; `scripts/verify_m1.py` OVERALL PASS (manifest hash `9bf51bb49a4e9d348a75b97904246717311391754b81fe8e94f1691646a03854` x2, smoke, redaction `[REDACTED]`, egress `0`, pin `code-graph-rag==0.7.1 #a4132ce7de7222f8de88d41a41f2ab80c87dfe8e`).
- Historical note: `docs/verification-m1.md` recorded stale `19 passed`; preserved as history.

## T1 Result (2026-11-17)

- Command: `python3 -m pytest -q` and `python3 scripts/verify_m1.py` from repository root.
- pytest: `30 passed in 3.09s` (wall `4.04s`), exit `0` — 30/30 (26 test functions + parametrization; 5 secret-format redaction cases).
- `scripts/verify_m1.py`: all 6 checks `[PASS]` (`manifest_verify_hash_stable_2x`, `pytest`, `smoke`, `redaction_grep`, `egress_guard`, `cgr_pin`); `OVERALL: PASS`; exit `0` (wall `5.04s`).
- Commit: `d7c3d8c52a7a506297c6405bfc0548fa665455cb`.
- CGR stub-only confirmed: runtime `import cgr` failed → `cgr = None`; `CgrAdapter.available = False`. Pin `code-graph-rag==0.7.1 #a4132ce7de7222f8de88d41a41f2ab80c87dfe8e` is a pin, not a live integration (live CGR runtime deferred, debt D1).
- Call edge: implementation evidence only — cross-repo `invokes` edges remain deterministic provenance/evidence edges (origin `deterministic`, source+target file/line both ends); not published ontology assertions (C5).

## Debt Table — Residual LOWs (M1) + Open Questions (M1)

Recorded at M2 T1 (closed-or-deferred with scope restriction and regression test reference). Append-only.

### Residual security LOWs (M1)

| ID | Debt | Status | Scope restriction | Regression test reference |
|----|------|--------|-------------------|---------------------------|
| L1 | Hex digests excluded from redaction | closed | Pure-hex content hashes/commit SHAs treated as metadata; entropy fallback excludes hex tokens | `tests/test_manifest.py::test_hex_digest_not_redacted`, `tests/test_manifest.py::test_entropy_fallback_redacts_high_entropy_token` |
| L2 | Entropy fallback and space/sentence tokens | deferred | Entropy fallback scoped to single non-hex token >=20 chars, no-space, entropy >=4.5; sentence/multi-token redaction deferred | `tests/test_manifest.py::test_entropy_fallback_redacts_high_entropy_token` (current scope) |
| L3 | Non-str excerpt values | deferred | Fail-closed handling for non-string excerpt values deferred (redact_value passes non-str through) | none in M1 suite; M2 `tests/test_m2_security/` |
| L4 | `extract_file` direct-call symlink | deferred | Directory-walk symlink guard only; direct-call guard deferred to M2 T2+ | `tests/test_cross_repo.py::test_extract_skips_symlinked_python_file` (walk-level); direct-call pending |
| L5 | `?repo=` absent returns all; bind host overridable | deferred | localhost-default binding; host override local-only; exposure beyond localhost deferred | `tests/test_api.py::test_repo_filter_scopes_rows_parameterized` |

### Code questions (M1)

| ID | Question | Status | Scope restriction | Regression test reference |
|----|----------|--------|-------------------|---------------------------|
| Q1 | Entropy threshold/space handling in `_redact` | deferred | Threshold/min-len/no-space heuristic under M2 review | `tests/test_manifest.py::test_entropy_fallback_redacts_high_entropy_token` |
| Q2 | `_redact` fallback fail-closed behavior | deferred | server.py fallback `_redact` returns value unchanged (fail-open) when provenance unavailable | `tests/test_api.py::test_redaction_holds` |
| Q3 | `/rules` unfiltered (no repo/context scope) | deferred | M2 note: kind-based rule split deferred to M2; `?repo=` scoping present | `tests/test_api.py::test_repo_filter_scopes_rows_parameterized`, `tests/test_api.py::test_get_returns_persisted_rows` |
| Q4 | `OSError` skip semantics during extraction | deferred | Fail-closed-vs-skip decision deferred | none in M1 suite; M2 extraction tests |
| Q5 | `origin`/`status` `None` handling | deferred | Enum validation strictness for missing origin/status deferred | `tests/test_manifest.py::test_provenance_row_has_ten_fields_plus_id` |

### Security review outcome

- HIGHs F1/F2/F3 (security re-audit): **closed** (M1 fix round; see `docs/STATUS.md`).
- Residual LOWs L1-L5 and open Qs Q1-Q5: recorded at M2 T1 as closed-or-deferred with scope restrictions and regression test references (this file).
- Debt ledger remains source of truth: `docs/debt-ledger.md` (append-only).

## Deferred (post-M1) still open

- D1 Live CGR daemon: deferred — M1 uses import-guarded adapter/stub when live runtime unavailable (confirmed stub-only at M2 T1).
- D5 LLM extraction live: deferred — M1 deterministic/free-only.
- D6 Multi-user auth: deferred — read-only local inspect API only.

## Verification command + result

```bash
python3 -m pytest -q && python3 scripts/verify_m1.py
```

- pytest: `30 passed in 3.09s`, exit `0`.
- `scripts/verify_m1.py`: `OVERALL: PASS`, exit `0`.
- All 6 checks `[PASS]`.

## T7 Result (2026-11-17)

- Command: `python3 scripts/verify_m2.py` from repository root.
- `scripts/verify_m2.py`: `OVERALL: PASS`, exit `0` (wall `~5.00s`).
- Checks:
  - **full_pytest**: `98 passed in 3.23s`, exit `0` (30 M1 + 68 M2 tests)
  - **m1_verifier**: `OVERALL: PASS`, all 6 M1 checks pass
  - **semantic_adapter_mock**: `6 passed in 0.01s`, exit `0`
  - **confinement_redaction**: redaction excerpt `[REDACTED]`, token removed
  - **per_category_eval**: 15/15 category tests correct (rules, relations, events, constants, abstention)
  - **citation_validity**: format and support validation pass
  - **m1_end_to_end**: verified via M1 verifier
- All verification checks pass; ledger written to `docs/verification-m2-ledger.json`.
- Per-category precision/recall: all categories 1.0 (100%)
- Ledger reference: `docs/verification-m2-ledger.json`

## Demo Transcript Reference

M2 close demonstration completed:

### 1. Deterministic Extraction Demo
```bash
python3 -c "import sys; sys.path.insert(0, 'src'); from bo_extract.deterministic import extract; n,e,r=extract('fixtures'); print('rule statement, transition guard/target, event construction present + publication abstain'); print('rules:', len(r)); print('events:', len(e)); print('transitions:', len(n)); print('Sample rule name:', r[0]['name'] if r else 'None')"
```
**Output**: `3 rules, 34 events, 40 transitions`, sample rule `refund_window_days`

### 2. Business Ontology Review CLI Demo
```bash
python3 scripts/bo_review.py propose --subject refund_window_days --reviewer demo --reason demo
python3 scripts/bo_review.py accept --subject refund_window_days --reviewer demo --reason "demo acceptance"
python3 scripts/bo_review.py history --subject refund_window_days
```
**Output**: Review created → Review accepted → Complete history with 3 entries (proposed → accepted)

### 3. Invalidation Demo
```python
from bo_ontology.invalidation import invalidate_artifact
modified_rule = {**original_rule, 'value': 999}
invalidated = invalidate_artifact(modified_rule, 'Demonstration: value changed')
```
**Output**: Original status preserved → Invalidated status = `stale` → Superseded = `True` → Original value = `30` preserved, new value = `999`

All demos completed successfully with full history preservation.

## Ledger Reference

Ledger: `docs/verification-m2-ledger.json`

Generated: 2026-11-17
Task: M2 T7

## Compliance

- **C1**: Debt-first append-only honored
- **C2**: Construction≠publication enforced
- **C3**: Mock-only NOT EVALUATED (semantic quality)
- **C4**: Independent ground truth per-category
- **C5**: Call edge stays evidence only
- **C6**: Scope equals approved allowlist (deterministic.py, bo_ontology schema/store/review/invalidation/migration, migrations/0001_init.sql, semantic_adapter.py, server.py, bo_review CLI + verify_m2.py, fixtures, tests, docs)

## Notes

- Human approval recorded (2026-11-17, approved_by human-garikipatisai-code-2026-11-17)
- Machine/TUI approval NOT claimed
- Deterministic origin only (free-only model routing)
- No secrets, no network egress
- Read-only analysis, no exec/import of untrusted code
- Secret redaction in provenance
- Egress disabled (SSRF protection)
- Local-first deployment
- **M2 COMPLETE** - ready for M3 planning
