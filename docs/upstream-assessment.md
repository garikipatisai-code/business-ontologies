# Upstream Assessment — Milestone 0

Date: 2026-11-17 | Account: console-openrouter | Model: console-openrouter/nvidia/nemotron-3.5-super-vl:free
Scope: docs-only, public upstream sources, free-only, no implementation code.

## Candidates assessed

| # | Upstream | Repo | Pin | Version | License | Copyright |
|---|----------|------|-----|---------|---------|-----------|
| 1 | Understand-Anything (UA) | Egonex-AI/Understand-Anything | b861852dda79273bd9d2e01c3160d42e2df510d2 | v2.9.8 | MIT | ©2026 Yuxiang Lin/Infinite Universe |
| 2 | code-graph-rag (CGR) | vitali87/code-graph-rag | a4132ce7de7222f8de88d41a41f2ab80c87dfe8e | v0.7.1 | MIT | ©2025 Vitali Avagyan |
| 3 | KAG (OpenSPG) | OpenSPG/KAG | fdab15b3929d2ee40dfcdd388f90233096a6afc9 | v0.8.0 | Apache-2.0 | OpenSPG |

## Candidate detail

### Understand-Anything (UA)

- Backbone: Tree-sitter (C#, C++, Go, Java, JS, PHP, Python, Ruby, Rust, Scala, TS; Kotlin/Dart/Swift via WASM) + 12 custom non-code parsers.
- Node/edge types: 21+ node / 35+ edge types. Pre-resolved importMap, fingerprint incremental, post-commit hook.
- Domain analysis: `/understand-domain` → domain → flow → step. Article analyzer, per-file lessons.
- Provenance: per-project `.ua/` (`knowledge-graph.json`, `meta.json`). Freshness: fresh/dirty/stale.
- Multi-project: none central (`resolveUaDir` per projectPath; monorepo via subdirs).
- Interface: 7 agents, MCP stdio (6 read-only tools), viewer 62KB gzip, `127.0.0.1` + token.
- Tests: vitest, no coverage % published.
- Headless: MCP JSON-RPC yes; needs LLM agent runtime per repo.
- Cost profile: token-heavy initial, incremental cheap.
- Maintenance: 93.0k stars / 7.9k forks; active Nov 2026.
- Limitation: no central multi-project store; headless requires per-repo LLM agent runtime.

### code-graph-rag (CGR)

- Backbone: Tree-sitter + opt-in compiler frontends (libclang, go/types, Roslyn, javac, Jedi) + cgr trace overlay.
- Langs: 14 full; Scala/SQL in-dev; 8 langs ast-grep basic (NO CALLS).
- Schema: nodes Project/Package/Folder/File/Module/Class/Function/Method/Interface/Enum/Type + edges CONTAINS/DEFINES/CALLS/REFERENCES/IMPORTS/INHERITS/IMPLEMENTS/OVERRIDES.
- CALLS resolution: exact/overload/heuristic/trace_confirmed/dynamic, line/col/arg_count.
- Multi-project: one shared graph, `--project`/`--workspace` scoping, MCP workspace mode.
- Retrieval: hybrid exact (resolve/definition/callers/callees/Cypher) + vector UniXcoder 768-dim (sqlite-vec default).
- Provenance: protobuf + manifest.json (commit, dirty flag, analyzer version, codec sha256, per-artifact sha256); `verify-index` + `diff-index`.
- Incremental: hash cache + fingerprint; watch 5s/30s; interface_fingerprint; per-checkout lock.
- Extension: add-grammar / YAML ast-grep; MCP read-only; Python SDK.
- Headless: `cgr graph` JSON, no LLM required.
- Deployment: embedded SQLite `~/.cgr/graph.sqlite` default; Memgraph/Neo4j opt-in Docker.
- Deps: Python>=3.12 + cgr-graph-rs native; extras treesitter-full/semantic/ast-grep/memgraph/neo4j.
- Maintenance: 5379 stars / 719 forks / 420 issues; very active Nov 2026.
- Limits: Cypher subset; `--clean` wipes all; no rename (rebuild); ast-grep excluded from call analyses.

### KAG (OpenSPG)

- Paradigm: logical-form reasoning + retrieval on OpenSPG engine + LLMs.
- kg-builder (schema-free + schema-constrained; 6 index managers: Outline/Summary/KnowledgeUnit/AtomicQuery/Chunk/Table) + kg-solver (fused exact/text/numeric/semantic).
- Alignment thresholds 0.8–0.9.
- Index cost per 100k chars (7B): hybrid 4.63M tok / 1425s; lightweight −89% claim unverified.
- Provenance: references via reporter lifecycle; streaming + graph rendering.
- Multi-project: `qa()` over `kb[]` with ProjectClient + namespace.
- Incremental: checkpoints by content hash; ProcessPoolExecutor.
- Extension: Registrable ABCs (Scanner/Reader/Splitter/Extractor/Mapping/Aligner/Sink/Vectorizer/Planner/Retriever/Generator).
- Headless: pip + SolverMain.invoke / BuilderChainRunner.
- Ops: Docker Compose; engine `openspg/openspg@kag`; UI `127.0.0.1:8887`; Neo4j + vector + LLM (openai/ollama/dashscope/vllm); BGE embeddings.
- Tests: pytest tests/unit, no pass-rate published.
- Maintenance: 9175 stars / 722 forks / 174 issues; pushed 2026-01-28 (stale >30d).
- Opinion: justified only for multi-hop logical QA; overkill for code search; weeks integration.

## Comparison table

| Criterion | UA | CGR | KAG |
|---|---|---|---|
| Parser breadth | Tree-sitter 10 + WASM 3 + 12 non-code | Tree-sitter + 5 compiler frontends + cgr trace | OpenSPG engine + LLMs |
| Domain depth out-of-box | domain→flow→step (rich) | structural code graph (none domain-specific) | logical-form reasoning (KG domain) |
| Deterministic provenance manifest | per-project `.ua/` meta.json (partial) | manifest.json (commit, dirty, version, codec sha256, per-artifact sha256) + verify/diff-index | reporter lifecycle references |
| Multi-project central store | No (resolveUaDir per projectPath) | Yes (one shared graph, --project/--workspace) | Yes (ProjectClient + namespace over kb[]) |
| Incremental | fingerprint + post-commit hook | hash cache + fingerprint + watch + per-checkout lock | content-hash checkpoints + ProcessPoolExecutor |
| Extensions | per-file lessons / custom parsers | add-grammar / YAML ast-grep / Python SDK | Registrable ABCs |
| Tests | vitest, no coverage % | not published (implied) | pytest, no pass-rate |
| Headless, no LLM | No (per-repo LLM runtime) | Yes (`cgr graph` JSON) | Yes (SolverMain.invoke / BuilderChainRunner) |
| Deployment | viewer 62KB gzip, 127.0.0.1+token | embedded SQLite default; Memgraph/Neo4j opt-in Docker | Docker Compose; Neo4j + vector + LLM |
| License | MIT | MIT | Apache-2.0 |
| Maintenance (≤30d before 2026-11-17) | Yes (active Nov 2026) | Yes (very active Nov 2026) | No (pushed 2026-01-28, stale >30d) |

## Option-selection matrix (yes/no)

Binary criteria: (1) central multi-project store? (2) deterministic provenance manifest (commit+version+hashes+verify)? (3) embedded zero-Docker default? (4) no heavyweight engine? (5) domain nodes out-of-box? (6) permissive license? (7) maintained ≤30d before 2026-11-17?

| Candidate | C1 | C2 | C3 | C4 | C5 | C6 | C7 |
|---|---|---|---|---|---|---|---|
| UA | N | Partial–N | Y | Y | Y | Y | Y |
| CGR | Y | Y | Y | Y | N | Y | Y |
| KAG | Y | Partial–N | N | N | Partial–N | Partial–N | N |

Pairwise swap check: order-independent (matrix symmetric under column permutation); no criterion swap changes the winner.

## Decision

Pick: **code-graph-rag** as dependency (pip, pinned a4132ce7), smallest defensible foundation.
- Meets structural evidence: multi-repo shared graph, deterministic provenance manifest + verify/diff-index, embedded SQLite zero-Docker default, no heavyweight engine, permissive MIT, actively maintained, headless no-LLM.
- Tradeoff: domain/business-ontology nodes not out-of-box → business-ontology layer built in-house.

Adapt UA pattern (MIT attribution, no fork): domain→flow→step prompt pattern for the business-ontology layer only.

KAG: reference-only. Justified only for multi-hop logical QA; heavy engine + stale push (>30d) + unverified token-cost claim → cost unjustified for code search.

Losers:
- UA: lacks central multi-project store; headless requires per-repo LLM agent runtime.
- KAG: heavyweight engine (Docker/Neo4j/vector/LLM), stale maintenance, token cost unverified.

Fork-vs-dependency: **dependency** on CGR (pip pinned a4132ce7). Adapt UA domain prompt pattern with attribution (no fork). KAG reference-only. No second parser, no queue, no second DB.

## Update strategy

- CGR: re-pin on security advisory or breaking change; review `verify-index`/`diff-index` drift per release.
- UA: monitor domain→flow→step prompt evolution; re-attribute if adapted text changes.
- KAG: reference only; no pin, no integration unless multi-hop logical QA requirement emerges.

## Limitations

- Assessment distilled from public research evidence, not from local upstream checkouts (out of scope).
- Test pass-rates not published for CGR/KAG; UA coverage % not published.
- KAG lightweight token-cost claim (−89%) unverified.
- No private/proprietary source used; free public upstream only.
