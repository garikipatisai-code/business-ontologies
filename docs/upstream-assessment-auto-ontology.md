# Upstream Assessment — NVIDIA Auto Ontology (auto-ontology)

Date: 2026-11-17 | Account: console-openrouter | Model: console-openrouter/nvidia/nemotron-3.5-super-vl:free
Scope: docs-only, researcher tables R1/R2/R3 only, public upstream sources, free-only, no implementation code, no new fetching, no code reads, no execution.
Pin: aedaf127fac95c9cb5219b64918f3f8e31959187 (2026-11-17). License: Apache-2.0, ©2024-2026 NVIDIA. M2 baseline 3e0c80f preserved.
Source of truth for this assessment: TABLE R1 (concepts+reconcile+cert), TABLE R2 (compile+invalidation+Ossie), TABLE R3 (surfaces+modules+license+cost) — provided researcher tables only.

## Upstream identity (pin)

- Upstream: NVIDIA Auto Ontology, converter package `ossie_nvidia_auto_ontology` (external, git pin cb4b46b, fidelity unverified-execution).
- Pin: aedaf127fac95c9cb5219b64918f3f8e31959187, dated 2026-11-17T16:54:17Z (#550, 392-file stat; 13.6k stars/1.2k forks/66 issues/59 PRs/1389 commits [API]).
- License: Apache-2.0, per-file SPDX headers; third-party notices in THIRD_PARTY_NOTICES.md + CONTAINER list [LICENSE].
- Runtime: ontology lives in Postgres+pgvector (:5432); 5-service surface (:3000 Next.js+Better Auth, :3001 FastAPI, :3002 ingestion, :3003 MCP, :5432 Postgres+pgvector); OpenAPI 87 ops; MCP explicit read-only allow-list (9 tools).

## Tier legend

| Tier | Meaning |
|------|---------|
| T1 | Directly stated in researcher tables R1/R2/R3 (documented + inspected source cited in table). |
| T2 | Statically inspected per table, single-source, unverified by execution (e.g. compile.py, pipeline.py, reset.py, rules.py, schema.py, schemas.py, model_interchange.py, service.py, term_families.py, modeling.md, architecture.md, publication.md, README, mcp.md, SECURITY.md, env.example, pyproject.toml). |
| T3 | Unanswered / not evidenced by any table (absent, insufficient data, or explicitly "unanswered"). |

## Dimension evaluations

### 1. Identity — what the upstream is (Tier T1/T2)

- DB-metadata ontology: concepts = Concept(term+desc+synonyms), Mapping(column attrs), Relationship(declared+inferred joins+bridge), Metric(reviewed SQL attrs), Lineage(term→source, answer returns SQL); Certification = per-field reviewed flags [documented, README] — T1.
- Ontology materialized in Postgres+pgvector; unusable without its DB/UI/LLM; ingest needs live source-DB conn + EMBED; compile needs REASONING+EMBED+RERANK + SQL sampling; retrieve needs EMBED+SQL; answer needs REASONING ReAct; MCP holds no creds, inherits frontend auth via x-auto-ontology-user-id [mcp.md, architecture.md] — T2.
- Deps pinned pyproject: python ≥3.12<3.14; fastapi, httpx, hvac, langchain-openai 1.3.0-1.3.5, openai==2.44.0, langchain-nvidia-ai-endpoints, langchain-postgres, sqlalchemy/alembic, psycopg, databricks/mysql/trino/pyhive, sqlglot, numpy, cryptography; uv.lock + vendor/wheels [pyproject.toml] — T2.
- Maintenance pin aedaf127fac95c9cb5219b64918f3f8e31959187 (2026-11-17T16:54:17Z #550, 392-file stat) [API] — T1.

### 2. Certification vs audit (Tier T1/T2/T3)

- Certification = per-field certified flags; marks reviewed in UI/search; does NOT change retrieval [architecture.md + publication.md] — T1.
- Explicitly NOT atomic review/promotion/supersession/rollback; no immutable audit-history endpoint [architecture.md + publication.md] — T1.
- Import pitfalls #250/#265/#267 documented [architecture.md + publication.md] — T1.
- Audit = wall-clock/user + certified flags only [schemas.py+model_interchange.py+schema.py] — T2.
- NO commit-SHA/file-path/line-range in interchange schemas [schemas.py+model_interchange.py+schema.py] — T2.
- Immutable audit log or atomic promotion: NOT evidenced — unanswered [R1] — T3.
- Secret-redaction behavior: insufficient data [R3] — T3.

### 3. Edit preservation (Tier T1/T2)

- Human edits re-embedded on save and kept; next compile only new tables [README, compile.py] — T1/T2.
- EXCEPTION: default semantic-FK join re-selected every compile [compile.py] — T2.
- Full reset = destructive async rebuild [README] — T1.
- Compile selects only tables without a term; runs at startup/24h/on-demand [pipeline.py] — T2.

### 4. Invalidation + deletion (Tier T1/T2/T3)

- Stale handling narrow: column__foreign_key.last_seen stamped; unseen deleted; compilation_history only succeeded/failed (no aborted) [schema.py] — T2 (single-source, unverified).
- NO generic stale+reason+history for rules/transitions/events found [schema.py] — T2/T3.
- delete_semantic_layer/delete_data_layer/delete_all_data: cascade + orphan sql_query sweep; pgvector outside txn [reset.py] — T2.
- Scoped reset: all-or-nothing per-DB; does NOT reach PqlAnalysis/TextAttribute/Analysis [reset.py] — T2.
- replace=true deletes in-scope semantics absent from payload matching imported_id OR id [reset.py] — T2.
- delete_rule cascades rule__tag + tag_target.rule_id, realigns PII, deletes orphan tags; keep_tags reattributes to author; rule search/tags immutable rename-only [rules.py] — T2 (unverified).
- Stale-with-reason+history for rules/transitions/events: not found — unanswered [R2] — T3.

### 5. Lineage vs provenance (Tier T1/T2/T3)

- Lineage = DB/schema/table/column text-UUIDs + SQL text; term→source, answer returns SQL [R1, schema.py] — T1/T2.
- Native interchange: data_layer + semantic_layer(terms/represents/columns_attributes/semantic_fks/sql_attributes/custom_analyses) + zones=[] hardcoded [model_interchange.py] — T2.
- Interchange carries NO rules/guards/exceptions/transitions/event-construct/conflicts [model_interchange.py] — T2.
- Code provenance (commit-SHA/file-path/line-range): absent from interchange — unanswered [R2] — T3.
- Revision-pinned code provenance: unanswered [R2] — T3.

### 6. Ossie fidelity (Tier T2/T3)

- Ossie round-trip documented-lossy [service.py] — T2:
  - Multi-table terms projected to single table; rest dropped with warning.
  - Duplicate custom-analysis names rejected.
  - Joins only in-scope.
  - sql_column_is best-effort, silent-empty when absent.
  - is_nullable NULL → true.
- zones=[] hardcoded (no round-trip) [model_interchange.py] — T2.
- Converter external (ossie_nvidia_auto_ontology @ cb4b46b); fidelity unverified-execution [R2] — T3.
- Guards/exceptions/transitions/publication/conflicts through Ossie: none appear in fetched sources — unanswered [R2] — T3.

### 7. Boundaries / coupling (Tier T1/T2)

- Coupling chain: fe→be→ing→pg; be+ing→source DBs/model endpoints/optional Vault/Kumo [architecture.md] — T2.
- Key units: ingestion_service/ingest.py; semantic/visit_enter + compile + semantic_fk; retrieval/text_to_sql/react + entity_coverage [architecture.md] — T2.
- Cost drivers (no $): 4 model roles REASONING/NON_REASONING/EMBED/RERANK; per-table sampling ≤1000 rows + distinct scans + FK verification queries, 3 tables at once, MAX_INFLIGHT 6; compile at startup/24h/on-demand; answer ReAct ≤24 calls/10 SQL/8000 chars/100 rows; KumoRFM caps [env.example] — T2.
- Security: :3001/:3002 private-network only; backend trusts x-auto-ontology-user-id from frontend; ingestion no own auth; secrets env + optional Vault; MCP per-call OAuth; vulns via NVIDIA PSIRT [SECURITY.md] — T2.
- CLI: claimed as tagline only; no standalone entrypoint verified [tree, low/unverified] — T3.
- Standalone CLI flags: insufficient data — unanswered [R3] — T3.

## Exact reusable modules — finding: none reusable without heavy deps

Finding: the upstream is a coupled 5-service stack whose ontology lives in Postgres+pgvector; no module extracts standalone. Reuse requires the full dep set below. No covering tests are cited in any table (test pass-rates not published equivalent; mcp.md/architecture.md list no test targets).

| Module @ pin aedaf127fac95c9cb5219b64918f3f8e31959187 | Covering tests | Deps | Required adaptations for reuse |
|---|---|---|---|
| ingestion_service/ingest.py | none cited | psycopg, live source-DB conn, EMBED model endpoint, optional Vault | decouple from live source DB + EMBED; replace with M2 deterministic code extractor |
| semantic/visit_enter + compile + semantic_fk | none cited | langchain-openai/openai/langchain-postgres, REASONING+EMBED+RERANK, sqlglot, SQL sampling ≤1000 rows | replace LLM FK/desc/terms with deterministic M2 rules; remove pgvector dependency |
| retrieval/text_to_sql/react + entity_coverage | none cited | EMBED + SQL + REASONING ReAct (≤24 calls/10 SQL/8000 chars/100 rows) | replace ReAct text-to-SQL with M2 construction path |
| ossie converter (external pkg @ cb4b46b) | none cited | ossie_nvidia_auto_ontology git pin cb4b46b | lossy for our constructs (multi-table→single table, zones dropped) — not reusable losslessly |
| model_interchange.py (data_layer + semantic_layer + zones=[]) | none cited | sqlalchemy/alembic | interchange lacks rules/guards/exceptions/transitions/conflicts and commit-SHA provenance — schema extension needed |

## vs current implementation (M2)

- M2 deterministic code ontologies (commit 3e0c80f preserved) vs upstream DB-metadata ontologies derived from tables/columns/views/PKs/FKs + query history + LLM/EMBED. Mismatch: code-derived terms/lineage vs DB-schema-derived terms; our constructs (rules, guards, transitions, events, publication, conflicts) have no upstream equivalent [R1/R2 vs M2 baseline].
- Stale+reason+history (ours, generic across rules/transitions/events) vs upstream narrow last_seen/succeeded-failed (column__foreign_key.last_seen stamped/unseen deleted; compilation_history succeeded/failed only; no generic stale+reason+history found) [schema.py vs M2].
- Construction ≠ publication (ours) vs upstream term-certification (per-field certified flags, reviewed in UI/search, no retrieval change, explicitly not atomic promotion/supersession/rollback, no immutable audit-history endpoint) [architecture.md + publication.md vs M2].
- File+line+SHA provenance (ours) vs upstream UUID+wall-clock lineage (DB/schema/table/column text-UUIDs + SQL text; audit wall-clock/user + certified flags; NO commit-SHA/file-path/line-range in interchange schemas) [schemas.py+model_interchange.py+schema.py vs M2].

## vs prior assessments (this repo)

- CGR (code-graph-rag @ a4132ce7de7222f8de88d41a41f2ab80c87dfe8e): structural code-graph evidence; complementary to upstream's DB-metadata ontology. CGR supplies deterministic provenance manifest (commit, dirty, analyzer version, codec sha256, per-artifact sha256) + verify/diff-index — upstream lacks commit-SHA provenance entirely. Comparison of structural vs DB-metadata evidence is unverified (no joint execution).
- UA (Understand-Anything @ b861852dda79273bd9d2e01c3160d42e2df510d2): domain→flow→step prompt pattern vs upstream term families (OLD_/NEW_-style prefix families via mirrored FK/SQL-join edges, twin Jaccard 0.8/0.5, min evidence 3, expansion stays in prefix). Pattern-vs-family mapping is unverified (different domains: code vs DB schema).
- KAG (OpenSPG @ fdab15b3929d2ee40dfcdd388f90233096a6afc9): multi-hop logical-form reasoning vs upstream ReAct text-to-SQL (≤24 calls/10 SQL/8000 chars/100 rows). Reasoning-style comparison is unverified (no execution; KAG stale push 2026-01-28 >30d).
- All three vs-prior-assessment comparisons marked unverified: no shared execution environment, no joint test data, no pass-rate data published.

## Cost drivers (no $)

- 4 model roles: REASONING / NON_REASONING / EMBED / RERANK.
- Per-table SQL sample ≤1000 rows + distinct scans + FK verification queries; 3 tables at once; MAX_INFLIGHT 6.
- Compile at startup / 24h / on-demand.
- Answer ReAct: ≤24 calls / 10 SQL / 8000 chars / 100 rows; KumoRFM caps [env.example].
- pgvector indexes outside transaction on reset/delete [reset.py].

## Verdict

**reference-only** — do not reuse, adapt, or adopt. Justification: DB-metadata vs code-derived ontology mismatch; heavyweight 5-service + 4-model-role stack; no code-provenance fields (commit-SHA/file-path/line-range absent from interchange); lossy Ossie round-trip for our constructs (multi-table terms → single table, zones dropped, rules/guards/exceptions/transitions/conflicts not carried); no reusable module without the full dep set. Evidence contradicting this (e.g. demonstrated lossless interchange round-trip of M2 rule constructs, or published commit-SHA provenance) would change the verdict.

## Stop-duplicating list

Do not rebuild upstream capabilities we already have or that misfit M2:
- Per-field certification flags with UI/search review marking (M2 construction≠publication already covers reviewed-state semantics).
- Generic stale+reason+history for rules/transitions/events (M2 already has it; upstream lacks it).
- Commit-SHA/file-path/line-range provenance (M2 has it; upstream interchange lacks it).
- Atomic review/promotion/supersession/rollback with immutable audit-history endpoint (out of scope for both; upstream explicitly does not have it).
- Lossy Ossie interchange round-trip for multi-table terms/zones/rules/guards/exceptions/transitions/conflicts.

## Smallest validation experiment — NOT executed

Export one M2 rule to the upstream interchange schema offline by hand: map M2 rule fields into data_layer + semantic_layer terms/represents/columns_attributes/semantic_fks/sql_attributes/custom_analyses (zones=[] hardcoded), note dropped fields (rules/guards/exceptions/transitions/conflicts/commit-SHA provenance), and confirm field loss. NOT run — no execution per task boundary; results unknown.

## Limitations

- Distilled from researcher tables R1/R2/R3 only; no local upstream checkout, no public fetch, no code reads, no execution.
- Table citations are single-source where marked inspected-unverified; fidelity of ossie converter and service.py behaviors unverified by execution.
- No private/proprietary source used; free public upstream only; pin aedaf127fac95c9cb5219b64918f3f8e31959187.
