# Milestone Plan M0

## M0 — Scaffold & progress ledger (current)

### Acceptance criteria
- Docs: architecture-m0.md, requirements-traceability.md, milestone-plan-m0.md complete
- STATUS.md, ROADMAP.md, business-ontologies-progress.md updated with M0 complete
- .gitignore exists excluding upstream-checkouts/, indexed-repos/, node_modules, .env
- All docs present and verified
- M0 approval awaited (no implementation code)

### Risks
- Single maintainer CGR beta: dependency on code-graph-rag v0.7.1 MIT (actively maintained but requires expertise)
- Cypher subset limits: complex queries may need workarounds
- `--clean` wipes all: requires backup strategy documentation
- ast-grep no-calls: CALLS resolution excluded from analysis
- Token cost: LLM-staged extraction cost must be monitored
- Scale ≠ enterprise: vertical slice M1 designed for small-scale deployment

### Budget
- 3 iterations for M0:
  - Iteration 1: scaffold docs + .gitignore
  - Iteration 2: verification run (ls -R docs/plans)
  - Iteration 3: files-touched list + final report
- Deadline: 2026-11-24T00:00:00Z

## M1 — Vertical slice: manifest→2 fixtures→structural→1 rule→1 cross-repo rel→provenance→minimal UI/API

### Acceptance criteria
- CGR workspace import working (verify-index, diff-index)
- Ontology schema validation functional
- Evidence retrieval exact+structural+semantic functional
- Provenance manifest generation + status tracking functional
- Basic UI navigation dept→context→repo→evidence functional
- LLM extraction pipeline functional (staged with validation)
- Cross-repo resolution functional (definition/callers/callees)
- Incremental caching + invalidation functional
- Security controls (T1-T7) functional
- Evaluation harness (A-H) functional

### Risks
- CGR integration complexity: multi-project workspace setup
- LLM validation pipeline overhead: token cost, validation logic
- Ontology schema adoption: alignment with business domains
- Incremental invalidation accuracy: transitive invalidation correctness
- UI complexity: navigation + alignment review + metrics
- Security bypass: edge cases in untrusted content handling
- Performance: query response times with vector+exact hybrid

## M2 — Scale & reliability

### Acceptance criteria
- Enterprise-scale workspace (100+ repos, 1M+ files)
- SLA: >99.9% uptime, <500ms query response
- Multi-user access with authN/authZ
- Graph-store encryption at rest (opt-in Docker stores)
- Audit logging of graph queries
- Backup/restore strategy for `--clean` operations
- Performance monitoring + alerts
- Cost optimization for LLM extraction

### Risks
- Graph store scaling: database choice (SQLite vs Memgraph/Neo4j)
- Multi-user complexity: RBAC, permissions, audit trails
- Encryption overhead: key management, performance impact
- Backup strategy complexity: incremental vs full backups
- Cost explosion: LLM extraction at scale
- Monitoring complexity: metrics collection + alerting

## M3 — Extensions & integrations

### Acceptance criteria
- Plugin architecture for new ontology types
- Integration with version control systems (GitHub, GitLab)
- API for external consumers (MCP extensions)
- Export capabilities (JSON, YAML, GraphQL)
- CI/CD integration (pre-commit hooks)
- Custom rule engine for ontology validation
- Semantic versioning for ontology schemas
- Multi-language support (beyond Python)

### Risks
- Plugin architecture security: code injection risks
- CI/CD integration: pipeline overhead, rate limits
- Version control complexity: webhook handling, deduplication
- Export format complexity: schema validation for each format
- Multi-language support: parser overhead, maintenance burden
- Rule engine performance: rule compilation and execution

## M4 — Advanced features

### Acceptance criteria
- Advanced reasoning capabilities (KAG integration)
- Machine learning model integration (training, inference)
- Real-time evidence updates (continuous monitoring)
- Collaborative editing for ontology development
- Automated quality checks (code smell detection)
- Domain-specific adapters (Java, C#, Rust, etc.)
- Compliance and audit reporting
- Disaster recovery and failover

### Risks
- KAG integration complexity: heavyweight engine coupling
- ML model integration: data privacy, model drift
- Real-time updates: streaming infrastructure complexity
- Collaborative editing: conflict resolution, sync issues
- Quality checks: rule complexity, false positives/negatives
- Domain adapter complexity: parser maintenance across languages
- Compliance reporting: regulatory requirements variation

## M5 — Production & enterprise

### Acceptance criteria
- Enterprise deployment (multi-cluster, HA)
- 24/7 support operations
- Advanced security features (SOC2, ISO27001)
- Performance benchmarking (TPTs, throughput)
- Cost analysis and optimization
- User documentation and training
- SLA agreements with customers
- Exit strategy (data portability)

### Risks
- Enterprise deployment complexity: infrastructure, operations
- Security compliance: audit preparation, incident response
- Performance tuning: optimization, tuning cycles
- Cost management: usage-based pricing, budget controls
- Knowledge transfer: documentation, training quality
- Exit strategy: data extraction, migration complexity
- Market competition: alternative solutions

## Current position

M0 completed (docs scaffold). Awaiting M0 approval. Next milestone: M1 implementation (vertical slice). Implementation deferred pending M0 approval.

## Dependencies

- code-graph-rag (pip pinned a4132ce7de7222f8de88d41a41f2ab80c87dfe8e v0.7.1 MIT)
- Understand-Anything pattern adapted (MIT b861852dda79273bd9d2e01c3160d42e2df510d2)
- KAG reference-only (Apache-2.0 fdab15b3929d2ee40dfcdd388f90233096a6afc9)

## Timeline

- M0 deadline: 2026-11-24T00:00:00Z (completed)
- M1 target: Q1 2027
- M2 target: Q2 2027
- M3 target: Q3 2027
- M4 target: Q4 2027
- M5 target: 2028

## Iteration budget remaining

- M0 iterations: 3/3 complete
- M1 iterations: TBD (pending M0 approval)
- Total iterations: TBD (pending M0 approval)