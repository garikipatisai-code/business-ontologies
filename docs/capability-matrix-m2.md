# M2 Capability Matrix

M2 implements the deep-ontology milestone with deterministic rule/transition/event-construct/review/invalidation, semantic adapter mock-only, NOT EVALUATED (live semantic quality), and deferred (live CGR daemon, Java frontends, full Cypher, multi-user auth, hundreds-repo orchestration, storage migration, YAML/SQL extractors).

## Implemented (Deterministic)

| Category | Status | Description |
|----------|--------|-------------|
| Rules | ✅ | Deep ground truth per-category (independent), boundary logic, prereqs, override, window 29/30/31 |
| Transitions | ✅ | Guard/source/target declaration with file+line evidence, deterministic origin |
| Events | ✅ | Construction present, publication abstains, declaration separate from construction |
| Constants | ✅ | Misleading comments inert, similar-name constants excluded, same-symbol exclusion |
| Review | ✅ | Human-in-loop status transitions (construction≠publication, C2) |
| Invalidation | ✅ | Append-only debt-first invalidation flow, no silent carry after value change |
| Provenance | ✅ | Strict origin/status enums, secret redaction in provenance |
| Security | ✅ | Loopback-only binding, SSRF/egress disabled, traversal/symlink guards |
| Extraction | ✅ | Deterministic AST extraction, mock-only semantic adapter |
| Storage | ✅ | Plain SQL migration under PRAGMA user_version, CRUD operations |

## Mocked (Semantic Adapter)

| Component | Status | Description |
|-----------|--------|-------------|
| Semantic Adapter | 🔶 | Mock-only protocol, no live-model semantic evaluation (C3) |
| Semantic Mock Tests | ✅ | egress disabled, config selects mock, untrusted comments quarantined, citation validation, prompt hash recording, bounded retries |

## Not Evaluated (Live Quality)

| Component | Status | Description |
|-----------|--------|-------------|
| Live CGR Daemon | 🔶 | Runtime stub-only (cgr is None), pin `code-graph-rag==0.7.1` is not integrated |
| Java Frontends | 🔶 | Deferred to post-M2 |
| Full Cypher | 🔶 | Subset limited (no rename, no second parser) |
| Multi-user Auth | 🔶 | Read-only local inspect API only |
| Hundreds-repo Orchestration | 🔶 | Single workspace focus |
| Storage Migration | 🔶 | Deferred to post-M2 |
| YAML/SQL Extractors | 🔶 | Deterministic free-only path |

## Deferred (Post-M2)

- **D1**: Live CGR daemon integration
- **D5**: LLM extraction live (beyond deterministic/free-only)
- **D6**: Multi-user auth full implementation
- **D7**: Scale testing (hundreds of repos)
- **D8**: Storage migration (embedded SQLite to distributed)
- **D9**: YAML/SQL extractors

## Verification Results (T7)

All verification checks pass:

| Check | Status | Details |
|-------|--------|---------|
| Full pytest | ✅ PASS | 98 tests passed (30 M1 + 68 M2) |
| M1 verifier | ✅ PASS | All 6 checks pass (manifest hash, smoke, redaction, egress guard, CGR pin) |
| Semantic adapter mock | ✅ PASS | 6 mock tests pass |
| Confinement/redaction | ✅ PASS | Redaction excerpt: `[REDACTED]` |
| Per-category evaluation | ✅ PASS | 15/15 category tests correct |
| Citation validity | ✅ PASS | Format and support validation |
| M1 end-to-end | ✅ PASS | Verified via M1 verifier |

**OVERALL: PASS**

## Ledger Reference

Ledger: `docs/verification-m2-ledger.json`

Generated: 2026-11-17
Task: M2 T7

## Compliance

- **C1**: Debt-first append-only honored
- **C2**: Construction≠publication enforced
- **C3**: Mock-only NOT EVALUATED (semantic quality)
- **C4**: Independent ground truth per-category
- **C5**: Call edge stays evidence only
- **C6**: Scope equals approved allowlist (deterministic.py, bo_ontology schema/store/review/invalidation/migration, migrations/0001_init.sql, semantic_adapter.py, server.py, bo_review CLI + verify_m2.py, fixtures, tests, docs)

## M2 Status Summary

**IMPLEMENTED (Deterministic):** 10/10 categories complete
  - Rules, Transitions, Events, Constants, Review, Invalidation, Provenance, Security, Extraction, Storage
  
**MOCKED (Semantic Adapter):** 1/2 components
  - Semantic Adapter: 🔶 (mock-only)
  - Semantic Mock Tests: ✅ (6/6 tests pass)
  
**NOT EVALUATED (Live Quality):** 7/7 components
  - Live CGR Daemon, Java Frontends, Full Cypher, Multi-user Auth, Hundreds-repo Orchestration, Storage Migration, YAML/SQL Extractors
  
**DEFERRED (Post-M2):** 6/6 items
  - D1-D6

## Demo Transcript Reference

M2 close demonstration completed:

### 1. Deterministic Extraction Demo
```bash
python3 -c "import sys; sys.path.insert(0, 'src'); from bo_extract.deterministic import extract; n,e,r=extract('fixtures'); print('rule statement, transition guard/target, event construction present + publication abstain'); print('rules:', len(r)); print('events:', len(e)); print('transitions:', len(n)); print('Sample rule name:', r[0]['name'] if r else 'None')"
```
**Output**: `3 rules, 34 events, 40 transitions`, sample rule `refund_window_days`

### 2. Business Ontology Review CLI Demo
```bash
python3 scripts/bo_review.py propose --subject refund_window_days --reviewer demo --reason demo
python3 scripts/bo_review.py accept --subject refund_window_days --reviewer demo --reason "demo acceptance"
python3 scripts/bo_review.py history --subject refund_window_days
```
**Output**: Review created → Review accepted → Complete history with 3 entries (proposed → accepted)

### 3. Invalidation Demo
```python
from bo_ontology.invalidation import invalidate_artifact
modified_rule = {**original_rule, 'value': 999}
invalidated = invalidate_artifact(modified_rule, 'Demonstration: value changed')
```
**Output**: Original status preserved → Invalidated status = `stale` → Superseded = `True` → Original value = `30` preserved, new value = `999`

All demos completed successfully with full history preservation.

## Notes

- Human approval recorded (2026-11-17, approved_by human-garikipatisai-code-2026-11-17)
- Machine/TUI approval NOT claimed
- Deterministic origin only (free-only model routing)
- No secrets, no network egress
- Read-only analysis, no exec/import of untrusted code
- Secret redaction in provenance
- Egress disabled (SSRF protection)
- Local-first deployment
- **M2 COMPLETE** - ready for M3 planning