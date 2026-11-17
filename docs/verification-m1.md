# M1 Verification

Run from repository root.

## Command

```bash
python -m pytest tests/ -v && python scripts/verify_m1.py
```

`scripts/verify_m1.py` performs:

1. Manifest verify hash-stable 2x (`manifests/m1-manifest.json`)
2. Full pytest: `python -m pytest tests/ -v`
3. Smoke: `python scripts/smoke_m1.py`
4. Redaction grep: SQLite query over provenance shows no raw `sk-test` token
5. Egress guard: AST scan finds no `socket`, `requests`, `urllib`, `subprocess`, `importlib`, or `exec` usage in `src/bo_extract/` and `src/bo_store/`
6. CGR pin check: `requirements.txt` contains `code-graph-rag==0.7.1` and commit `a4132ce7de7222f8de88d41a41f2ab80c87dfe8e`
7. Pass/fail ledger written to stdout and `docs/verification-m1-ledger.json`

## Expected outputs

- Pytest: all tests pass (`19 passed` in the current M1 suite) and exit `0`
- Smoke: prints `OK /rules`, `OK /relations`, `OK /provenance/<id>`, `OK / (html)`, `smoke_m1: PASS`, exit `0`
- Manifest verify: `ok=True`, identical hash on two runs
- Redaction grep: inserted `sk-test-*` value is stored/queried as redacted (no raw `sk-test` in queried excerpt)
- Egress guard: no forbidden names/calls in extractor/store sources
- CGR pin: pinned version and commit hash present in `requirements.txt`
- Ledger: every check `PASS`, overall `PASS`, `python scripts/verify_m1.py` exit `0`

On any failure, the failing check is reported in the ledger/output and `scripts/verify_m1.py` exits non-zero.