# M1 Capability Matrix

## Supported (M1)

| Area | Supported in M1 | Notes |
| --- | --- | --- |
| Structural extraction | Python `ast` (stdlib) | Deterministic, read-only parse; analyzed code is never imported/executed |
| Text/config fixtures | SQL, YAML, OpenAPI text fixtures | Present as workspace fixtures/artifacts in the walking skeleton |
| Workspace manifest | Manifest JSON + verification | `bo_store.manifest.load_manifest` / `verify_manifest`; path confinement and traversal/symlink escape checks |
| Provenance | Manifest-shaped provenance in SQLite + verify flow | `bo_store.provenance.init_db`, `record_artifact`, redaction of `sk-test-*` values |
| Retrieval/API | Hybrid exact+vector reference path, read-only HTTP inspect | `bo_api.server` stdlib server: `/rules`, `/relations`, `/provenance/<id>`, HTML inspect; abstains on missing evidence/traversal |
| Cross-repo structural relation | Deterministic `invokes` relation from stdlib AST resolution | Single cross-repo relation in walking skeleton fixtures |
| Verification harness | `scripts/verify_m1.py` + smoke | Manifest verify stability, pytest, smoke, redaction/egress/CGR-pin checks, pass/fail ledger |

## Deferred (post-M1)

| Area | Deferred | Reason |
| --- | --- | --- |
| Live CGR daemon | Yes | M1 uses import-guarded adapter/stub behavior when live CGR runtime is unavailable |
| Java compiler frontends | Yes | M1 structural extractor is Python `ast` only |
| Cypher full | Yes | CGR/Cypher subset limits remain; no full Cypher engine in M1 |
| KAG reasoning | Yes | KAG is reference-only, not an M1 runtime dependency |
| LLM extraction live | Yes | M1 is deterministic/free-only; no live LLM extraction path |
| Multi-user auth | Yes | Read-only local inspect API only; no authN/authZ/multi-user controls |