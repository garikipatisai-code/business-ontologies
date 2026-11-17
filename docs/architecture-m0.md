# Architecture Milestone-0

## Goal

A modular monolith + workers providing structural evidence for business ontologies with version provenance and epistemic status tracking. Core foundation: code-graph-rag dependency (pip pinned a4132ce7de7222f8de88d41a41f2ab80c87dfe8e v0.7.1 MIT) for deterministic, headless, embedded SQLite-backed knowledge graphs. In-house versioned business ontology implements Understand-Anything domain→flow→step pattern (MIT b861852dda79273bd9d2e01c3160d42e2df510d2, adapted no fork). LLM-staged extraction with typed validation and audit trail. Cross-repo resolution via CGR unified workspace. Evidence-aware answers retrieve exact+structural+semantic from graph manifest. Vertical slice M1: manifest→2 fixtures→structural→1 rule→1 cross-repo rel→provenance→minimal UI/API.

## Non-goals

- No microservices (monolithic design)
- No second parser/queue/DB (single CGR instance)
- No giant force-graph UI (department→context→repo→evidence nav)
- No exhaustive program understanding (structural evidence only)
- No auto-exec of analyzed code (headless, no execution)
- No per-repo LLM runtime (central store via CGR)
- No heavyweight engine like KAG (reference-only)
- No giant force-graph UI (proof-of-concept graph viewer only)

## Alternatives

### Pure UA fork
Rejected: Understand-Anything lacks central multi-project store; headless requires per-repo LLM agent runtime; violates single-source foundation requirement.

### KAG-based engine
Rejected: Heavyweight engine requiring Docker/Neo4j/vector/LLM stack; stale maintenance (>30d); token cost unverified; opinionated only for multi-hop logical QA; overkill for code search.

### Do-nothing
Rejected: Misses departmental need for structural evidence, deterministic provenance, and cross-repo business ontology resolution; strategy would stall platform development.

## Trade-offs

Buys: Centralized multi-project store via CGR, deterministic provenance manifest, embedded SQLite zero-Docker default, headless no-LLM, permissive MIT license, actively maintained, structural evidence for ontologies. Gives up: Domain-specific nodes not out-of-box (requires in-house ontology layer), CALLS exclusion from AST-grep, limited Cypher subset, no rename in graph (rebuild required), no per-file lessons from UA.

## Trace

| Requirement (user sec) | Design element | Test future |
|-----------------------|----------------|-------------|}}
| 5 Ingestion | CGR adapter index/verify/diff (repo → .cgr workspace) | unit: cgr workspace import flow |
| 6 Evidence | CGR embedded SQLite with vector UniXcoder, retrieval exact+structural+semantic | unit: graph query exact/structural/semantic |
| 7 Ontology | In-house schema: Domain/Context/Capability/Concept/Actor/Process/Step/Operation/Rule/Policy/State/Transition/Event/Contract/Representation/Store/Team + relations | unit: ontology validation + semantic search |
| 8 Provenance | Manifest (repo, commit SHA, file+range, content hash, extractor/version, run+timestamp, origin deterministic/model-inferred/human-reviewed, status proposed/accepted/rejected/stale/disputed, confidence with basis) | unit: manifest integrity + status transitions |
| 9 Extraction | LLM-staged extraction with typed validation + bounded retry + audit trail (no hidden chain) | unit: LLM validation pipeline |
| 10 Resolution | CGR adapter cross-repo resolve (definition/callers/callees/Cypher subset), unified workspace, bidirectional, exact+vector hybrid | unit: cross-repo relation resolution |
| 11 Incremental | Hash cache + fingerprint + watch + per-checkout lock; incremental content/version caching + derivation tracking + transitive invalidation | unit: incremental indexing + invalidation |
| 12 Retrieval/API/MCP/UI | Retrieval exact+structural+semantic + abstain; UI nav dept→context→repo→evidence + alignment review + coverage/freshness/cost; MCP read-only tools | unit: retrieval API + UI navigation |
| 13 Security | Untrusted content analysis (no exec by default), redaction, SSRF/egress disabled, traversal/symlink guards, permission-gated retrieval | unit: security tests (T1-T7) |
| 14 Evaluation | Deterministic (same input → same graph), cross-repo (multi-project), semantic mocked + opt-in live, alignment (human-in-loop), evidence validity, answer quality, invalidation, access-control/injection | unit: evaluation harness (A-H) |

