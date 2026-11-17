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
