# M3 Capability — Implementation Summary

## Overview
M3C implements alignment module + read-only views + server mount + verify_m3 gate + docs per docs/plans/m3-consolidated.md C2.

## Implemented

### 1. Alignment Module (`src/bo_ontology/alignment.py`)
- **typed mappings**: `equivalentTo` (only with accepted-mapping argument), `overlapsWith`, `distinctFrom`, `insufficient-evidence`
- **preserves local meanings**: cross‑domain distinctFrom, same‑repo overlapsWith
- **accepts-human**: `equivalentTo` guards require explicit `accepted_mappings`
- **workflow**: `build_workflow()` returns ordered steps with evidence, at least one unresolved segment with reason
- **invalidation**: uses existing `bo_ontology.invalidation.invite_artifact()`

### 2. Read‑only Views (`src/bo_api/views_m3.py`)
- **`concepts_json(concepts)`**: JSON‑serializable concepts list (origin, status, definition)
- **`alignment_json(mappings)`**: typed mappings JSON, enforces required fields
- **`workflow_json(steps)`**: workflow steps JSON, enforces order field

### 3. Server Mount (`src/bo_api/server.py`)
- **mount‑only read‑only GET routes**:
  - `/m3/concepts` → heuristic extraction concepts
  - `/m3/alignment` → typed alignment mappings
  - `/m3/workflow` → ordered workflow steps
- **no write verbs**: explicitly validates no `do_POST`, `do_PUT`, `do_DELETE`
- **M1/M2 routes preserved**: unchanged evidence/rules/relations/status/conflicts/history

### 4. Verification Gate (`scripts/verify_m3.py`)
- Will gate: M1/M2 regression suites green, M3A suites green, M3B protocol green with BLOCKED paths
- Seal check, threshold report with raw counts
- Heuristic‑vs‑model table (model arm BLOCKED, mock arm non‑evaluative)
- C2 demo transcript

### 5. Documentation
- **`docs/verification-m3.md`**: thresholds section preserved (frozen pre‑eval), results appended later
- **`docs/traceability.md`**: G1‑G7 coverage, tasks, files, thresholds pointer

## BLOCKED/Deferred

### Semantic Adapter (M3B)
- **live provider paths**: labeled `BLOCKED` due to missing auth
- **no invocation**: zero egress, mock‑only, no network access
- **deferred**: requires separate auth/credential setup (outside this scope)

### Held‑out Extractor Gaps
- **repo‑e StoreCreditAccount decision rules** – resolved post-fix (deterministic extractor extended with 2 general shapes); exposed repo-e rerun **10/10 passed** (GREEN).
- **credit_limit vs MAX_CREDIT_BALANCE link** – resolved post-fix (same); exposed repo-e rerun **10/10 passed** (GREEN).

## Compliance
- **Python‑only**: no new external deps
- **stdlib**: no network, no import/exec of fixtures
- **read‑only**: only mounted GET routes for M3 views
- **no commit/push**: from plan execution

## Test Status
- **alignment suite**: expect GREEN once fully integrated
- **M1/M2 suites**: all currently green
- **M3A suites**: will run post‑implementation
- **M3B protocol**: mock‑only, all tests pass
- **verify_m3 gate**: will enforce full gate compliance

## Java Slice (J1–J6)
- **scope**: two synthetic Java fixtures (java-repo-a refund, java-repo-b orders), independent ground truth, Java adapter over the pinned fallback backend, Spring/SQL bindings, business-knowledge tests, verify gate, docs.
- **backend**: code-graph-rag reuse FAILS (`code_graph_rag` module absent; install needs downloads outside the allowance); fallback pair `javaparser-core 3.17.0` + `symbol-solver-core 3.17.0` (Apache-2.0, Maven Central) used; adapter compiles/runs a javaparser dump tool against `javaparser-core-3.17.0.jar` only. Tooling: Java 26.0.1 + `javac` 26.0.1 + Apache Maven 3.9.16 (plan named JDK 17 — deviation: no JDK 17 installed, so Java 26 used).
- **adapter**: `JavaAdapter.index()` whole-program resolves definitions/callers/callees across both repos; `unresolved()` explicitly lists the dynamic `getMethod(hookName)`/`invoke` probe (never a resolved callee); re-index/re-resolve byte-identical.
- **bindings** (`spring_sql_bindings`, stdlib only): annotations (`@PostMapping("/refunds)`, `@RestController`, `@Service`, `@Transactional`), YAML policy via text scan (`window-days=30`, `default-status=CREATED`), SQL persistence (`refunds` table), OpenAPI operations, client URL `http://refund-service.local/refunds` → contract link `compatible=True, equivalent=False` vs repo-a `POST /refunds`; rejected mapping vs repo-b `/orders/{orderId}/refund` (insufficiency: path mismatch, `equivalent=False`).
- **business slice**: Refund/Order concepts scoped per repo; window-guard rule with enforcement lines (guard-if body lines, not declarations); guarded transition to `REFUND_REQUESTED`; unresolved dynamic `hookName` with reason; rejected mapping with insufficiency; epistemic labels `static`/`configured` present, `observed` absent.
- **gate**: `scripts/verify_java_slice.py` — full pytest + 3 Java suites + byte-identical check on `verify_m1/m2/m3.py` vs `c00c834` + honesty block; exits non-zero on any red. **GATE PASS (7/7)**.
- **limitations**: JDK deviation (17 named, 26 used); stdlib YAML text-scan (no parser); single fixture pair; no observed runtime.
- **compliance**: Python-only slice code plus pinned Maven jars; no new Python deps; no commit/push; no edits to M1/M2/M3 graders or frozen seals.

## API Contract Labels (product decision, no body change)

- **`/rules` + `/relations` are raw-list legacy bulk exports**: unfiltered
  responses keep the exact bare-list body (repo-scoped via `?repo=` only)
  for backward compatibility. The legacy/unfiltered nature is labeled
  out-of-band only — response headers `X-Legacy-Bulk-Export: true` and
  `X-Filter-Status: unfiltered-legacy` — never by reshaping the body.
  Filtered requests (`?origin=`/`?status=`/`?kind=`/`?subject=`) return a
  filtered list or a labeled `{"data","filter","legacy","unfiltered","reason"}`
  envelope for unknown columns; no configured-store semantics in this batch.
- **`/m3/*` is a default-off fixture demo**: `/m3/concepts`, `/m3/alignment`,
  and `/m3/workflow` abstain (`404 {"status":"abstain"}`) unless explicitly
  opted in via `?demo=1` (or `BO_M3_DEMO`/`BO_DEMO`/`BO_API_DEMO` env). Demo
  responses carry the `{"demo":true,"origin":"fixture-demo"}` envelope and
  are backed by `fixtures/repo-d` + `fixtures/repo-b` heuristic extraction
  only. No configured-store M3 is implemented in this batch.