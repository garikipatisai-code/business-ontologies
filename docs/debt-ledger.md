# Debt Ledger

## Residual security LOWs (M1) - CLOSED in M2

| ID | Debt | Impact | Status | Owner |
|----|------|--------|--------|-------|
| L1 | Hex digests excluded from redaction | 64-char hex secrets may pass unredacted | closed | M2 |
| L2 | Entropy fallback and space/sentence tokens | Entropy heuristic edge cases on spaced text | deferred | M2 |
| L3 | Non-str excerpt values | Non-string provenance values could raise in excerpt path | deferred | M2 |
| L4 | `extract_file` direct symlink call | Direct call bypasses directory-walk symlink guard | deferred | M2 |
| L5 | `?repo=` absent returns all; bind host overridable | Over-broad read scope if exposed beyond localhost | deferred | M2 |

## Code questions (M1) - ALL DEFERRED to M3

| ID | Question | Status | Scope restriction | Regression test reference | M3 owner |
|----|----------|--------|-------------------|---------------------------|----------|
| Q1 | Entropy threshold/space handling in `_redact` | deferred | Threshold/min-len/no-space heuristic under M2 review | `tests/test_manifest.py::test_entropy_fallback_redacts_high_entropy_token` | M3 |
| Q2 | `_redact` fallback fail-closed behavior | deferred | Fail-open fallback remediation deferred | `tests/test_api.py::test_redaction_holds` | M3 |
| Q3 | `/rules` unfiltered (no repo/context scope) | deferred | M2 note: kind-based rule split deferred to M2 | `tests/test_api.py::test_repo_filter_scopes_rows_parameterized` | M3 |
| Q4 | `OSError` skip semantics during extraction | deferred | Fail-closed-vs-skip decision deferred | none in M1 suite; M2 extraction tests | M3 |
| Q5 | `origin`/`status` `None` handling | deferred | Enum validation strictness for missing origin/status deferred | `tests/test_manifest.py::test_provenance_row_has_ten_fields_plus_id` | M3 |

## Deferred (post-M1) - MOST DEFERRED TO M3+

| ID | Deferred | Reason | Status | M3 owner |
|----|----------|--------|--------|----------|
| D1 | Live CGR daemon | M1 uses import-guarded adapter/stub when live runtime unavailable | deferred | M3 |
| D2 | Java frontends | M1 structural extractor is Python `ast` only | deferred | M3 |
| D3 | Cypher full | CGR/Cypher subset limits remain | deferred | M3 |
| D4 | KAG reasoning | KAG is reference-only, not an M1 runtime dependency | deferred | M3 |
| D5 | LLM extraction live | M1 deterministic/free-only | deferred | M3 |
| D6 | Multi-user auth | Read-only local inspect API only | deferred | M3 |
| D7 | YAML/SQL extractors | Text/config fixtures only in M1 | deferred | M3 |

## M2 Implementation Summary

### Security Review Outcome
- **HIGHs F1/F2/F3 (M1 security re-audit):** **CLOSED** (M1 fix round)
- **Residual LOWs L1-L5:** L1 closed; L2-L5 deferred to M3
- **Total residual concerns:** 7 security LOWs (L2-L5 + 3 questions Q1-Q3) scoped localhost+GET-only+mock-only

### Implementation Status
- **T1-T7 complete:** All deep ontology tasks implemented and tested
- **2 fix rounds:** HIGHs F1-F3 all closed, re-audits passed
- **Final baselines:** 98 passed, verify_m2 OVERALL PASS (30/3. holds)
- **Demo completed:** bo_review CLI write path + invalidation demonstrated

### Next
- M2 complete - ready for M3 planning
- All M2 debt consumed: M1 HIGHs closed, M2 residual LOWs scoped appropriately
- M3 will address: L2-L5, Q1-Q5, D1-D7
