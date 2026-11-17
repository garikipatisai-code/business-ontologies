# M1 Walking Skeleton Progress

## Goal

Implement vertical slice of business ontologies platform: manifest→2 fixtures→structural→1 rule→1 cross-repo relation→provenance→minimal UI/API using code-graph-rag as foundation with deterministic provenance and structural evidence.

## Acceptance criteria

- CGR workspace import working (verify-index, diff-index)
- Ontology schema validation functional
- Evidence retrieval exact+structural+semantic functional
- Provenance manifest generation + status tracking functional
- Basic UI navigation dept→context→repo→evidence functional
- LLM extraction pipeline functional (staged with validation)
- Cross-repo resolution functional (definition/callers/callees)
- Incremental caching + invalidation functional
- Security controls (T1-T7) functional
- Evaluation harness (A-H) functional

## Budget

3 iterations for M1:
- Iteration 1: CGR workspace setup + manifest generation + 2 fixtures structural evidence
- Iteration 2: Business logic rule implementation + cross-repo relation resolution + provenance tracking
- Iteration 3: Integration testing + security controls + evaluation harness + final verification

## UTC deadline

2026-11-24T00:ற்றி:00Z

## Task list

| Task | Status |
|------|--------|
| 1. Set up CGR workspace with manifest generation | complete |
| 2. Implement 2 fixtures for structural evidence | complete |
| 3. Create single rule for business logic | complete |
| 4. Establish cross-repo relation | complete |
| 5. Implement provenance tracking | complete |
| Fix round (reviewer + security findings) | complete |

## Per-task evidence

- Task 1: CGR workspace import on pinned `code-graph-rag==0.7.1`; manifest `manifests/m1-manifest.json` verify hash-stable 2x (`9bf51bb4…`)
- Task 2: 2 fixtures ingested; deterministic AST structural nodes/edges; `tests/test_extractor.py` PASS
- Task 3: exactly one deterministic rule `refund_window_days=30` with evidence; `test_exactly_one_refund_window_rule_deterministic_with_evidence` PASS
- Task 4: cross-repo `invokes` relation `OrderService.createOrder` → `RefundPolicy.applyWindow` with file+line both ends; persisted with provenance; `tests/test_cross_repo.py` PASS
- Task 5: provenance SQLite + manifest, 10 fields + id; secret redaction `[REDACTED]`; `tests/test_manifest.py` PASS
- Fix round: pytest 30 passed (was 19); `verify_m1.py` OVERALL PASS; code-reviewer re-review no HIGHs blocking; security re-audit F1/F2/F3 HIGHs closed, residual LOWs only
- Verification: `python3 -m pytest tests/ -v && python3 scripts/verify_m1.py` exit 0; 30 passed in 3.59s; hash stable x2; smoke PASS; redaction `[REDACTED]`; egress 0; pin `code-graph-rag==0.7.1  # a4132ce7de7222f8de88d41a41f2ab80c87dfe8e`
- seq2 (researcher, UA): BRAIN session-model, high, on, complete — UA assessed, MIT b861852d, lacks central store, per-repo LLM headless
- seq3 (researcher, CGR): BRAIN session-model, high, on, complete — CGR assessed, MIT a4132ce7, meets all structural criteria, selected
- seq4 (researcher, KAG): BRAIN session-model, high, on, complete — KAG assessed, Apache-2.0 fdab15b3, reference only, heavy/stale
- seq5 (implementer): console-openrouter/nvidia/nemotron-3.5-super-vl:free, console-openrouter, medium, on→complete — docs written + verified
- seq6 (implementer): console-openrouter/nvidia/north-mini-code:free, console-openrouter, medium, on→complete — architecture + requirements + plan docs written + verified

## Key decisions

- Foundation: code-graph-rag dependency (pip pinned a4132ce7de7222f8de88d41a41f2ab80c87dfe8e), deterministic fallback hermetic, no second parser/queue/DB
- Business ontology layer: in-house schema built on Understand-Anything domain→flow→step pattern (MIT attribution, no fork)
- Integration: CGR headless no-LLM, embedded SQLite default, workspace mode for multi-project isolation
- Security: untrusted content handling (no exec by default), secret redaction, SSRF/egress disabled, traversal/symlink guards, permission-gated retrieval

## Next step

M1 walking skeleton complete (Tasks 1-5 + fix round). Next: M2 planning pending approval.

## Dispatch ledger

| seq | role | model | account | effort | thinking | status |
|-----|------|-------|---------|--------|----------|--------|
| 1 | implementer | console-openrouter/nvidia/nemotron-3.5-super-vl:free | console-openrouter | medium | off | complete |
| 2 | researcher | BRAIN session-model | BRAIN | high | on | complete |
| 3 | researcher | BRAIN session-model | BRAIN | high | on | complete |
| 4 | researcher | BRAIN session-model | BRAIN | high | on | complete |
| 5 | implementer | console-openrouter/nvidia/nemotron-3.5-super-vl:free | console-openrouter | medium | on | complete |
| 6 | implementer | console-openrouter/nvidia/north-mini-code:free | console-openrouter | medium | on | complete |
| 7 | researcher | BRAIN session-model | BRAIN | high | on | complete |
| 8 | implementer | console-openrouter/nvidia/north-mini-code:free | console-openrouter | medium | on | complete |
| 9 | implementer | opencode/longcat-2.5-free | opencode | exempt | off | complete |