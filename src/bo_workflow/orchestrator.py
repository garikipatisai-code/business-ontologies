"""Unified multi-repo workflow orchestrator (stdlib only).

Ingests several local Java repos through the existing extractors/linkers
(``bo_java.spring_sql_bindings``) into one ``OntologyStore`` database, then
supports review / invalidate-cascade / report over the unified store.

Manifest contract (JSON only -- YAML is not supported with stdlib only)::

    {"repos": [{"name": "Alpha", "path": "/abs/or/manifest/relative/dir"}]}

* ``path`` must exist locally as a directory; remote ``scheme://`` (or
  ``git@``) paths are rejected -- no remote fetches are ever attempted.
* Relative paths resolve against the manifest file's directory and
  must stay inside it; ``..`` segments are rejected before any read
  (absolute paths are explicit local intent and keep working).

Persistence mapping (minimal glue, no extractor/store rewrites):

* YAML policy values -> ``PolicyParameter`` (``name`` is ``"<repo>:<key>"``;
  provenance JSON lives in ``description`` so ``value`` stays the raw value).
* Enforced guards -> ``Rule`` (condition in ``value``/``predicate``;
  provenance JSON in ``supporting``; file/line span in ``evidence``).
* Guarded status writes -> ``Transition`` (``source`` None/unknown;
  provenance JSON inside ``trigger``; file/line span in ``evidence``).
* Mapping routes -> ``Operation`` (method+path JSON in ``description``;
  file/line span in ``evidence``; unlinkable routes kept with a reason).
* Abstentions (unlinkable routes, compatible-False contract links,
  non-full operation bindings), gaps (inheritance gaps, declared-only
  enforcement) and every cross-repo contract link (declared-only:
  ``destination_verified`` False, ``equivalent`` never True) ->
  ``Assertion`` rows with predicate ``abstention`` / ``gap`` /
  ``contract_link``.

Explicit dependency edges (``add_dependency``; never name guessing):

* same-file guard evidence: ``rules -> transitions`` when the transition's
  ``guard_if_line`` cites the enforced rule's line (prerequisites included);
* condition reference: ``params -> rules`` when the rule condition text
  contains the YAML key as a whole token, with both file:line citations.

Report partitions assertions out of the active/stale claim lists and shows
for every stale claim the last invalidate trigger reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bo_java import spring_sql_bindings as ssb
from bo_ontology import review as review_mod
from bo_ontology import schema
from bo_ontology.store import _ABSENT, OntologyStore

WORKFLOW_VERSION = "bo_workflow/1.0.0"

ABSTENTION = "abstention"
GAP = "gap"
CONTRACT_LINK = "contract_link"

_CLAIM_KINDS = (
    "concepts",
    "operations",
    "params",
    "rules",
    "states",
    "transitions",
    "events",
)

_REMOTE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


# ---------------------------------------------------------------- manifest


def load_manifest(manifest_path: str | Path) -> list[dict[str, str]]:
    """Load and validate a JSON manifest; return ``[{name, path}]``.

    Raises ``FileNotFoundError`` for a missing manifest or repo directory and
    ``ValueError`` for malformed JSON, missing keys, duplicate names, remote
    paths, or non-JSON (YAML) manifests.
    """
    mpath = Path(manifest_path)
    if mpath.suffix.lower() in {".yaml", ".yml"}:
        raise ValueError(
            f"manifest must be JSON (.json); YAML is not supported "
            f"(stdlib only): {mpath}"
        )
    if not mpath.is_file():
        raise FileNotFoundError(f"manifest not found: {mpath}")
    try:
        data = json.loads(mpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"malformed manifest {mpath}: invalid JSON ({exc})"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        raise ValueError(
            f"malformed manifest {mpath}: top-level object with a "
            f"'repos' list is required"
        )
    entries = data["repos"]
    if not entries:
        raise ValueError(f"malformed manifest {mpath}: 'repos' is empty")
    repos: list[dict[str, str]] = []
    seen: set[str] = set()
    for pos, entry in enumerate(entries):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not entry.get("name").strip()
            or not isinstance(entry.get("path"), str)
            or not entry.get("path").strip()
        ):
            raise ValueError(
                f"malformed manifest {mpath}: repos[{pos}] needs "
                f"'name' and 'path' strings"
            )
        name = entry["name"].strip()
        raw = entry["path"].strip()
        if name in seen:
            raise ValueError(
                f"malformed manifest {mpath}: duplicate repo name {name!r}"
            )
        seen.add(name)
        if _REMOTE.match(raw) or raw.startswith("git@"):
            raise ValueError(
                f"manifest repos[{pos}] {name!r}: remote fetches are "
                f"unsupported; 'path' must exist locally"
            )
        raw_path = Path(raw)
        if ".." in raw_path.parts:
            raise ValueError(
                f"malformed manifest {mpath}: repos[{pos}] {name!r}: "
                f"'path' must not contain '..' (confined to the "
                f"manifest directory): {raw}"
            )
        if raw_path.is_absolute():
            # Absolute paths are explicit local intent (per the manifest
            # contract above); traversal segments are still rejected.
            candidate = raw_path
        else:
            candidate = (mpath.parent / raw_path).resolve()
            try:
                candidate.relative_to(mpath.parent.resolve())
            except ValueError:
                raise ValueError(
                    f"malformed manifest {mpath}: repos[{pos}] {name!r}: "
                    f"'path' escapes the manifest directory: {raw}"
                ) from None
        if not candidate.is_dir():
            raise FileNotFoundError(
                f"manifest repos[{pos}] {name!r}: repo path not found: {raw}"
            )
        repos.append({"name": name, "path": str(candidate)})
    return repos


# ---------------------------------------------------------------- provenance


def _provenance(
    repo_name: str, record: dict[str, Any], extractor: str
) -> dict[str, Any]:
    """Provenance dict reusing the extractor ``_base`` fields."""
    span = record.get("span") or {}
    line = int(record.get("line", 1))
    return {
        "repo": repo_name,
        "file": str(record.get("file", "")),
        "line": line,
        "span": {
            "start_line": int(span.get("start_line", line)),
            "end_line": int(span.get("end_line", line)),
        },
        "content_hash": str(record.get("content_hash", "")),
        "extractor": extractor,
        "extractor_version": str(
            record.get("extractor_version", ssb.EXTRACTOR_VERSION)
        ),
        "origin": "deterministic",
    }


def _evidence(provenance: dict[str, Any]) -> list[schema.Evidence]:
    """File/line span evidence for a persisted claim."""
    return [
        schema.Evidence(
            repo=provenance["repo"],
            path=provenance["file"],
            line_start=provenance["span"]["start_line"],
            line_end=provenance["span"]["end_line"],
            extractor=provenance["extractor"],
            version=provenance["extractor_version"],
        )
    ]


def _contract_evidence(link: dict[str, Any]) -> list[schema.Evidence]:
    """Two-sided evidence for a contract link: client site + declaration."""
    span = link.get("span") or {}
    start = span.get("start_line", link.get("line", 1))
    end = span.get("end_line", start)
    items = [
        schema.Evidence(
            repo="cross-repo",
            path=str(link.get("file", "")),
            line_start=int(start),
            line_end=int(end),
            extractor="bo_workflow.contract_link",
            version=WORKFLOW_VERSION,
        )
    ]
    if link.get("openapi_file"):
        oline = link.get("openapi_line", 1) or 1
        items.append(
            schema.Evidence(
                repo="cross-repo",
                path=str(link["openapi_file"]),
                line_start=int(oline),
                line_end=int(oline),
                extractor="bo_workflow.contract_link",
                version=WORKFLOW_VERSION,
            )
        )
    return items


def _is_linkable_route(route: dict[str, Any]) -> bool:
    """Mirror of ``emit_annotation_operations`` linkability."""
    methods = route.get("http_methods")
    path = route.get("path")
    return (
        isinstance(methods, list)
        and len(methods) == 1
        and isinstance(path, str)
        and path != ""
    )


# ---------------------------------------------------------------- ingest


def _persist_repo(
    store: OntologyStore,
    repo_name: str,
    repo_path: str,
    aggregate: dict[str, Any],
) -> None:
    """Extract one repo, persist claims, and accumulate cross-repo inputs."""
    index = ssb.index([repo_path])
    records = index["records"]
    java_files = sorted(Path(repo_path).rglob("*.java"))

    field_constraints: list[dict[str, Any]] = []
    interfaces: list[dict[str, Any]] = []
    implementations: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    enforcement: dict[str, dict[str, Any]] = {}
    for java in java_files:
        field_constraints.extend(ssb.extract_field_constraints(java))
        interfaces.extend(ssb.extract_java_interfaces(java))
        implementations.extend(ssb.extract_implements(java))
        entities.extend(ssb.extract_jpa_entities(java))
        enforcement[str(java)] = ssb.has_valid_enforcement(java)
    inherited = ssb.resolve_inheritance(java_files)
    jpa_links = ssb.link_jpa_to_sql(entities, records["persistence"])

    aggregate["telemetry"]["mapping_routes"] += len(records["mapping_routes"])
    aggregate["telemetry"]["field_constraints"] += len(field_constraints)
    aggregate["telemetry"]["record_constraints"] += len(
        records["record_constraints"]
    )
    aggregate["telemetry"]["interfaces"] += len(interfaces)
    aggregate["telemetry"]["implementations"] += len(implementations)
    aggregate["telemetry"]["jpa_entities"] += len(entities)
    aggregate["telemetry"]["jpa_links"] += len(jpa_links)
    aggregate["telemetry"]["sql_records"] += len(records["persistence"])
    aggregate["telemetry"]["openapi_operations"] += len(records["openapi"])
    aggregate["telemetry"]["client_urls"] += len(records["client_urls"])
    aggregate["telemetry"]["enforced_rules"] += len(records["enforced_rules"])
    aggregate["telemetry"]["raw_transitions"] += len(records["transitions"])

    aggregate["openapi"].extend(records["openapi"])
    aggregate["annotation_operations"].extend(records["annotation_operations"])
    aggregate["client_urls"].extend(records["client_urls"])

    param_ids: dict[str, Any] = {}
    for policy in records["policies"]:
        provenance = _provenance(
            repo_name, policy, "bo_java.spring_sql_bindings.extract_yaml_policy"
        )
        provenance["key"] = policy.get("key")
        pid = store.add_parameter(
            schema.PolicyParameter(
                name=f"{repo_name}:{policy.get('key')}",
                value=policy.get("value"),
                description=json.dumps(provenance, sort_keys=True),
            )
        )
        param_ids[str(policy.get("key"))] = pid
        aggregate["counts"]["params"] += 1

    rule_ids: dict[tuple[str, Any], Any] = {}
    for guard in records["enforced_rules"]:
        provenance = _provenance(
            repo_name, guard, "bo_java.spring_sql_bindings.extract_guards"
        )
        condition = str(guard.get("condition", ""))
        try:
            rel_guard = Path(guard["file"]).relative_to(repo_path)
        except ValueError:
            rel_guard = Path(guard["file"]).name
        rid = store.add_rule(
            schema.Rule(
                name=f"{repo_name}:{rel_guard.as_posix()}"
                f":L{guard.get('guard_if_line')}:{condition[:48]}",
                value=condition,
                statement=(
                    f"enforced rule {guard.get('guard')!r}: "
                    f"{condition} -> {guard.get('failure_outcome')}"
                ),
                predicate=condition,
                supporting=[provenance],
                evidence=_evidence(provenance),
            )
        )
        rule_ids[(str(guard.get("file")), guard.get("guard_if_line"))] = rid
        aggregate["counts"]["rules"] += 1

    transition_ids: dict[tuple[str, Any], Any] = {}
    for trans in records["transitions"]:
        provenance = _provenance(
            repo_name, trans, "bo_java.spring_sql_bindings.extract_guards"
        )
        target = str(trans.get("target_status", ""))
        trigger = json.dumps(
            {"status_write": target, "provenance": provenance}, sort_keys=True
        )
        tid = store.add_transition(
            schema.Transition(
                source=None,
                target=target or "unknown",
                guard=trans.get("guard"),
                trigger=trigger,
                evidence=_evidence(provenance),
            )
        )
        transition_ids[(str(trans.get("file")), trans.get("line"))] = tid
        aggregate["counts"]["transitions"] += 1
        if trans.get("guard") is None:
            aggregate["abstentions"].append(
                {
                    "kind": "unguarded_transition",
                    "repo": repo_name,
                    "target": target,
                    "file": provenance["file"],
                    "line": provenance["line"],
                    "span": provenance["span"],
                    "reason": (
                        "no preceding enforced guard claims this status "
                        "write (abstain, no guard synthesized)"
                    ),
                }
            )

    for route in records["mapping_routes"]:
        provenance = _provenance(
            repo_name,
            route,
            "bo_java.spring_sql_bindings.extract_mapping_routes",
        )
        linkable = _is_linkable_route(route)
        methods = route.get("http_methods")
        body = {
            "http_methods": methods,
            "path": route.get("path"),
            "class": route.get("class"),
            "handler": route.get("method"),
            "linkable": linkable,
            "reason": route.get("reason"),
            "provenance": provenance,
        }
        handler = route.get("method") or "unknown"
        opath = route.get("path") or "unlinkable"
        store.add_operation(
            schema.Operation(
                name=f"{repo_name}:{handler}:{opath}:L{provenance['line']}",
                description=json.dumps(body, sort_keys=True),
                evidence=_evidence(provenance),
            )
        )
        aggregate["counts"]["operations"] += 1
        if not linkable:
            aggregate["abstentions"].append(
                {
                    "kind": "unlinkable_route",
                    "repo": repo_name,
                    "handler": handler,
                    "file": provenance["file"],
                    "line": provenance["line"],
                    "span": provenance["span"],
                    "reason": route.get("reason")
                    or "route has no single method/path (abstain)",
                }
            )

    constrained = {
        str(Path(r["file"]).resolve())
        for r in field_constraints + records["record_constraints"]
        if r.get("file")
    }
    for path_text, verdict in enforcement.items():
        if str(Path(path_text).resolve()) in constrained and not verdict.get(
            "verified"
        ):
            spans = verdict.get("evidence_spans") or []
            gap_span = spans[0] if spans else {
                "start_line": int(verdict.get("line", 1)),
                "end_line": int(verdict.get("line", 1)),
            }
            aggregate["gaps"].append(
                {
                    "kind": "declared_only_enforcement",
                    "repo": repo_name,
                    "file": path_text,
                    "line": int(verdict.get("line", 1)),
                    "span": gap_span,
                    "reason": str(verdict.get("reason", "declared-only")),
                }
            )
    for gap in inherited:
        # resolve_inheritance reports missing/ambiguous superclasses as
        # resolved-False gaps; previously these were computed but dropped.
        if gap.get("kind") == "inheritance_gap" and not gap.get("resolved"):
            gap_line = int(gap.get("line", 1))
            aggregate["gaps"].append(
                {
                    "kind": "inheritance_gap",
                    "repo": repo_name,
                    "class": gap.get("class"),
                    "superclass": gap.get("superclass"),
                    "file": str(gap.get("file", "")),
                    "line": gap_line,
                    "span": gap.get("span")
                    or {"start_line": gap_line, "end_line": gap_line},
                    "reason": str(gap.get("reason", "unresolved inheritance")),
                }
            )

    aggregate["_param_ids"][repo_name] = param_ids
    aggregate["_rule_ids"][repo_name] = rule_ids
    aggregate["_trans_ids"][repo_name] = transition_ids
    aggregate["_policies"][repo_name] = records["policies"]
    aggregate["_guards"][repo_name] = records["enforced_rules"]
    aggregate["_trans"][repo_name] = records["transitions"]
    aggregate["_bindings_input"][repo_name] = (
        records["openapi"], interfaces, implementations
    )


def _link_dependencies(
    store: OntologyStore, aggregate: dict[str, Any]
) -> None:
    """Persist explicit same-evidence dependency edges (no name guessing)."""
    seen: set[tuple[str, Any, str, Any]] = set()

    def _link(src_kind, src_id, dst_kind, dst_id, reason):
        key = (src_kind, src_id, dst_kind, dst_id)
        if key in seen:
            return
        seen.add(key)
        store.add_dependency(src_kind, src_id, dst_kind, dst_id, reason=reason)
        aggregate["counts"]["dependencies"] += 1

    for repo_name in aggregate["repos"]:
        rule_ids = aggregate["_rule_ids"][repo_name]
        trans_ids = aggregate["_trans_ids"][repo_name]
        param_ids = aggregate["_param_ids"][repo_name]
        guards = { (str(g.get("file")), g.get("guard_if_line")): g
                   for g in aggregate["_guards"][repo_name] }
        for trans in aggregate["_trans"][repo_name]:
            tfile = str(trans.get("file"))
            tkey = (tfile, trans.get("line"))
            if tkey not in trans_ids:
                continue
            for gifl in [trans.get("guard_if_line")] + [
                p.get("guard_if_line") for p in trans.get("prerequisites", [])
            ]:
                if gifl is None:
                    continue
                rkey = (tfile, gifl)
                if rkey not in rule_ids:
                    continue
                guard = guards.get(rkey, {})
                _link(
                    "rules", rule_ids[rkey],
                    "transitions", trans_ids[tkey],
                    f"same-file guard: rule L{gifl} governs transition "
                    f"L{trans.get('line')} in {tfile} "
                    f"({trans.get('coverage')}; "
                    f"condition {str(guard.get('condition', ''))[:80]})",
                )
        for policy in aggregate["_policies"][repo_name]:
            key = str(policy.get("key"))
            if not key.strip():
                # Text-scanned YAML can yield an empty key (``: value``);
                # ``\b\b`` would match every condition, so skip it here.
                continue
            token = re.compile(r"\b%s\b" % re.escape(key))
            for guard in aggregate["_guards"][repo_name]:
                if not token.search(str(guard.get("condition", ""))):
                    continue
                rkey = (str(guard.get("file")), guard.get("guard_if_line"))
                if rkey not in rule_ids:
                    continue
                _link(
                    "params", param_ids[key],
                    "rules", rule_ids[rkey],
                    f"rule condition references param '{key}': "
                    f"{guard.get('file')}:{guard.get('line')} cites "
                    f"{policy.get('file')}:{policy.get('line')}",
                )


def _link_contracts(
    store: OntologyStore, aggregate: dict[str, Any]
) -> None:
    """Cross-repo declared-only links; unverified ones become abstentions."""
    # NOTE: ssb.index() already dedupes client_urls per repo on the same
    # (file, line, url) key with the same fluent preference, and aggregate
    # keys are repo-disjoint paths, so no second dedup pass is needed here.
    # The double walk below is required: links resolve each client URL
    # against the *unified* openapi/annotation sets across repos (e.g. a
    # Beta client calling an Alpha path), which per-repo index records
    # cannot see.
    urls = aggregate["client_urls"]

    links: list[dict[str, Any]] = []
    for url in urls:
        links.extend(ssb.link_client_url(url, aggregate["openapi"]))
        links.extend(
            ssb.link_client_to_annotation(url, aggregate["annotation_operations"])
        )
    aggregate["counts"]["contract_links"] = len(links)
    aggregate["counts"]["compatible_links"] = sum(
        1 for link in links if link.get("compatible") is True
    )

    for link in links:
        provenance = _provenance(
            "cross-repo", link, "bo_workflow.contract_link"
        )
        payload = {
            "client_url": link.get("client_url"),
            "client_path": link.get("client_path"),
            "openapi_path": link.get("openapi_path"),
            "compatible": bool(link.get("compatible")),
            "equivalent": False,
            "destination_verified": False,
            "destination_evidence": link.get("destination_evidence"),
            "reason": link.get("reason"),
            "provenance": provenance,
        }
        store.add_assertion(
            schema.Assertion(
                subject=f"link:{link.get('client_path')}"
                f":{link.get('openapi_path')}:{link.get('line')}",
                predicate=CONTRACT_LINK,
                object=payload,
                evidence=_contract_evidence(link),
            )
        )
        aggregate["counts"]["assertions"] += 1
        if link.get("compatible") is not True:
            aggregate["abstentions"].append(
                {
                    "kind": "unverified_link",
                    "repo": "cross-repo",
                    "client_url": link.get("client_url"),
                    "file": provenance["file"],
                    "line": provenance["line"],
                    "span": provenance["span"],
                    "reason": str(link.get("reason", "unverified")),
                }
            )


def _persist_ledger(
    store: OntologyStore, aggregate: dict[str, Any]
) -> None:
    """Persist abstentions/gaps/bindings as inspectable assertion rows."""
    seq = 0
    for entry in aggregate["abstentions"]:
        if entry["kind"] == "unverified_link":
            continue  # already persisted with its contract link
        seq += 1
        provenance = {
            "repo": entry.get("repo", ""),
            "file": str(entry.get("file", "")),
            "line": int(entry.get("line", 1)),
            "span": entry.get("span")
            or {"start_line": 1, "end_line": 1},
            "content_hash": "",
            "extractor": "bo_workflow",
            "extractor_version": WORKFLOW_VERSION,
            "origin": "deterministic",
        }
        store.add_assertion(
            schema.Assertion(
                subject=f"{provenance['repo']}:abstention:{seq}",
                predicate=ABSTENTION,
                object={**entry, "provenance": provenance},
            )
        )
        aggregate["counts"]["assertions"] += 1
    for entry in aggregate["gaps"]:
        seq += 1
        provenance = {
            "repo": entry.get("repo", ""),
            "file": str(entry.get("file", "")),
            "line": int(entry.get("line", 1)),
            "span": entry.get("span")
            or {"start_line": 1, "end_line": 1},
            "content_hash": "",
            "extractor": "bo_workflow",
            "extractor_version": WORKFLOW_VERSION,
            "origin": "deterministic",
        }
        store.add_assertion(
            schema.Assertion(
                subject=f"{provenance['repo']}:gap:{seq}",
                predicate=GAP,
                object={**entry, "provenance": provenance},
            )
        )
        aggregate["counts"]["assertions"] += 1


def ingest_manifest(
    manifest_path: str | Path, db_path: str | Path
) -> dict[str, Any]:
    """Ingest every manifest repo into ``db_path``; return a summary."""
    repos = load_manifest(manifest_path)
    aggregate: dict[str, Any] = {
        "repos": [r["name"] for r in repos],
        "counts": {
            "params": 0,
            "rules": 0,
            "transitions": 0,
            "operations": 0,
            "assertions": 0,
            "dependencies": 0,
            "contract_links": 0,
            "compatible_links": 0,
        },
        "telemetry": {
            "mapping_routes": 0,
            "field_constraints": 0,
            "record_constraints": 0,
            "interfaces": 0,
            "implementations": 0,
            "jpa_entities": 0,
            "jpa_links": 0,
            "sql_records": 0,
            "openapi_operations": 0,
            "client_urls": 0,
            "enforced_rules": 0,
            "raw_transitions": 0,
        },
        "abstentions": [],
        "gaps": [],
        "openapi": [],
        "annotation_operations": [],
        "client_urls": [],
        "_param_ids": {},
        "_rule_ids": {},
        "_trans_ids": {},
        "_policies": {},
        "_guards": {},
        "_trans": {},
        "_bindings_input": {},
    }
    with OntologyStore(str(db_path)) as store:
        for repo in repos:
            _persist_repo(store, repo["name"], repo["path"], aggregate)
        for repo_name, (openapi_ops, interfaces, implementations) in aggregate[
            "_bindings_input"
        ].items():
            for op in openapi_ops:
                binding = ssb.bind_operation_to_implementation(
                    op, interfaces, implementations
                )
                if binding.get("binding") != "full":
                    aggregate["abstentions"].append(
                        {
                            "kind": "unresolved_binding",
                            "repo": repo_name,
                            "operation_id": op.get("operation_id"),
                            "path": op.get("openapi_path"),
                            "file": op.get("file"),
                            "line": op.get("line"),
                            "span": op.get("span"),
                            "reason": str(binding.get("reason", "unresolved")),
                        }
                    )
        _link_dependencies(store, aggregate)
        _link_contracts(store, aggregate)
        _persist_ledger(store, aggregate)

    summary = {
        "manifest": str(manifest_path),
        "db": str(db_path),
        "repos": aggregate["repos"],
        "counts": aggregate["counts"],
        "telemetry": aggregate["telemetry"],
        "abstentions": aggregate["abstentions"],
        "gaps": aggregate["gaps"],
        "extractor_version": WORKFLOW_VERSION,
    }
    return summary


# ---------------------------------------------------------------- review


def record_review(
    db_path: str | Path,
    subject: str,
    reviewer: str,
    reason: str,
    status: str = "accepted",
) -> dict[str, Any]:
    """Persist one review decision; history is append-only (never rewrites)."""
    if not subject or not reviewer or not reason:
        raise ValueError("record_review needs subject, reviewer, and reason")
    payload = review_mod.record_review(
        {"subject": subject, "status": status,
         "reviewer": reviewer, "reason": reason}
    )
    with OntologyStore(str(db_path)) as store:
        rid = store.record_review(payload)
    return {"id": rid, "review": payload}


# ---------------------------------------------------------------- invalidate


def invalidate_root(
    db_path: str | Path,
    kind: str,
    record_id: Any,
    reason: str | None = None,
    new_value: Any = _ABSENT,
) -> dict[str, Any]:
    """Cascade-invalidate from one root; unrelated rows stay untouched.

    Repeat cascades are idempotent by design: an already-stale root is
    traversed but not re-audited (params carry no status column, so a
    prior ``invalidate`` history entry is the staleness signal there), so
    re-invalidating it with a different ``new_value`` leaves the stored
    value untouched and yields no new pairs. This append-only behaviour
    is intended, not a bug.
    """
    with OntologyStore(str(db_path)) as store:
        if new_value is _ABSENT:
            invalidated = store.invalidate_cascade(kind, record_id, reason)
        else:
            invalidated = store.invalidate_cascade(
                kind, record_id, reason, new_value
            )
    return {"invalidated": [list(pair) for pair in invalidated]}


# ---------------------------------------------------------------- report


def _detail_reason(entry: dict[str, Any]) -> str | None:
    detail = entry.get("detail", {})
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return None
    if isinstance(detail, dict):
        return detail.get("reason")
    return None


def report(db_path: str | Path) -> dict[str, Any]:
    """Inspect the unified store: active/stale claims, ledger, reviews."""
    with OntologyStore(str(db_path)) as store:
        active: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        for kind in _CLAIM_KINDS:
            for row in store.all(kind):
                label = (
                    row.get("name") or row.get("subject")
                    or row.get("target") or ""
                )
                entry: dict[str, Any] = {
                    "kind": kind,
                    "id": row.get("id"),
                    "name": label,
                    "status": row.get("status"),
                }
                invalidates = [
                    e for e in store.history(kind, row.get("id"))
                    if e.get("action") == "invalidate"
                ]
                if row.get("status") == "stale" or (
                    "status" not in row and invalidates
                ):
                    if invalidates:
                        entry["trigger"] = _detail_reason(invalidates[-1])
                    stale.append(entry)
                else:
                    active.append(entry)
        ledger = store.all("assertions")
        abstentions = [
            a for a in ledger if a.get("predicate") == ABSTENTION
        ]
        abstentions.extend(
            link for link in ledger
            if link.get("predicate") == CONTRACT_LINK
            and isinstance(link.get("object"), dict)
            and link["object"].get("compatible") is not True
        )
        gaps = [a for a in ledger if a.get("predicate") == GAP]
        links = [a for a in ledger if a.get("predicate") == CONTRACT_LINK]
        reviews = store.reviews()
        counts = {
            "active": len(active),
            "stale": len(stale),
            "abstentions": len(abstentions),
            "gaps": len(gaps),
            "contract_links": len(links),
            "reviews": len(reviews),
        }
    return {
        "db": str(db_path),
        "counts": counts,
        "active": active,
        "stale": stale,
        "abstentions": abstentions,
        "gaps": gaps,
        "contract_links": links,
        "reviews": reviews,
    }
