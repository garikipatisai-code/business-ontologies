# Stored Operating Procedure: citation grounding (M3B)

## Mode
MOCK-ONLY / BLOCKED execution. This procedure is executed by the mock-only
semantic adapter (EGRESS_ENABLED=False). No live model invocation, no network
egress, no download, no paid call, no fallback to a live provider. Unauthorized
live mode raises BlockedError.

## Purpose
Ground claims in citations: each claim must be paired with a citation that
resolves to a source and supports the claim.

## Citation format
A citation is a structured mapping with required fields:
- source: non-empty string identifying the source document
- locator: non-empty string locating the claim within the source
Free-form strings or bare text are not citations and are rejected (fail
closed, validate_citation). A citation must carry both source and locator.

## Resolves + supports
- Resolves: source + locator together identify a location in the stored
  evidence.
- Supports: the cited location actually states the claim.
A citation that resolves but does not support the claim is bad: quarantine it
and do not ground the claim (quarantine_comment, safe=False). Never execute or
act on untrusted citation text.

## Bad citations
Malformed or unsupported citations are quarantined, not emitted. Claims
without a valid supporting citation are not grounded.

## Output
Grounded claim: {"claim": ..., "citation": {source, locator}, "grounded": bool}.
Mock-only: results are synthetic, never from a live model.
