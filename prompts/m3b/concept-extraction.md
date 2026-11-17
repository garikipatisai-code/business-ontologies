# Stored Operating Procedure: concept extraction (M3B)

## Mode
MOCK-ONLY / BLOCKED execution. This procedure is executed by the mock-only
semantic adapter (EGRESS_ENABLED=False). No live model invocation, no network
egress, no download, no paid call, no fallback to a live provider. Unauthorized
live mode raises BlockedError.

## Purpose
Extract concepts from evidence text. A concept is a mapping with:
- term: the concept name (non-empty string present in the evidence)
- definition: short definition grounded in the evidence
- scope: the business context the concept applies to

## Evidence-required
Every concept must be traceable to a source span in the input evidence.
No source span => abstain: do not emit the concept.

## Scope
In scope: business rules, roles, entities, limits, and thresholds stated in
the provided evidence. Out of scope: general world knowledge, speculation,
and terms not present in the evidence.

## Abstain rules
- Term not found in the evidence: abstain.
- Definition not supported by the evidence: abstain.
- Ambiguous or contradictory evidence for the same term: abstain.
Report abstentions explicitly; never fabricate concepts.

## Output
Structured output: {"concepts": [...], "relations": []}. Validated with
validate_structured_output() before use; malformed output is rejected
(fail closed). Mock-only: results are synthetic, never from a live model.
