---
spec: docs/architecture-m0.md
status: draft
approved_by: ""
approved_at: ""
files:
  - fixtures/java-repo-a/src/main/java/com/example/refund/RefundService.java
  - fixtures/java-repo-a/src/main/java/com/example/refund/RefundController.java
  - fixtures/java-repo-a/src/main/resources/application.yml
  - fixtures/java-repo-a/src/main/resources/db/migration/V1__refund_schema.sql
  - fixtures/java-repo-a/openapi.yaml
  - fixtures/java-repo-b/src/main/java/com/example/orders/OrderClient.java
  - fixtures/java-repo-b/src/main/java/com/example/orders/OrderService.java
  - fixtures/java-repo-b/src/main/resources/application.yml
  - fixtures/java-repo-b/openapi.yaml
  - src/bo_java/__init__.py
  - src/bo_java/java_adapter.py
  - src/bo_java/spring_sql_bindings.py
  - tests/test_java_ground_truth.py
  - tests/test_java_adapter.py
  - tests/test_java_business.py
  - scripts/verify_java_slice.py
  - docs/java-slice.md
  - docs/m3-capability.md
  - docs/traceability.md
allow:
  - docs/plans/java-slice-progress.md
  - docs/plans/java-slice-evidence.json
---

# Plan: Java slice (J1–J6)

## Goal

Add a Java/Spring/SQL business-knowledge slice on top of the M3 Python-only baseline: two synthetic Java fixtures (refund + orders), an independent ground-truth label set, a Java adapter reusing the code-graph-rag shape, Spring/SQL binding extraction, business-knowledge tests, a verification gate, and docs updates. Implementation NOT approved by this plan (plan only).

```mermaid
flowchart LR
  J1[Java fixtures repo-a + repo-b] --> J2[Independent ground truth]
  J2 --> J3[Java adapter (CGR reuse, spike-gate backend)]
  J3 --> J4[Spring/SQL bindings]
  J4 --> J5[Business knowledge extraction + tests]
  J5 --> J6[Verify script + docs]
```

## Architecture

Reuse `bo_ontology.store` / `review` / `invalidation`, manifest/provenance, and the resolver/cgr_adapter shape. New `bo_java` package: `__init__`, `java_adapter` (index, verify, resolve_definition, resolve_callers, callees, unresolved list), `spring_sql_bindings`. Interfaces consumed: M3 deterministic extractor baseline, SQLite manifest/provenance. Interfaces produced: Java adapter API, Spring/SQL binding records, verify_java_slice gate, Java capability doc updates. No new parser or queue or DB.

## Tech Stack

Python ≥3.12 plus source-only Java 17 fixtures. Maven/JDK only if the fallback backend wins. Existing code-graph-rag pin reused (zero new downloads on primary path). Test commands: `python3 -m pytest -q`, `python3 scripts/verify_java_slice.py`. No commit/push.

## Spec

Authoritative: `docs/architecture-m0.md`, `docs/milestone-plan-m0.md`, `docs/requirements-traceability.md`, `docs/verification-m3.md` (M3 baseline 162 tests at c00c834), `tests/test_m3_ground_truth.py` (independent-label precedent: no `src` imports in ground-truth files). Java slice extends the post-M3 Java/Spring gate named in `docs/m3-capability.md`.

## Global Constraints

Protect baseline c00c834 / 162 tests (no regressions). No NVIDIA integration, no migration, no UI scale push. Epistemic split (configured vs observed) preserved. Honesty labels (Python-only bounded; Java/Spring/SQL explicitly scoped). No edits to M1/M2/M3 graders or frozen seals. `docs/requirements-traceability.md` append-only.

## Lean Constraints

Spike-gate one backend only. No parser written by hand. No new Python deps unless justified and re-approved. New verify script over frozen gate edits (do not modify verify_m1/verify_m2/verify_m3). Reuse-first; minimal new modules (bo_java package only).

## Review Focus

Backend acceptance bar (below). Ground-truth independence (zero `src` imports). Static configured vs observed discipline. Permission completeness (Permission Batch).

## Permission Batch

Live model NOT requested. Build tools only if fallback backend wins. Primary backend A: zero-new-downloads reuse of the code-graph-rag pin. Fallback backend B: Maven `javaparser-core` 3.17.0 + `symbol-solver-core` 3.17.0 (Apache-2.0), no other deps. Boundary: fixtures + one backend + bindings + slice verify + docs, without commit/push.

## Backend Acceptance Bar

- a. Backend resolves definitions, callers, and callees on both Java fixtures.
- b. Unresolved list is explicit and bounded (no silent misses).
- c. Spring/SQL bindings extracted for both repos.
- d. `scripts/verify_java_slice.py` green on the chosen backend.

Fallback rule: if the primary code-graph-rag backend fails the spike (a–d), switch to fallback backend B (Maven javaparser) and re-run; no third backend permitted.

## Task table

| # | Task | Spec req | Files | Test | Expected | Verify |
|---|------|----------|-------|------|----------|--------|
| J1 | Java fixtures (repo-a refund, repo-b orders) | Java slice scope | `fixtures/java-repo-a/...`, `fixtures/java-repo-b/...` | source-only compile check | Two synthetic redistributable Java repos with Spring/SQL/OpenAPI | `python3 scripts/verify_java_slice.py` (fixture stage) |
| J2 | Independent ground truth | ground-truth independence | `tests/test_java_ground_truth.py` | ground truth asserts labels; no `src` imports | Labels frozen, independent of adapter | `python3 -m pytest tests/test_java_ground_truth.py -q` |
| J3 | Java adapter (index/verify/resolve) | backend bar a–b | `src/bo_java/__init__.py`, `src/bo_java/java_adapter.py` | `tests/test_java_adapter.py` | resolve_definition/resolve_callers/callees + unresolved list | `python3 -m pytest tests/test_java_adapter.py -q` |
| J4 | Spring/SQL bindings | backend bar c | `src/bo_java/spring_sql_bindings.py` | `tests/test_java_business.py` | bindings extracted for both repos | `python3 -m pytest tests/test_java_business.py -q` |
| J5 | Business knowledge extraction + tests | business-not-labels | `tests/test_java_business.py` | business tests green | concepts/rules/links with provenance | `python3 -m pytest tests/test_java_business.py -q` |
| J6 | Verify script + docs | verification gate | `scripts/verify_java_slice.py`, `docs/java-slice.md`, `docs/m3-capability.md`, `docs/traceability.md` | full gate green | M1/M2/M3 regression + Java slice gate | `python3 scripts/verify_java_slice.py` |

## Requirement coverage

- [ ] J1 fixtures → J1
- [ ] J2 ground-truth independence → J2
- [ ] J3 backend bar a–b → J3
- [ ] J4 backend bar c → J4
- [ ] J5 business knowledge → J5
- [ ] J6 verification + docs → J6
- [ ] No task edits M1/M2/M3 graders or frozen seals

## Test integrity

New `tests/test_java_*.py` are deliverables verified by `scripts/verify_java_slice.py`; frozen after authoring. If a grader is wrong, stop and escalate — do not weaken tests to pass. Never edit M1/M2/M3 graders.
