# Verification

Run from repository root.

## Command

```bash
python3 -m pytest tests/ -v && python3 scripts/verify_m1.py
```

## Real results (2026-11-17)

- pytest: `30 passed in 3.59s`, exit `0`.
- `scripts/verify_m1.py`: `OVERALL: PASS`, exit `0`.
- Checks: `manifest_verify_hash_stable_2x`, `pytest`, `smoke`, `redaction_grep`, `egress_guard`, `cgr_pin` — all `[PASS]`.
- Manifest hash stable 2x: `9bf51bb49a4e9d348a75b97904246717311391754b81fe8e94f1691646a03854`.
- Smoke: `OK /rules`, `OK /relations`, `OK /provenance/1`, `OK / (html)`, `smoke_m1: PASS`.
- Redaction excerpt: `[REDACTED]`.
- Egress guard: `issues=0`.
- CGR pin: `code-graph-rag==0.7.1  # a4132ce7de7222f8de88d41a41f2ab80c87dfe8e`.

No throughput numbers are claimed; only the measured pytest wall time above is reported.

## M2-T1 Baseline (2026-11-17)

Baseline reconciliation + security/review debt closure for M2 Task T1 (deep deterministic ground truth). Append-only: prior M1 result rows above are preserved as history.

- Re-run command: `python3 -m pytest -q && python3 scripts/verify_m1.py` from repository root.
- pytest rerun: `30 passed in 3.09s` (wall `4.04s`), exit `0`.
- `scripts/verify_m1.py` rerun: `OVERALL: PASS`, exit `0` (wall `5.04s`).
- Checks: `manifest_verify_hash_stable_2x`, `pytest`, `smoke`, `redaction_grep`, `egress_guard`, `cgr_pin` — all `[PASS]`.
- Manifest hash stable 2x: `9bf51bb49a4e9d348a75b97904246717311391754b81fe8e94f1691646a03854`.
- Smoke: `OK /rules`, `OK /relations`, `OK /provenance/1`, `OK / (html)`, `smoke_m1: PASS`.
- Redaction excerpt: `[REDACTED]`.
- Egress guard: `issues=0`.
- CGR pin: `code-graph-rag==0.7.1  # a4132ce7de7222f8de88d41a41f2ab80c87dfe8e`.
- Commit: `d7c3d8c52a7a506297c6405bfc0548fa665455cb` (M1 walking skeleton baseline).
- CGR adapter runtime state: `cgr is None` (import-guarded optional dependency absent) → stub-only; `CgrAdapter.available = False`. The `code-graph-rag==0.7.1 #a4132ce7de7222f8de88d41a41f2ab80c87dfe8e` requirement is a pin, not a live integration (live CGR runtime deferred, debt D1).
- Call edge: cross-repo `invokes` edges are implementation evidence only (deterministic origin, source+target file/line); not published ontology assertions (C5).

Baseline reconciled under M2 T1: M1 baseline re-confirmed at 30/30 PASS; no source changes required in this task.
