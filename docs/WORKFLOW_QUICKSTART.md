# Workflow Quickstart

Minimal end-to-end run of the unified multi-repo workflow (`bo_workflow`):
ingest local repos, review a claim, sync source changes, invalidate a root,
and render a human-readable report.

## Prerequisites

- Python `>=3.12` (verified with `python3 --version` → `3.14.4`)
- Stdlib only: no extra installs, no network, no remote fetches.
- Text scan only: Java sources are read as text, so no JDK/compiler is needed.
- Commands work from any directory when `--manifest`/`--db` are absolute
  paths (verified from `/tmp` and from the checkout root); relative manifest
  `path` entries resolve against the manifest file's directory, not the cwd.
- `git` is needed only for the optional clean-checkout method below
  (`git archive HEAD`); the pipeline itself never shells out to `git`.

### Clean checkout (optional)

```bash
mkdir -p /tmp/clean-checkout && git archive HEAD | tar -x -C /tmp/clean-checkout
```

Expected: a clean tree at `/tmp/clean-checkout` with no `.git` directory;
run the ingest/sync/report commands below against that tree to verify a
pristine checkout behaves identically.

## Manifest

The manifest is JSON only (YAML is rejected). Each `path` must exist locally;
remote `scheme://` (or `git@`) paths are rejected and `..` segments are
rejected before any read. Relative `path` entries resolve against the
manifest file's directory and must stay inside it (absolute paths are
explicit local intent and keep working).

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
writes nothing (`added` is `0`, `invalidated_roots`/`cascaded_stale` are `[]`).

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
`contract_links`, and `reviews`. Incompatible links (`compatible` false)
appear in both `contract_links` and `abstentions` by design (declared-only
candidates), so do not sum those two counts. Every `active`/`stale` entry also carries an
additive nullable `provenance` object (`{repo,file,line,span,...}`) sourced
from stored `supporting`/trigger/description JSON or `evidence[0]` without
parsing display names (`None` when no stored provenance exists); all other
keys keep byte-identical values.

Readable Markdown (deterministic: no timestamps, sections sorted):

```bash
python3 scripts/bo_workflow.py report --db /tmp/wf.db --format md
python3 scripts/bo_workflow.py report --db /tmp/wf.db --format md --out /tmp/report.md
```

Expected: exit code `0`; the six sections `Summary`, `Active Claims`
(grouped by kind), `Stale Claims` (with trigger reasons), `Cross-Repo
Contract Links`, `Abstentions & Gaps`, and `Human Reviews` render on stdout;
with `--out`, the same bytes are also written to the file. `Active`/`Stale`
tables carry an explicit `Provenance` column rendering
`<repo>:<file>:L<start>-L<end>` where stored, or
`source provenance unavailable` when no stored provenance exists
(`[REDACTED]` is preserved verbatim); names still show `repo:key` for params
and target for transitions.

Python API:

```bash
python3 -c "from bo_workflow.orchestrator import report, report_markdown; \
    print(report_markdown('/tmp/wf.db'))"
```

Expected: `report()` returns the JSON dict (additive-only: existing keys keep
their values; `active`/`stale` entries gain a nullable `provenance` key);
`report_markdown()` returns the same data as a Markdown string with an
explicit `Provenance` column.

## Scope and limits

- Deterministic only: Markdown output has no timestamps and sorts every
  section, so two runs over the same store are byte-identical.
- Contract links are declared-only: `destination_verified` is always false
  and `equivalent` is never true; a compatible link is not delivery or
  destination proof.
- Declared-only enforcement/gaps are abstentions, not `400`-style errors.
- OSS sample-repo flow only: local Java sources plus JSON manifest; not a
  production validation gate.
