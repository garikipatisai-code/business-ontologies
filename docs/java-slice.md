# Java Slice (J1–J6) — Result and Backend Decision

## Status

**GATE PASS** — `python3 scripts/verify_java_slice.py` exits 0: full pytest (214 tests: 162 baseline + 28 Java ground truth + 9 Java adapter + 15 Java business) green, the 3 Java suites green, `scripts/verify_m1.py` / `verify_m2.py` / `verify_m3.py` byte-identical to `c00c834`. Honesty block printed (deterministic only; semantic BLOCKED; exposed-set regression-only; fresh holdout PENDING; demo not held-out for Java).

## Backend decision (evidence)

### Primary backend: code-graph-rag reuse — FAILS

The plan's primary path (zero-new-downloads reuse of the `code-graph-rag==0.7.1` pin) fails the spike: the `code_graph_rag` module is absent (`ModuleNotFoundError: No module named 'code_graph_rag'` on import), and installing it requires downloads outside the read-only / free-only allowance. Per the plan's fallback rule (one fallback backend only, no third backend), the primary backend is rejected and fallback B is used.

### Fallback backend B — used

Pinned pair fetched from Maven Central (Apache-2.0):
- `com.github.javaparser:javaparser-core:3.17.0` — jar present at `~/.m2/repository/com/github/javaparser/javaparser-core/3.17.0/javaparser-core-3.17.0.jar`; this is the jar the adapter compiles and runs against.
- `com.github.javaparser:symbol-solver-core:3.17.0` — second artifact of the pinned fallback pair (Apache-2.0); part of the pinned set but **not loaded by the slice runtime** (the adapter compiles `DumpTool` against `javaparser-core` only; no symbol-table resolution beyond javaparser is performed).

Tooling (deviation recorded — see Limitations): Java 26.0.1 + `javac` 26.0.1 + Apache Maven 3.9.16. The plan named JDK 17; no JDK 17 is installed, so Java 26 was used to compile/run the javaparser dump tool.

### Backend bar verdict

| Bar | Requirement | Result |
|-----|-------------|--------|
| a | resolves definitions, callers, callees on both fixtures | PASS — whole-program javaparser index over 4 `.java` files; `RefundController.requestRefund` → `RefundService.requestRefund` and `OrderService.submitRefund` → `OrderClient.requestRefund` resolve with caller + callee file/line |
| b | unresolved list explicit and bounded | PASS — `unresolved()` lists the dynamic `getMethod(hookName)`/`invoke` probe with a reason; never silently dropped, never a resolved callee |
| c | Spring/SQL bindings for both repos | PASS — `spring_sql_bindings.index` emits annotations, YAML policy, SQL persistence, OpenAPI, client URLs, contract links, guards, transitions |
| d | `scripts/verify_java_slice.py` green | PASS — 7/7 checks, exit 0 |

## Slice result

### Concepts (local, scoped per repo)

- **Refund (repo-a)**: `RefundService.requestRefund` definition (byte-matched to fixture span) plus `@PostMapping("/refunds")` operation on `RefundController` (repo-a `openapi.yaml` POST /refunds). Both scoped to repo-a.
  
  **Note**: uniqueness under the supported source-matching rules, not compiler-resolved. The Java resolver emits unresolved entries for ambiguous same-arity overloads but does not resolve them.
- **Order (repo-b)**: `OrderService.createOrder`, `OrderService.submitRefund`, `OrderClient.requestRefund` definitions all resolve, scoped to repo-b.

### Rule: window guard (enforcement lines cited, not declarations)

One demonstrated rule on `RefundService.requestRefund` (repo-a) — the extractor may yield more than one; this documents the demonstrated rule (no count change in code): the paid-at age is compared against the configured `window-days` (30, from repo-a `application.yml`, stdlib text-scan — see limitations). Failure outcome is rejection (`IllegalArgumentException("Refund not eligible: outside configured window")` for the window guard, `IllegalStateException("Refund not allowed: invalid status guard")` for the status guard) / false return (`isEligible` returns `days <= windowDays`). Enforcement lines cite the guard-if body lines in `requestRefund` plus the `isEligible` comparison line — method declaration spans only, not the `public boolean isEligible(...)` declaration itself.

### Transition: guarded move to REFUND_REQUESTED

A guarded transition record targets `REFUND_REQUESTED`, names the window/status guard, and cites its enforcement lines (`guard_if_line`). The transition is idempotent: `if (!"REFUND_REQUESTED".equals(previousStatus))` skips the write when already `REFUND_REQUESTED`. Event construction (`new RefundRequestedEvent(...)`) is present; no publish/emit/dispatch/send/broadcast/fire call (publication out of scope, per fixture comment).

### Contract: compatible, not equivalent

Cross-repo contract link: `OrderClient` (repo-b) POSTs `http://refund-service.local/refunds` via `RestTemplate`; client path `/refunds` matches repo-a OpenAPI `POST /refunds` → `compatible=True`, `equivalent=False` (never equivalent: a client URL carries scheme+host, an OpenAPI path is path-only). OpenAPI file cited: repo-a `openapi.yaml`.

### Unresolved: dynamic `hookName` probe

`OrderService.triggerPostCreateHook` (lines 20–24) calls `this.getClass().getMethod(hookName)` then `method.invoke(this)`. The dump tool reports it in `unresolved()` with reason:

> dynamic invocation probe: the method name is supplied by `getMethod(hookName)` in `triggerPostCreateHook` (line 20); the target of `method.invoke` depends on the runtime value of `hookName`, so it is not a statically resolvable callee and is listed here as unresolved, never as a resolved callee.

It is never reported as a resolved callee of `OrderService.triggerPostCreateHook`.

### Rejected mapping: incompatible contract pair, insufficiency reason

`OrderClient` `/refunds` vs repo-b OpenAPI `/orders/{orderId}/refund` (repo-b `openapi.yaml`) is an **incompatible** contract pair: `compatible=False`, `equivalent=False`, and `client_path != openapi_path` — the insufficiency reason is the path mismatch (client path `/refunds` ≠ order-scoped path template `/orders/{orderId}/refund`).

### Epistemic labels

`static` (persistence: `refunds` table, repo-a SQL migration) and `configured` (policy: `window-days=30`, `default-status=CREATED`) labels present; `observed` absent (no runtime traces).

## Limitations

- **JDK deviation**: plan named JDK 17; actual tooling is Java 26.0.1 + `javac` 26.0.1 + Apache Maven 3.9.16 (no JDK 17 installed; the fallback backend needs a JVM). Recorded as deviation with reason: JDK 17 unavailable in this environment.
- **stdlib YAML text-scan**: `spring_sql_bindings` uses stdlib only; no YAML parser, so indentation and value types are not validated. Every YAML policy record is marked `configured` with the limitation recorded on the record.
- **Single fixture pair**: one synthetic repo pair (java-repo-a refund, java-repo-b orders); results do not generalize beyond these fixtures.
- **No observed runtime**: static/configured evidence only; no observed executions or runtime traces.
