# Stored Operating Procedure: abstention policy (M3B)

## Mode
MOCK-ONLY / BLOCKED execution. This procedure is executed by the mock-only
semantic adapter (EGRESS_ENABLED=False). No live model invocation, no network
egress, no download, no paid call, no fallback to a live provider. Unauthorized
live mode raises BlockedError.

## When to abstain
Abstain (emit no fabricated result; report null explicitly) when:
- evidence is unsupported: no source span backs the result;
- evidence is dynamic: the value may change (e.g., current date, live rate)
  and is not pinned in the stored evidence;
- evidence is conflicting: two supported sources disagree;
- input is out of scope or malformed.

## Null results reported
Null results are reported explicitly: {"abstained": true, "reason": <why>,
"result": null}. Never substitute a guess, default, or fabricated value.

## Downstream
Calling code must treat abstained results as unresolved: no fallback to a live
model, no egress, no silent substitution.

## Output
{"abstained": bool, "reason": str | None, "result": Any | None}.
Mock-only: results are synthetic, never from a live model.
