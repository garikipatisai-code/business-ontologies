# Threat Model — Milestone 0

Date: 2026-11-17 | Account: console-openrouter | Scope: docs-only M0 assessment

## Assumptions

- No implementation code exists in M0; this threat model governs the foundation decision and the business-ontology layer to be built on it.
- Public upstream only (Understand-Anything, code-graph-rag, KAG); no private/proprietary source used.
- Free-only model routing (console-openrouter/nvidia/nemotron-3.5-super-vl:free).

## Threats and mitigations

| # | Threat | Asset | Mitigation (M0) |
|---|--------|-------|-----------------|
| T1 | Untrusted repo content analyzed by CGR/UA (malicious Tree-sitter grammar, compiler frontend, or cgr trace overlay) | Analyzer integrity, host | No exec of analyzed code/tests/hooks by default; pinned dependency a4132ce7; sandboxed analysis; treat analyzed repos as untrusted input. |
| T2 | Secret leakage into graph/provenance (API keys, tokens embedded in source committed to graph) | Secrets | Secret redaction in provenance artifacts (manifest.json, knowledge-graph.json, meta.json equivalents); scan before index; never commit secrets to graph store. |
| T3 | SSRF via remote URLs (graph ingestion fetching attacker-controlled URLs) | Network, host | Egress disabled until explicitly approved; no remote URL ingestion in M0; allowlist any future egress. |
| T4 | Prompt injection in comments/docstrings (UA domain→flow→step pattern adapted with LLM) | LLM-derived ontology | Treat LLM output as untrusted; sanitize before persisting to ontology; no tool-calling from comment-derived prompts; human-in-the-loop for business-ontology assertions. |
| T5 | Path traversal / symlink escape during repo indexing | Host filesystem | Reject absolute paths and symlink escapes; confine indexing to declared workspace roots; per-checkout lock (CGR) limits blast radius. |
| T6 | Egress to unapproved endpoints (graph store, LLM provider, package index) | Network | Egress disabled until approved; pip install from approved index only; embedded SQLite default (no Docker/Neo4j/Memgraph unless approved). |
| T7 | Free-model routing for proprietary source without authorization | Proprietary IP | No proprietary source routed to free model; authorization gate before any non-public source is analyzed; M0 uses public upstream only. |

## Out of scope for M0

- Runtime authN/authZ for multi-user access.
- Graph-store encryption at rest (embedded SQLite default; opt-in Docker stores deferred).
- Audit logging of graph queries.

## References

- ADR 0001 (code-graph-rag foundation)
- ADR 0002 (dependency, not fork)
- docs/upstream-assessment.md
