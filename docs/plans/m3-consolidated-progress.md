# Goal

Consolidated recovery-and-M3 execution: isolate original M2 input corpus (R0), complete M3A evidence/contract/eval on dev + sealed held-out, add M3B provider-neutral plumbing only (BLOCKED execution), and deliver M3C alignment/workflow/read-only views/invalidation — without weakening assertions or expanding scope.

## Tasks

### R0
- Incident isolation: original M2 corpus manifest + frozen-test input amendment + combined-index retention test
- Spec req: INC-1 isolate corpus, preserve assertions; M2 gate
- Files: `fixtures/m2-corpus.json`, `tests/test_m2_extractor.py`, `tests/test_m2_combined_index.py`
- Test: New combined-index test asserts both state machines indexed; amended extractor test selects only manifest-listed corpus
- Expected: `fixtures/m2-corpus.json` lists original repo-a/b/c; `test_m2_extractor` passes on original corpus; combined test green; full M1/M2 rerun green
- Verify: `python -m pytest tests/test_m2_extractor.py tests/test_m2_combined_index.py tests/test_m2_ground_truth.py -v`

### A1
- Repair M3 dev ground truth via independent test role
- Spec req: M3A-DEV (evidence fixtures repo-d review fixes; dev ground-truth repair)
- Files: `fixtures/repo-d/*`, `tests/test_m3_dev_ground_truth.py`
- Test: Repaired dev ground-truth test fills empty event stubs, adds missing client/server + shared-table assertions, fixes vacuous fetch, keeps no-src-imports independence
- Expected: Dev ground-truth test fails before fix on stubs/vacuous fetch, passes after with behavioral assertions
- Verify: `python -m pytest tests/test_m3_dev_ground_truth.py -v`

### A2
- Held-out repo-e + seal + harness + thresholds + resolver + eval/contract tests (frozen before eval)
- Spec req: M3A-EVAL (held-out seal/harness/thresholds; compatibility≀equivalence; eval tests)
- Files: `fixtures/repo-e/*`, `docs/m3_heldout_seal.json`, `src/bo_resolve/*`, `src/bo_eval/*`, `tests/test_m3_heldout_sealed.py`, `tests/test_m3_contract.py`, `tests/test_m3_eval.py`
- Test: Contract test proves compatibility≀equivalence; sealed held-out test enforces seal + evaluator-only answers; eval test checks denominators + thresholds + raw counts/FP
- Expected: repo-e contains required 5 shapes; seal frozen pre-eval; thresholds enforced as specified
- Verify: `python -m pytest tests/test_m3_contract.py tests/test_m3_eval.py tests/test_m3_heldout_sealed.py -v`

### B1
- M3B plumbing + stored prompts + protocol tests, mocked, BLOCKED
- Spec req: M3B-PLUMB (provider-neutral plumbing, validation, budget caps, ledger, stored prompts; NO invocation)
- Files: `src/bo_extract/semantic_adapter.py`, `prompts/m3b/concept-extraction.md`, `prompts/m3b/relation-extraction.md`, `prompts/m3b/citation-grounding.md`, `prompts/m3b/abstention-policy.md`, `tests/test_m3b_protocol.py`
- Test: Mocked protocol test proves validation + caps + ledger + prompt loading with zero egress; semantic demo paths labeled BLOCKED
- Expected: Plumbing loads stored prompts, validates, caps, ledgers without network/paid/fallback
- Verify: `python -m pytest tests/test_m3b_protocol.py -v`

### C1
- Heuristic concepts + links (evidence extraction, no semantics)
- Spec req: M3A-EXT (heuristic evidence extraction)
- Files: `src/bo_extract/heuristic_concepts.py`, `tests/test_m3_dev_ground_truth.py`
- Test: Dev ground-truth heuristic assertions pass on repo-d concepts/links
- Expected: Heuristic extraction yields concepts + links without semantic calls
- Verify: `python -m pytest tests/test_m3_dev_ground_truth.py -v`

### C2
- Alignment + workflow + read-only views + invalidation + verify_m3 + docs
- Spec req: M3C-ALIGN (typed mappings + unresolved segment; mount-only views; review integration; contract-change invalidation)
- Files: `src/bo_ontology/alignment.py`, `src/bo_api/views_m3.py`, `src/bo_api/server.py`, `tests/test_m3_alignment.py`, `scripts/verify_m3.py`, `docs/m3-capability.md`, `docs/verification-m3.md`, `docs/traceability.md`
- Test: Alignment test asserts `insufficient-evidence` + `distinctFrom` + unresolved segment; invalidation test asserts stale flag + history preserved; `verify_m3.py` gates all
- Expected: Views mount-only read-only; semantic demo labeled BLOCKED; docs record capability/traceability
- Verify: `python -m pytest tests/test_m3_alignment.py -v && python scripts/verify_m3.py`

## Next

Await consolidated approval, then dispatch rows for R0/A1/A2/B1/C1/C2 execution with evidence tracking in `m3-consolidated-progress.md` and `m3-consolidated-evidence.json`.

## Dispatch Rows

- R0: Isolate original M2 corpus manifest + frozen-test input amendment + combined-index retention test
- A1: Repair M3 dev ground truth via independent test role
- A2: Held-out repo-e + seal + harness + thresholds + resolver + eval/contract tests (frozen before eval)
- B1: M3B plumbing + stored prompts + protocol tests, mocked, BLOCKED
- C1: Heuristic concepts + links (evidence extraction, no semantics)
- C2: Alignment + workflow + read-only views + invalidation + verify_m3 + docs

## D4 — Final Gates / Final Evidence / Docs Finalize (2026-11-17)

- Status: full gates green; final evidence appended; stale BLOCKED/live/deferred labels refreshed only where factually stale; ledger entry appended.
- Files touched (D4 allowlist): `scripts/verify_m3.py` (placeholder demo/heuristic-table sections completed deterministically), `docs/verification-m3.md` (final results appended; thresholds + prior sections preserved), `docs/m3-capability.md` (held-out BLOCKED labels refreshed — resolved post-fix), `docs/traceability.md` (coverage status refreshed — held-out GREEN post-fix), `docs/plans/m3-consolidated-progress.md` (this ledger entry).
- Full suite: `python3 -m pytest tests/ -q` → **162 passed**, exit `0`.
- verify_m1: OVERALL **PASS**, exit `0`. verify_m2: OVERALL **PASS**, exit `0`. verify_m3: gates **GREEN**, exit `0` (heuristic-table OK; demo-transcript OK post-completion; M3A held-out GREEN post-fix; M3B protocol GREEN mock-only; seal OK; thresholds PRESENT).
- Exposed-set regression note: original exposed repo-e run **8/10 passed** (2 known BLOCKED extractor gaps) → post-fix rerun **10/10 passed** (GREEN); exposed rerun = regression evidence only; fresh-holdout acceptance **PENDING**.
- Pending: fresh-holdout acceptance (PENDING); M3B execution (BLOCKED); post-M3 Java/Spring gate (PENDING).

1. 3e0c80f | 4a67fbcb7b3cc96a6d0d6a42b910710f65aa3a1b2774fbd5133a36541198f9d7 | 9bed06655fe30aa9ea8522c35f01e1c3fefe037ae286c196f9543189f5ce23ea | 9bed06655fe30aa9ea8522c35f01e1c3fefe037ae286c196f9543189f5ce23ea | no distinct staged version exists | NEXT=recovery-search