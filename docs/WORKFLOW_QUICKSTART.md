# Workflow Quickstart

Minimal end-to-end run of the unified multi-repo workflow (`bo_workflow`):
ingest local repos, review a claim, sync source changes, invalidate a root,
and render a human-readable report.

## Prerequisites

- Python `>=3.12`
- Run commands from the repository root: `business-ontologies/`
- Stdlib only: no extra installs, no network, no remote fetches.

## Manifest

The manifest is JSON only (YAML is rejected). Each `path` must exist locally;
remote `scheme://` (or `git@`) paths are rejected and `..` segments are
rejected before any read.

```bash
cat > /tmp/repos.json <<'EOF'
{"repos": [{"name": "Alpha", "path": "/abs/path/to/Alpha"}]}
EOF
```

Expected: `load_manifest` accepts it; missing paths raise `FileNotFoundError`.

## Ingest

```bash
python3 scripts/bo_workflow.py ingest --manifest /tmp/repos.json --db /tmp/wf.db
```

Expected: exit code `0`, JSON summary on stdout with `counts`
(`params`, `rules`, `transitions`, `operations`, `assertions`,
`dependencies`, `contract_links`, `compatible_links`), plus `abstentions`
and `gaps` ledger entries for anything the extractors decline to assert.

## Review

```bash
python3 scripts/bo_workflow.py review --db /tmp/wf.db --subject "Alpha:windowDays" \
    --reviewer qa --reason "looks good"
```

Expected: exit code `0`, JSON `{"id": ..., "review": ...}`; history is
append-only and the reviewed row is untouched.

## Sync

```bash
python3 scripts/bo_workflow.py sync --manifest /tmp/repos.json --db /tmp/wf.db
```

Expected: exit code `0`; changed facts are invalidated with a
`sync: <file>:<span> changed: ...` trigger reason and removed facts with a
`sync: <file> removed: ...` reason, and re-inserted as new
rows (stale rows are preserved); reviews are never touched; a clean re-run
writes nothing.

## Invalidate

```bash
python3 scripts/bo_workflow.py invalidate --db /tmp/wf.db --kind params \
    --id 1 --reason "window 30->45" --new-value 45
```

Expected: exit code `0`, JSON `{"invalidated": [["params", 1], ...]}` listing
the cascade in BFS order; unrelated rows stay untouched. Repeat invalidates
are idempotent (already-stale roots yield no new pairs).

## Report

JSON (default, byte-stable):

```bash
python3 scripts/bo_workflow.py report --db /tmp/wf.db
```

Expected: exit code `0`, JSON with `counts`, `active`, `stale` (each stale
entry carries the last invalidate `trigger` reason), `abstentions`, `gaps`,
`contract_links`, and `reviews`.

Readable Markdown (deterministic: no timestamps, sections sorted):

```bash
python3 scripts/bo_workflow.py report --db /tmp/wf.db --format md
python3 scripts/bo_workflow.py report --db /tmp/wf.db --format md --out /tmp/report.md
```

Expected: exit code `0`; the six sections `Summary`, `Active Claims`
(grouped by kind), `Stale Claims` (with trigger reasons), `Cross-Repo
Contract Links`, `Abstentions & Gaps`, and `Human Reviews` render on stdout;
with `--out`, the same bytes are also written to the file. Rules and
operations carry file/line provenance (`<repo>:<path>:L<line>:...`);
params show `repo:key` and transitions show target.

Python API:

```bash
python3 -c "from bo_workflow.orchestrator import report, report_markdown; \
    print(report_markdown('/tmp/wf.db'))"
```

Expected: `report()` returns the JSON dict; `report_markdown()` returns the
same data as a Markdown string (`report()` itself never changes shape).

## Scope and limits

- Deterministic only: Markdown output has no timestamps and sorts every
  section, so two runs over the same store are byte-identical.
- Contract links are declared-only: `destination_verified` is always false
  and `equivalent` is never true; a compatible link is not delivery or
  destination proof.
- Declared-only enforcement/gaps are abstentions, not `400`-style errors.
- OSS sample-repo flow only: local Java sources plus JSON manifest; not a
  production validation gate.
