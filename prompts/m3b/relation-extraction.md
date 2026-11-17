# Stored Operating Procedure: relation extraction (M3B)

## Mode
MOCK-ONLY / BLOCKED execution. This procedure is executed by the mock-only
semantic adapter (EGRESS_ENABLED=False). No live model invocation, no network
egress, no download, no paid call, no fallback to a live provider. Unauthorized
live mode raises BlockedError.

## Purpose
Extract typed relations between concepts extracted from evidence.
A relation is a mapping with:
- source: source concept term
- target: target concept term
- type: one of supertype, subtype, part-of, depends-on, related-to
- evidence: source span supporting the relation

## Typed mappings
Relations are typed mappings only. The type must be stated or clearly implied
by the evidence; do not invent types.

## Compatibility is not equivalence
Two concepts being compatible (e.g., co-occurring in the same policy) does not
make them equivalent. Emit an equivalence-type relation only when the evidence
states identity; otherwise use the most specific supported type or abstain.

## Evidence-required
Every relation must cite source spans for both endpoints. Missing endpoint
evidence => insufficient evidence => abstain (no relation emitted).

## Insufficient evidence
- Endpoint concept missing: abstain.
- Relation type not stated or implied by the evidence: abstain.
- Conflicting evidence for the same pair: abstain.

## Output
Structured output: {"concepts": [], "relations": [...]} validated with
validate_structured_output() before use; malformed output is rejected
(fail closed). Mock-only: results are synthetic, never from a live model.
