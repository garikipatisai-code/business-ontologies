# Requirements Traceability Milestone-0

## Sections 5-14 mapped to design elements

### 5 Ingestion
Requirement: Robust ingestion of source code with structural evidence and provenance tracking
Design element: CGR adapter with index/verify/diff (repo → `.cgr` workspace) using embedded SQLite and unified workspace
Future tests: Unit tests for cgr workspace import flow, verify-index integrity, incremental watch mechanism, per-project scoping

### 6 Evidence
Requirement: Evidence retrieval from ingested content with exact+structural+semantic capabilities
Design element: CGR embedded SQLite with vector UniXcoder 768-dim (sqlite-vec default), retrieval hybrid exact+vector, abstain capability
Future tests: Unit tests for graph query exact match, structural query (Cypher subset), semantic search (vector similarity), abstain behavior

### 7 Ontology
Requirement: Business ontology schema for knowledge representation
Design element: In-house ontology schema (Domain/Context/Capability/Concept/Actor/Process/Step/Operation/Rule/Policy/State/Transition/Event/Contract/Representation/Store/Team + 35+ relations)
Future tests: Unit tests for ontology validation, semantic search, relation traversal, ontology consistency checks

### 8 Provenance
Requirement: Comprehensive provenance tracking with deterministic origin and epistemic status
Design element: Manifest (repo, commit SHA, file+range, content hash, extractor/version, run+timestamp, origin deterministic/model-inferred/human-reviewed, status proposed/accepted/rejected/stale/disputed, confidence with basis)
Future tests: Unit tests for manifest integrity, status transitions, origin tracking, confidence scoring, provenance validation

### 9 Extraction
Requirement: Business ontology extraction from source code using LLM with validation
Design element: LLM-staged extraction with typed validation + bounded retry + audit trail (no hidden chain), sanitization before persistence, human-in-loop for assertions
Future tests: Unit tests for LLM validation pipeline, extraction retry logic, audit trail completeness, sanitization effectiveness, human-in-loop workflow

### 10 Resolution
Requirement: Cross-repo resolution of ontology concepts and relationships
Design element: CGR adapter cross-repo resolve (definition/callers/callees/Cypher subset), unified workspace, bidirectional, exact+vector hybrid
Future tests: Unit tests for cross-repo relation resolution, definition/callees discovery, Cypher subset queries, multi-project scoping

### 11 Incremental
Requirement: Incremental updates and invalidation handling
Design element: Hash cache + fingerprint + watch + per-checkout lock; incremental content/version caching + derivation tracking + transitive invalidation
Future tests: Unit tests for incremental indexing, invalidation propagation, cache consistency, watch mechanism, per-checkout lock functionality

### 12 Retrieval/API/MCP/UI
Requirement: Evidence retrieval with user interface and API
Design element: Retrieval exact+structural+semantic + abstain; UI nav dept→context→repo→evidence + alignment review + coverage/freshness/cost; MCP read-only tools
Future tests: Unit tests for retrieval API endpoints, UI navigation flow, coverage/freshness/cost metrics, alignment review process, MCP tool integration

### 13 Security
Requirement: Security controls for untrusted content and access
Design element: Untrusted content handling (no exec by default), secret redaction in provenance, SSRF/egress disabled, traversal/symlink guards, permission-gated retrieval
Future tests: Unit tests for security controls (T1-T7), injection prevention, access control, secret redaction, path traversal prevention, egress control

### 14 Evaluation
Requirement: Evaluation framework for deterministic, cross-repo, semantic, alignment, evidence validity, answer quality, invalidation, access-control/injection
Design element: Deterministic behavior, cross-repo support, semantic mocked + opt-in live evaluations, alignment (human-in-loop), evidence validity tracking, answer quality scoring, invalidation handling, access control/injection tests
Future tests: Unit tests for evaluation harness (A-H), deterministic verification, cross-repo evaluation, semantic mocking, alignment scoring, evidence validity checks, answer quality metrics, invalidation tests, security tests

## Implementation status

M1 walking skeleton implements and tests the deterministic scope of Sections 5-14 (30 passed; `verify_m1.py` OVERALL PASS). Live-model and scale paths remain deferred.

| Section | Status | Notes |
|---------|--------|-------|
| 5 Ingestion | implemented+tested | Deterministic CGR workspace import + manifest verify |
| 6 Evidence | implemented+tested | Deterministic exact+structural evidence; abstain on missing |
| 7 Ontology | implemented+tested | Structural nodes/edges/rules shape validated |
| 8 Provenance | implemented+tested | SQLite + manifest; 10 fields + id; redaction |
| 9 Extraction | implemented+tested | Deterministic AST extraction; live LLM path deferred |
| 10 Resolution | implemented+tested | Cross-repo `invokes` relation with file+line both ends |
| 11 Incremental | implemented+tested | Deterministic cache/manifest stability; watch deferred |
| 12 Retrieval/API/MCP/UI | implemented+tested | Read-only GET API/UI on 127.0.0.1; 404 abstain; MCP deferred |
| 13 Security | implemented+tested | T1-T7 controls; HIGHs closed, residual LOWs in debt ledger |
| 14 Evaluation | implemented+tested | Deterministic evaluation; live-model and scale deferred |

## Future test mapping summary

- **Implemented**: Sections 5-14 deterministic design elements (M1 walking skeleton)
- **Tested**: Deterministic tests pass (30 passed; `verify_m1.py` OVERALL PASS)
- **Live-evaluated**: Deferred (M1 is deterministic only)
- **Scale-tested**: Deferred (enterprise-scale testing post-M1)
- **Deferred**: Live LLM extraction, live semantic/alignment evaluation, scale tests