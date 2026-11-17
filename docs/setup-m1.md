# M1 Setup

Minimal operator setup for the Milestone 1 walking skeleton.

## Prerequisites

- Python `>=3.12`
- `pip`
- Run commands from the repository root: `business-ontologies/`

## Install

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
```

`requirements.txt` pins the CGR dependency used by the M1 adapter:

- `code-graph-rag==0.7.1  # a4132ce7de7222f8de88d41a41f2ab80c87dfe8e`

`pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]` for pytest.

## Test

```bash
python -m pytest tests/ -v
```

Expected: all tests pass (19 tests in the current M1 suite).

## Smoke

```bash
python scripts/smoke_m1.py
```

Expected output includes:

- `OK /rules`
- `OK /relations`
- `OK /provenance/<id>`
- `OK / (html)`
- `smoke_m1: PASS`
- exit code `0`

The smoke script starts a temporary read-only stdlib HTTP server over a temporary SQLite provenance DB, verifies JSON/HTML inspect endpoints, then shuts down.

## Full M1 verification

```bash
python scripts/verify_m1.py
```

See `docs/verification-m1.md` for expected outputs and the pass/fail ledger.