## Review findings

### Failure-modes perspective
Potential points of failure: untrusted repo content (T1) compromising analyzer integrity; secret leakage in provenance; SSRF via future egress; prompt injection in LLM-staged extraction; path traversal in repo indexing; egress to unapproved endpoints; free-model routing for unauthorized proprietary source. Mitigations: no exec by default, secret redaction, egress disabled, LLM output sanitization, path confinement, egress whitelist, authorization gate.

### Operability perspective
Operational considerations: embedded SQLite default ensures local-first deployment; headless no-LLM reduces infrastructure; incremental watch enables efficient updates; CGR subset limits query complexity; `--clean` wipes all necessitates backup strategy; ast-grep excluded from call analyses reduces security surface. Mitigations: backup strategy for `--clean`, clear documentation on Cypher subset limits, audit trail for LLM extractions, health checks for graph integrity.

### Merged dispositions
Architecture builds on proven foundation (CGR) with bounded scope (structural evidence only). Security focuses on defense-in-depth (no exec, redaction, egress control). Operability prioritizes simplicity (embedded SQLite, headless, incremental). Trace links each requirement to design with explicit test future. Review findings cover both failure modes (security) and operability (maintenance). Documentation provides clear guidance for implementation.

## Leading indicators

Issues caught pre-impl: trace table maps requirements to design; review findings identify failure modes; threat-model validates security. Coverage: vertical slice M1 defines minimal viable product. Traced/total: requirements linked to design + tests; evidence manifest tracked; ontology schema defined.

## Replaceable interfaces

A: CGR adapter: index/verify/diff/resolve/definition/callers/callees/Cypher subset, extract via unified workspace, embed SQLite with vector UniXcoder, retrieval exact+structural+semantic, incremental cache invalidation.
B: Ontology schema: Domain/Context/Capability/Concept/Actor/Process/Step/Operation/Rule/Policy/State/Transition/Event/Contract/Representation/Store/Team + relations implements/enforces/verifies/represents/owns/participates/depends/publishes/consumes/invokes/precedes/conditionallyLeadsTo/compensates/transitionsFrom/To/guardedBy/equivalentTo/specializes/overlapsWith/distinctFrom/contradicts/supersedes.
C: Provenance format: manifest (repo, commit SHA, file+range, content hash, extractor/version, run+timestamp, origin deterministic/model-inferred/human-reviewed, status proposed/accepted/rejected/stale/disputed, confidence with basis).
D: LLM extraction pipeline: staged extraction with typed validation + bounded retry + audit trail (no hidden chain), sanitization before ontology persistence, human-in-loop for business ontology assertions.
E: Provider adapter: token-free deterministic mode (console-openrouter/nvidia/nemotron-3.5-super-vl:free), no-credentials deterministic mode, provider switchable via config, fallback validation.
F: Caching layer: incremental content/version caching + derivation tracking + transitive invalidation, hash cache + fingerprint + watch, per-checkout lock, cache health checks.
G: Retrieval layer: exact+structural+semantic + abstain, hybrid UniXcoder vector + exact match, UI nav dept→context→repo→evidence, alignment review (coverage/freshness/cost), MCP read-only tools.
H: Security layer: untrusted content handling (no exec by default), secret redaction in provenance, SSRF/egress disabled, traversal/symlink guards, permission-gated retrieval, audit logging of graph queries (deferred to M2+).

## Stack

- Python ≥3.12 (CGR native)
- embedded SQLite default
- local-first
- pinned deps
- reproducible setup
- Language priority: Java/Spring, SQL, YAML/properties, OpenAPI, then TS/JS

## Implementation plan

Vertical slice M1: manifest→2 fixtures→structural→1 rule→1 cross-repo rel→provenance→minimal UI/API.

## Notes

- Full implementation deferred to M1+ (beyond M0 scope)
- CGR subset limits (Cypher, no rename, --clean wipes all)
- AST-grep excluded from call analyses (security surface reduction)
- Business ontology layer built in-house, not out-of-box from CGR/UA