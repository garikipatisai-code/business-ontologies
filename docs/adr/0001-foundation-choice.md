# ADR 0001 — Foundation Choice: code-graph-rag

Date: 2026-11-17 | Status: Accepted | Account: console-openrouter

## Context

Milestone 0 needs a verifiable structural foundation for the business-ontologies platform: a multi-repo code graph with deterministic provenance, incremental updates, headless operation, and minimal ops. Three upstream candidates were assessed in docs/upstream-assessment.md:

- Understand-Anything (Egonex-AI/Understand-Anything), pin b861852dda79273bd9d2e01c3160d42e2df510d2, v2.9.8, MIT.
- code-graph-rag (vitali87/code-graph-rag), pin a4132ce7de7222f8de88d41a41f2ab80c87dfe8e, v0.7.1, MIT.
- KAG (OpenSPG/KAG), pin fdab15b3929d2ee40dfcdd388f90233096a6afc9, v0.8.0, Apache-2.0 (reference only).

## Decision

Adopt **code-graph-rag (CGR)** as the structural foundation, consumed as a **dependency** (pip, pinned to commit a4132ce7de7222f8de88d41a41f2ab80c87dfe8e, v0.7.1).

## Rationale

CGR satisfies the structural evidence criteria:

1. Central multi-project store: one shared graph with `--project`/`--workspace` scoping and MCP workspace mode. (UA lacks this; resolveUaDir is per projectPath.)
2. Deterministic provenance manifest: manifest.json records commit, dirty flag, analyzer version, codec sha256, per-artifact sha256; `verify-index` + `diff-index` supported.
3. Embedded zero-Docker default: SQLite `~/.cgr/graph.sqlite`; Memgraph/Neo4j opt-in Docker only.
4. No heavyweight engine: Tree-sitter backbone + opt-in compiler frontends; headless `cgr graph` JSON requires no LLM.
5. Permissive license: MIT ©2025 Vitali Avagyan.
6. Maintained: very active Nov 2026 (≤30d before 2026-11-17).

Tradeoff accepted: CGR has no domain/business-ontology nodes out-of-box; the business-ontology layer is built in-house, adapting the Understand-Anything domain→flow→step pattern (MIT attribution, no fork). KAG is reference-only: justified only for multi-hop logical QA, heavy engine (Docker Compose/Neo4j/vector/LLM), stale push 2026-01-28 (>30d), unverified token-cost claim → cost unjustified for code search.

## Consequences

- Positive: verifiable structural evidence, incremental hash cache + fingerprint + per-checkout lock, hybrid exact + vector retrieval, Python SDK, headless no-LLM, minimal ops.
- Negative: business-ontology layer (domain nodes, flows, steps) must be built in-house; Cypher subset only; ast-grep languages excluded from call analyses; `--clean` wipes all graph data.

## References

- https://github.com/vitali87/code-graph-rag @ a4132ce7de7222f8de88d41a41f2ab80c87dfe8e (MIT)
- https://github.com/Egonex-AI/Understand-Anything @ b861852dda79273bd9d2e01c3160d42e2df510d2 (MIT, pattern attribution only)
- https://github.com/OpenSPG/KAG @ fdab15b3929d2ee40dfcdd388f90233096a6afc9 (Apache-2.0, reference only)

## Related

- ADR 0002: fork-vs-dependency
- docs/upstream-assessment.md
- docs/THIRD_PARTY_NOTICES.md
