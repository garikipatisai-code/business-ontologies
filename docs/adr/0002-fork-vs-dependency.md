# ADR 0002 — Fork vs Dependency: code-graph-rag as Dependency

Date: 2026-11-17 | Status: Accepted | Account: console-openrouter

## Context

ADR 0001 selected code-graph-rag (CGR) as the structural foundation. Question: fork CGR into the business-ontologies repo, or consume it as an external dependency (pip, pinned)?

## Decision

Consume CGR as an **external dependency**, pip pinned to commit a4132ce7de7222f8de88d41a41f2ab80c87dfe8e (v0.7.1). Do not fork.

## Rationale

- CGR is actively maintained (very active Nov 2026, ≤30d before 2026-11-17); pinning tracks upstream security/bug fixes via re-pin review.
- MIT license permits dependency use without copyleft obligations.
- Headless `cgr graph` JSON + Python SDK + MCP read-only surface the needed structural evidence without modifying CGR internals.
- Forking would obligate the business-ontologies platform to maintain a parallel Tree-sitter/compiler-frontend codebase and re-pin manually on every upstream change.
- No second parser, no queue, no second DB: CGR supplies the graph; the business-ontology layer sits on top, adapting the Understand-Anything domain→flow→step prompt pattern (MIT attribution, no fork of UA either).

UA domain pattern is adapted as a prompt-pattern reference only (no fork). KAG remains reference-only (no fork, no dependency).

## Consequences

- Positive: minimal maintenance surface; upstream fixes/security arrive via version bump; no fork drift.
- Negative: pinned dependency must be re-pinned on breaking changes or security advisories; CGR internals not directly modifiable (extension via add-grammar / YAML ast-grep / Python SDK instead).

## References

- https://github.com/vitali87/code-graph-rag @ a4132ce7de7222f8de88d41a41f2ab80c87dfe8e (MIT)
- https://github.com/Egonex-AI/Understand-Anything @ b861852dda79273bd9d2e01c3160d42e2df510d2 (MIT, pattern attribution only)

## Related

- ADR 0001: foundation choice
- docs/upstream-assessment.md
- docs/THIRD_PARTY_NOTICES.md
