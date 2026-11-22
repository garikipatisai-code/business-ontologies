# ADR 0003 — Auto-ontology upstream assessment: NVIDIA Auto Ontology (reference-only)

Date: 2026-11-17 | Status: Accepted | Account: console-openrouter | Pin: aedaf127fac95c9cb5219b64918f3f8e31959187

## Context

Assess the NVIDIA Auto Ontology upstream (pin aedaf127fac95c9cb5219b64918f3f8e31959187, dated 2026-11-17T16:54:17Z, Apache-2.0, ©2024-2026 NVIDIA) as a potential source for the business-ontology platform. Assessment basis: docs/upstream-assessment-auto-ontology.md, synthesized from researcher tables R1 (concepts+reconcile+cert), R2 (compile+invalidation+Ossie), R3 (surfaces+modules+license+cost) only — docs-only, no new fetching, no code reads, no execution. M2 baseline 3e0c80f preserved; no product changes.

## Decision

Treat the upstream as **reference-only**: do not reuse, adapt, or adopt any of its code, schemas, or interchange format. No migration plan; no fork; no dependency pin beyond the documentation reference at aedaf127fac95c9cb5219b64918f3f8e31959187.

## Rationale

- Ontology mismatch: upstream derives DB-metadata ontologies from tables/columns/views/PKs/FKs + query history + LLM/EMBED into Postgres+pgvector; M2 (3e0c80f) uses deterministic code ontologies. No shared term/lineage model.
- Coupling: ontology unusable without its DB/UI/LLM; 5-service stack (:3000/:3001/:3002/:3003/:5432) with 4 model roles (REASONING/NON_REASONING/EMBED/RERANK); compile needs REASONING+EMBED+RERANK + SQL sampling; answer needs REASONING ReAct. No module extracts standalone (per docs/upstream-assessment-auto-ontology.md module table).
- No code provenance: upstream interchange carries no commit-SHA/file-path/line-range; lineage is DB/schema/table/column text-UUIDs + SQL text; audit is wall-clock/user + certified flags. M2's file+line+SHA provenance has no upstream equivalent.
- Certification ≠ publication: upstream certifies per-field flags (reviewed in UI/search, no retrieval change, explicitly not atomic promotion/supersession/rollback, no immutable audit-history endpoint); M2 keeps construction≠publication with its own stale+reason+history for rules/transitions/events — upstream lacks generic stale+reason+history.
- Lossy interchange: Ossie round-trip projects multi-table terms to a single table (rest dropped w/ warning), rejects duplicate custom-analysis names, joins only in-scope, sql_column_is best-effort silent-empty, is_nullable NULL→true, zones=[] hardcoded; carries no rules/guards/exceptions/transitions/event-construct/conflicts; converter fidelity unverified-execution.

## What would change the verdict

Evidence of any of the following, verified by execution against the pinned source aedaf127fac95c9cb5219b64918f3f8e31959187:
- lossless interchange round-trip of M2 rule constructs (rules/guards/exceptions/transitions/conflicts) through data_layer + semantic_layer;
- commit-SHA/file-path/line-range code provenance in the interchange or lineage schemas;
- a standalone, dependency-light module (no 5-service stack, no 4 model roles) extractable under Apache-2.0;
- generic stale+reason+history for rules/transitions/events matching M2's model.

Until then: reference-only. The smallest validation experiment — export one M2 rule to the upstream interchange schema offline by hand and check field loss — remains NOT executed.

## References

- docs/upstream-assessment-auto-ontology.md (assessment note; tier legend; dimension evaluations; verdict; stop-duplicating list)
- Upstream pin aedaf127fac95c9cb5219b64918f3f8e31959187 (2026-11-17T16:54:17Z #550), Apache-2.0
- M2 baseline 3e0c80f (preserved)
- docs/upstream-assessment.md (prior assessment: UA/CGR/KAG)
- docs/adr/0002-fork-vs-dependency.md

## Related

- ADR 0002: fork vs dependency (code-graph-rag)
- docs/plans/auto-ontology-assessment.md
