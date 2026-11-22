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
from bo_store.provenance import redact_value

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


# ---------------------------------------------------------------- builders

# Schema-record builders shared by ingest inserts and sync re-inserts, so a
# re-ingested row is byte-identical to its ingest-time twin.


def _param_record(
    repo_name: str, policy: dict[str, Any], provenance: dict[str, Any]
) -> schema.PolicyParameter:
    """Build the stored parameter row for one YAML policy."""
    return schema.PolicyParameter(
        name=f"{repo_name}:{policy.get('key')}",
        value=policy.get("value"),
        description=json.dumps(provenance, sort_keys=True),
    )


def _rule_record(
    repo_name: str,
    repo_path: str,
    guard: dict[str, Any],
    provenance: dict[str, Any],
) -> schema.Rule:
    """Build the stored rule row for one enforced guard."""
    condition = str(guard.get("condition", ""))
    try:
        rel_guard = Path(guard["file"]).relative_to(repo_path)
    except ValueError:
        rel_guard = Path(guard["file"]).name
    return schema.Rule(
        name=f"{repo_name}:{rel_guard.as_posix()}"
        f":L{guard.get('guard_if_line')}:{condition[:48]}",
        value=condition,
        statement=(
            f"enforced rule {guard.get('guard')!r}: "
            f"{condition} -> {guard.get('failure_outcome')}"
        ),
        predicate=condition,
        preconditions=guard.get("guard"),
        outcomes=guard.get("failure_outcome"),
        supporting=[provenance],
        evidence=_evidence(provenance),
    )


def _transition_record(
    trans: dict[str, Any], provenance: dict[str, Any]
) -> schema.Transition:
    """Build the stored transition row for one guarded status write."""
    target = str(trans.get("target_status") or "unknown")
    trigger = json.dumps(
        {
            "status_write": target,
            "provenance": provenance,
            "prerequisites": trans.get("prerequisites") or [],
            "coverage": trans.get("coverage") or "",
        },
        sort_keys=True,
    )
    return schema.Transition(
        source=None,
        target=target or "unknown",
        guard=trans.get("guard"),
        trigger=trigger,
        evidence=_evidence(provenance),
    )


def _operation_record(
    repo_name: str,
    route: dict[str, Any],
    provenance: dict[str, Any],
    linkable: bool,
) -> schema.Operation:
    """Build the stored operation row for one mapping route."""
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
    return schema.Operation(
        name=f"{repo_name}:{handler}:{opath}:L{provenance['line']}",
        description=json.dumps(body, sort_keys=True),
        evidence=_evidence(provenance),
    )


def _contract_assertion(link: dict[str, Any]) -> schema.Assertion:
    """Build the stored contract-link assertion for one resolved link."""
    provenance = _provenance("cross-repo", link, "bo_workflow.contract_link")
    payload = {
        "client_url": link.get("client_url"),
        "client_path": link.get("client_path"),
        "openapi_path": link.get("openapi_path"),
        "compatible": bool(link.get("compatible")),
        "equivalent": False,
        "destination_verified": False,
        "destination_evidence": link.get("destination_evidence"),
        "reason": link.get("reason"),
        "method_reason": link.get("method_reason"),
        "default_method": link.get("default_method", False),
        "provenance": provenance,
    }
    return schema.Assertion(
        subject=f"link:{link.get('client_path')}"
        f":{link.get('openapi_path')}:{link.get('line')}",
        predicate=CONTRACT_LINK,
        object=payload,
        evidence=_contract_evidence(link),
    )


def _ledger_assertion(
    entry: dict[str, Any], seq: int, predicate: str
) -> schema.Assertion:
    """Build the stored abstention/gap assertion for one ledger entry."""
    provenance = {
        "repo": entry.get("repo", ""),
        "file": str(entry.get("file", "")),
        "line": int(entry.get("line", 1)),
        "span": entry.get("span") or {"start_line": 1, "end_line": 1},
        "content_hash": "",
        "extractor": "bo_workflow",
        "extractor_version": WORKFLOW_VERSION,
        "origin": "deterministic",
    }
    leaf = "gap" if predicate == GAP else "abstention"
    return schema.Assertion(
        subject=f"{provenance['repo']}:{leaf}:{seq}",
        predicate=predicate,
        object={**entry, "provenance": provenance},
    )


# ---------------------------------------------------------------- ingest


def _extract_repo_state(
    repo_name: str, repo_path: str, aggregate: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Extract one repo without inserting (pure; shared by ingest + sync).

    Runs the same extractor calls as ingest, accumulates cross-repo inputs,
    telemetry, abstentions and gaps into ``aggregate``, and returns the
    pending per-kind ``[{"record", "provenance", ...}]`` payloads.
    """
    _check_repo_tree(repo_path)
    index = ssb.index([repo_path])
    records = index["records"]
    java_files = sorted(
        p for p in Path(repo_path).rglob("*.java") if not p.is_symlink()
    )

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
    # NOTE (intended): _check_repo_tree above already fail-closed on any
    # symlink, so for repo walks this filter never fires; it is
    # defense-in-depth for direct extract_ts_client_urls calls.
    ts_files = sorted(
        p
        for pattern in ("*.ts", "*.js")
        for p in Path(repo_path).rglob(pattern)
        if not p.is_symlink()
    )
    for ts_file in ts_files:
        ts_records = ssb.extract_ts_client_urls(ts_file)
        aggregate["client_urls"].extend(ts_records)
        aggregate["telemetry"]["client_urls"] += len(ts_records)
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

    pending: dict[str, list[dict[str, Any]]] = {
        "policies": [],
        "guards": [],
        "transitions": [],
        "routes": [],
    }
    for policy in records["policies"]:
        provenance = _provenance(
            repo_name, policy, "bo_java.spring_sql_bindings.extract_yaml_policy"
        )
        provenance["key"] = policy.get("key")
        pending["policies"].append(
            {"record": policy, "provenance": provenance}
        )

    for guard in records["enforced_rules"]:
        provenance = _provenance(
            repo_name, guard, "bo_java.spring_sql_bindings.extract_guards"
        )
        pending["guards"].append({"record": guard, "provenance": provenance})

    for trans in records["transitions"]:
        provenance = _provenance(
            repo_name, trans, "bo_java.spring_sql_bindings.extract_guards"
        )
        pending["transitions"].append(
            {"record": trans, "provenance": provenance}
        )
        target = str(trans.get("target_status") or "unknown")
        if trans.get("guard") is None:
            aggregate["abstentions"].append(
                {
                    "kind": "unguarded_transition",
                    "repo": repo_name,
                    "target": target,
                    "guard": trans.get("guard"),
                    "coverage": trans.get("coverage") or "",
                    "prerequisites": trans.get("prerequisites") or [],
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
        pending["routes"].append(
            {
                "record": route,
                "provenance": provenance,
                "linkable": linkable,
            }
        )
        if not linkable:
            aggregate["abstentions"].append(
                {
                    "kind": "unlinkable_route",
                    "repo": repo_name,
                    "handler": route.get("method") or "unknown",
                    "path": route.get("path"),
                    "http_methods": route.get("http_methods"),
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

    aggregate["_policies"][repo_name] = [p["record"] for p in pending["policies"]]
    aggregate["_guards"][repo_name] = [g["record"] for g in pending["guards"]]
    aggregate["_trans"][repo_name] = [t["record"] for t in pending["transitions"]]
    aggregate["_bindings_input"][repo_name] = (
        records["openapi"], interfaces, implementations
    )
    return pending


def _persist_extracted(
    store: OntologyStore,
    repo_name: str,
    repo_path: str,
    aggregate: dict[str, Any],
    pending: dict[str, list[dict[str, Any]]],
) -> None:
    """Insert one repo's extracted payloads; fills the id/linkage caches."""
    param_ids: dict[str, Any] = {}
    for item in pending["policies"]:
        policy = item["record"]
        pid = store.add_parameter(
            _param_record(repo_name, policy, item["provenance"])
        )
        param_ids[str(policy.get("key"))] = pid
        aggregate["counts"]["params"] += 1

    rule_ids: dict[tuple[str, Any], Any] = {}
    for item in pending["guards"]:
        guard = item["record"]
        rid = store.add_rule(
            _rule_record(repo_name, repo_path, guard, item["provenance"])
        )
        rule_ids[(str(guard.get("file")), guard.get("guard_if_line"))] = rid
        aggregate["counts"]["rules"] += 1

    transition_ids: dict[tuple[str, Any], Any] = {}
    for item in pending["transitions"]:
        trans = item["record"]
        tid = store.add_transition(
            _transition_record(trans, item["provenance"])
        )
        transition_ids[(str(trans.get("file")), trans.get("line"))] = tid
        aggregate["counts"]["transitions"] += 1

    for item in pending["routes"]:
        route = item["record"]
        store.add_operation(
            _operation_record(
                repo_name, route, item["provenance"], item["linkable"]
            )
        )
        aggregate["counts"]["operations"] += 1

    aggregate["_param_ids"][repo_name] = param_ids
    aggregate["_rule_ids"][repo_name] = rule_ids
    aggregate["_trans_ids"][repo_name] = transition_ids


def _persist_repo(
    store: OntologyStore,
    repo_name: str,
    repo_path: str,
    aggregate: dict[str, Any],
) -> None:
    """Extract one repo, persist claims, and accumulate cross-repo inputs."""
    pending = _extract_repo_state(repo_name, repo_path, aggregate)
    _persist_extracted(store, repo_name, repo_path, aggregate, pending)


def _link_dependencies(
    store: OntologyStore,
    aggregate: dict[str, Any],
    ignore_duplicates: bool = False,
) -> None:
    """Persist explicit same-evidence dependency edges (no name guessing).

    With ``ignore_duplicates`` (sync re-derivation), already-present edges
    are skipped instead of raising; only successful links bump the count.
    """
    seen: set[tuple[str, Any, str, Any]] = set()

    def _link(src_kind, src_id, dst_kind, dst_id, reason):
        key = (src_kind, src_id, dst_kind, dst_id)
        if key in seen:
            return
        seen.add(key)
        try:
            store.add_dependency(src_kind, src_id, dst_kind, dst_id,
                                 reason=reason)
        except ValueError:
            if not ignore_duplicates:
                raise
        else:
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


def _compute_links(aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve every client URL against the unified openapi/annotation sets.

    NOTE: ``ssb.index()`` already dedupes client URLs per repo on the same
    (file, line, url) key with the same fluent preference, and aggregate
    keys are repo-disjoint paths, so no second dedup pass is needed here.
    The double walk below is required: links resolve each client URL
    against the *unified* openapi/annotation sets across repos (e.g. a
    Beta client calling an Alpha path), which per-repo index records
    cannot see.
    """
    urls = aggregate["client_urls"]
    links: list[dict[str, Any]] = []
    for url in urls:
        links.extend(ssb.link_client_url(url, aggregate["openapi"]))
        links.extend(
            ssb.link_client_to_annotation(url, aggregate["annotation_operations"])
        )
    return links


def _collect_binding_abstentions(aggregate: dict[str, Any]) -> None:
    """Append unresolved operation-binding abstentions (per repo, in order)."""
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


def _link_contracts(
    store: OntologyStore, aggregate: dict[str, Any]
) -> None:
    """Cross-repo declared-only links; unverified ones become abstentions."""
    links = _compute_links(aggregate)
    aggregate["counts"]["contract_links"] = len(links)
    aggregate["counts"]["compatible_links"] = sum(
        1 for link in links if link.get("compatible") is True
    )

    for link in links:
        store.add_assertion(_contract_assertion(link))
        aggregate["counts"]["assertions"] += 1
        if link.get("compatible") is not True:
            provenance = _provenance(
                "cross-repo", link, "bo_workflow.contract_link"
            )
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
        store.add_assertion(_ledger_assertion(entry, seq, ABSTENTION))
        aggregate["counts"]["assertions"] += 1
    for entry in aggregate["gaps"]:
        seq += 1
        store.add_assertion(_ledger_assertion(entry, seq, GAP))
        aggregate["counts"]["assertions"] += 1


def _new_aggregate(repos: list[dict[str, str]]) -> dict[str, Any]:
    """Single aggregate constructor shared by ingest and sync (no drift)."""
    return {
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


def ingest_manifest(
    manifest_path: str | Path, db_path: str | Path
) -> dict[str, Any]:
    """Ingest every manifest repo into ``db_path``; return a summary."""
    repos = load_manifest(manifest_path)
    aggregate: dict[str, Any] = _new_aggregate(repos)
    with OntologyStore(str(db_path)) as store:
        for repo in repos:
            _persist_repo(store, repo["name"], repo["path"], aggregate)
        _collect_binding_abstentions(aggregate)
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


# ---------------------------------------------------------------- sync

_STRIP_LINE = re.compile(r"L\d+\b")

# Fail-closed guards (no core rewrite): absolute paths stay supported per the
# manifest contract, but every repo tree is pre-scanned before any extractor
# runs. Symlinks are rejected (local-file-inclusion risk; skipping would
# silently drop facts) and oversized trees are rejected to cap walk cost.
_MAX_REPO_FILES = 20000


def _check_repo_tree(repo_path: str) -> None:
    """Reject symlinks and cap tree size before extraction (fail-closed)."""
    root = Path(repo_path)
    count = 0
    for entry in root.rglob("*"):
        # is_symlink() is checked first so broken links still fail closed.
        if entry.is_symlink():
            raise ValueError(
                f"repo {repo_path}: symlink not supported: {entry}"
            )
        if entry.is_file():
            count += 1
            if count > _MAX_REPO_FILES:
                raise ValueError(
                    f"repo {repo_path}: too many files "
                    f"(>{_MAX_REPO_FILES}); refusing walk"
                )


def _strip_lines(text: str) -> str:
    """Drop ``L<digits>`` markers so line shifts do not change identity."""
    return _STRIP_LINE.sub("L", text)


def _prereq_key(prereqs: Any) -> list[str]:
    """Line-free sorted prerequisite guard names for fingerprints/keys.

    Redact-normalized so secret-shaped guard tokens match their stored
    ``[REDACTED]`` twins instead of churning every sync.
    """
    if not prereqs:
        return []
    items: list[str] = []
    for item in prereqs:
        if isinstance(item, dict):
            items.append(
                redact_value(_strip_lines(str(item.get("guard") or "")))
            )
        else:
            items.append(redact_value(_strip_lines(str(item or ""))))
    return sorted(items)


def _as_json(value: Any) -> Any:
    """Parse a JSON-encoded column value; pass mappings/lists through."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("[", "{")):
            try:
                return json.loads(text)
            except ValueError:
                return None
    return None


def _relpath(repo_path: str | None, file: str) -> str:
    """Repo-relative posix path; falls back to the raw path when outside."""
    if not file:
        return ""
    if repo_path:
        try:
            return Path(file).relative_to(repo_path).as_posix()
        except ValueError:
            pass
    return str(file)


def _rel_any(repo_paths: dict[str, str], file: str) -> str:
    """Relativize against whichever manifest repo contains the file."""
    for base in repo_paths.values():
        try:
            return Path(file).relative_to(base).as_posix()
        except ValueError:
            continue
    return str(file or "")


def _sync_identity(
    kind: str,
    repo: str,
    repo_path: str,
    record: dict[str, Any],
    provenance: dict[str, Any],
) -> tuple | None:
    """Stable fact identity: exact repo+file, line-free content markers.

    Returns None when either side is unparseable (never merge those).
    """
    rel = _relpath(repo_path, str(provenance.get("file") or ""))
    if not repo:
        return None
    if kind == "params":
        key = provenance.get("key", record.get("key"))
        if key is None:
            return None
        return ("params", repo, rel, str(key))
    if kind == "rules":
        condition = record.get("condition")
        if condition is None:
            return None
        return ("rules", repo, rel, _strip_lines(str(condition)))
    if kind == "transitions":
        return (
            "transitions",
            repo,
            rel,
            str(record.get("target_status") or "unknown"),
            _strip_lines(str(record.get("guard") or "")),
        )
    if kind == "operations":
        return (
            "operations",
            repo,
            rel,
            str(record.get("method") or ""),
            str(record.get("path") or ""),
            json.dumps(
                record.get("http_methods"), sort_keys=True, default=str
            ),
        )
    return None


def _nofile_identity(ident: tuple | None) -> tuple | None:
    """File-less identity: drop the rel component (index 2).

    Used as a fallback when the stored file was redacted to ``[REDACTED]``.
    """
    if ident is None or len(ident) < 3:
        return None
    return (ident[0], ident[1]) + tuple(ident[3:])


def _stored_identity(
    kind: str, row: dict[str, Any], repo_paths: dict[str, str]
) -> tuple | None:
    """Recompute a stored row's sync identity from its persisted columns."""
    if kind == "params":
        desc = _as_json(row.get("description")) or {}
        if not isinstance(desc, dict):
            desc = {}
        repo = desc.get("repo")
        key = desc.get("key")
        if repo is None or key is None:
            name = str(row.get("name") or "")
            if ":" in name:
                repo, key = name.split(":", 1)
            else:
                return None
        if not repo or key is None:
            return None
        return (
            "params",
            repo,
            _relpath(repo_paths.get(repo), str(desc.get("file") or "")),
            str(key),
        )
    if kind == "rules":
        supporting = _as_json(row.get("supporting"))
        prov: dict[str, Any] = {}
        if isinstance(supporting, list) and supporting:
            prov = supporting[0] if isinstance(supporting[0], dict) else {}
        elif isinstance(supporting, dict):
            prov = supporting
        repo = prov.get("repo")
        condition = row.get("value")
        if not repo or condition is None:
            return None
        # Provenance file paths are redacted to "[REDACTED]" on insert when
        # they look secret-like (long spaceless tmp paths); the stored name
        # (which contains spaces, so it survives redaction) carries the
        # repo-relative path instead: "<repo>:<rel>:L<line>:<cond>".
        pfile = str(prov.get("file") or "")
        if pfile and pfile != "[REDACTED]":
            rel: str | None = _relpath(repo_paths.get(repo), pfile)
        else:
            rel = None
            prefix = f"{repo}:"
            name = str(row.get("name") or "")
            if name.startswith(prefix):
                match = re.match(
                    r"^(.+?):L\d+:.*$", name[len(prefix):], re.DOTALL
                )
                if match:
                    rel = match.group(1)
        if rel is None:
            return None
        return ("rules", repo, rel, _strip_lines(str(condition)))
    if kind == "transitions":
        trigger = _as_json(row.get("trigger")) or {}
        if not isinstance(trigger, dict):
            return None
        prov = trigger.get("provenance") or {}
        if not isinstance(prov, dict):
            prov = {}
        repo = prov.get("repo")
        if not repo:
            return None
        return (
            "transitions",
            repo,
            _relpath(repo_paths.get(repo), str(prov.get("file") or "")),
            str(row.get("target") or "unknown"),
            _strip_lines(str(row.get("guard") or "")),
        )
    if kind == "operations":
        body = _as_json(row.get("description"))
        if not isinstance(body, dict):
            return None
        prov = body.get("provenance") or {}
        if not isinstance(prov, dict):
            prov = {}
        repo = prov.get("repo")
        if not repo:
            return None
        return (
            "operations",
            repo,
            _relpath(repo_paths.get(repo), str(prov.get("file") or "")),
            str(body.get("handler") or ""),
            str(body.get("path") or ""),
            json.dumps(
                body.get("http_methods"), sort_keys=True, default=str
            ),
        )
    return None


def _fresh_fingerprint(kind: str, item: dict[str, Any]) -> str:
    """Semantic content hash of a freshly extracted record (line-free).

    Every value component is ``redact_value``-normalized so a secret-shaped
    raw value matches its stored ``[REDACTED]`` twin (no perpetual churn,
    no raw secret persistence in the comparison payload).
    """
    record = item["record"]
    if kind == "params":
        payload = ["v", redact_value(record.get("value"))]
    elif kind == "rules":
        payload = [
            "c",
            redact_value(_strip_lines(str(record.get("condition", "")))),
            redact_value(_strip_lines(str(record.get("guard") or ""))),
            redact_value(str(record.get("failure_outcome") or "")),
        ]
    elif kind == "transitions":
        payload = [
            "t",
            redact_value(str(record.get("target_status") or "unknown")),
            redact_value(_strip_lines(str(record.get("guard") or ""))),
            _prereq_key(record.get("prerequisites")),
            redact_value(str(record.get("coverage") or "")),
        ]
    else:
        payload = [
            "o",
            redact_value(str(record.get("method") or "")),
            redact_value(str(record.get("path") or "")),
            redact_value(record.get("http_methods")),
            redact_value(record.get("class")),
            item.get("linkable"),
            redact_value(record.get("reason")),
        ]
    return json.dumps(payload, sort_keys=True, default=str)


def _stored_fingerprint(kind: str, row: dict[str, Any]) -> str:
    """Semantic content hash of a stored row, mirroring the fresh shape.

    Same ``redact_value`` normalization as the fresh side: the store
    already redacts on INSERT, and re-normalizing here keeps the
    comparison idempotent (``redact_value("[REDACTED]")`` is stable).
    """
    if kind == "params":
        payload = ["v", redact_value(row.get("value"))]
    elif kind == "rules":
        guard_val = row.get("preconditions")
        if isinstance(guard_val, list):
            guard_val = guard_val[0] if guard_val else ""
        outcome_val = row.get("outcomes")
        if isinstance(outcome_val, list):
            outcome_val = outcome_val[0] if outcome_val else ""
        # Fallback for rows written before guard/outcome columns existed:
        # parse the statement "enforced rule '<guard>': <cond> -> <outcome>".
        if guard_val is None and outcome_val is None:
            stmt = str(row.get("statement") or "")
            match = re.match(
                r"^enforced rule '(.*)': .* -> (.*)$", stmt, re.DOTALL
            )
            if match:
                guard_val, outcome_val = match.group(1), match.group(2)
        payload = [
            "c",
            redact_value(_strip_lines(str(row.get("value", "")))),
            redact_value(_strip_lines(str(guard_val or ""))),
            redact_value(str(outcome_val or "")),
        ]
    elif kind == "transitions":
        trigger = _as_json(row.get("trigger")) or {}
        if not isinstance(trigger, dict):
            trigger = {}
        payload = [
            "t",
            redact_value(str(row.get("target") or "unknown")),
            redact_value(_strip_lines(str(row.get("guard") or ""))),
            _prereq_key(trigger.get("prerequisites")),
            redact_value(str(trigger.get("coverage") or "")),
        ]
    else:
        body = _as_json(row.get("description")) or {}
        if not isinstance(body, dict):
            body = {}
        payload = [
            "o",
            redact_value(str(body.get("handler") or "")),
            redact_value(str(body.get("path") or "")),
            redact_value(body.get("http_methods")),
            redact_value(body.get("class")),
            body.get("linkable"),
            redact_value(body.get("reason")),
        ]
    return json.dumps(payload, sort_keys=True, default=str)


def _is_active_row(
    store: OntologyStore, kind: str, row: dict[str, Any]
) -> bool:
    """Active = not stale; status-less rows consult invalidate history."""
    if row.get("status") == "stale":
        return False
    if "status" not in row:
        return not any(
            e.get("action") == "invalidate"
            for e in store.history(kind, row.get("id"))
        )
    return True


def _stored_span(kind: str, row: dict[str, Any]) -> str:
    """Human span citation (``La-Lb``) from a stored row's provenance."""
    prov: Any = None
    if kind == "params":
        prov = _as_json(row.get("description"))
    elif kind == "rules":
        supporting = _as_json(row.get("supporting"))
        if isinstance(supporting, list) and supporting:
            prov = supporting[0]
        elif isinstance(supporting, dict):
            prov = supporting
    elif kind == "transitions":
        trigger = _as_json(row.get("trigger"))
        prov = trigger.get("provenance") if isinstance(trigger, dict) else None
    else:
        body = _as_json(row.get("description"))
        prov = body.get("provenance") if isinstance(body, dict) else None
    span = (prov or {}).get("span") if isinstance(prov, dict) else None
    if isinstance(span, dict):
        return f"L{span.get('start_line')}-L{span.get('end_line')}"
    return "L?-L?"


def _ledger_key(
    predicate: str, obj: dict[str, Any], repo_paths: dict[str, str]
) -> tuple[tuple | None, tuple | None]:
    """Line-tolerant ledger identity: ``(full key, file-less key)``.

    The file-less key exists only for shapes carrying a file component; it
    lets a fresh entry match a stored row whose file path was redacted to
    ``[REDACTED]`` on insert. Either element may be None (unparseable).
    Secret-shaped components are ``redact_value``-normalized on both
    sides so fresh raw values match stored ``[REDACTED]`` twins.
    """
    if not isinstance(obj, dict):
        return None, None
    if predicate == CONTRACT_LINK:
        key = (
            "contract_link",
            redact_value(obj.get("client_path")),
            redact_value(obj.get("openapi_path")),
            bool(obj.get("compatible")),
        )
        return key, None
    kind = obj.get("kind")
    repo = obj.get("repo", "")
    rel = redact_value(_rel_any(repo_paths, str(obj.get("file") or "")))
    if kind == "unguarded_transition":
        guard = redact_value(_strip_lines(str(obj.get("guard") or "")))
        coverage = redact_value(str(obj.get("coverage") or ""))
        prereqs = _prereq_key(obj.get("prerequisites"))
        prereq_json = json.dumps(prereqs, sort_keys=True, default=str)
        target = redact_value(str(obj.get("target") or "unknown"))
        key = (
            "abstention",
            kind,
            repo,
            rel,
            target,
            guard,
            coverage,
            prereq_json,
        )
        return key, (
            "abstention",
            kind,
            repo,
            target,
            guard,
            coverage,
            prereq_json,
        )
    if kind == "unlinkable_route":
        handler = redact_value(str(obj.get("handler") or "unknown"))
        path = redact_value(str(obj.get("path") or ""))
        methods_json = json.dumps(
            redact_value(obj.get("http_methods")), sort_keys=True, default=str
        )
        key = ("abstention", kind, repo, rel, handler, path, methods_json)
        return key, ("abstention", kind, repo, handler, path, methods_json)
    if kind == "unresolved_binding":
        key = (
            "abstention",
            kind,
            repo,
            redact_value(obj.get("operation_id")),
            redact_value(obj.get("path")),
        )
        return key, None
    if kind == "inheritance_gap":
        key = (
            "gap",
            kind,
            repo,
            redact_value(obj.get("class")),
            redact_value(obj.get("superclass")),
        )
        return key, None
    if kind == "declared_only_enforcement":
        key = (
            "gap",
            kind,
            repo,
            rel,
            redact_value(_strip_lines(str(obj.get("reason", "")))),
        )
        return key, (
            "gap",
            kind,
            repo,
            redact_value(_strip_lines(str(obj.get("reason", "")))),
        )
    if kind is None:
        return None, None
    key = (
        predicate,
        kind,
        repo,
        rel,
        redact_value(_strip_lines(str(obj.get("reason", "")))),
    )
    return key, (
        predicate,
        kind,
        repo,
        redact_value(_strip_lines(str(obj.get("reason", "")))),
    )


def _fresh_aggregate(
    repos: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, dict[str, list[dict[str, Any]]]]]:
    """Extract every manifest repo without inserting (sync source of truth)."""
    aggregate: dict[str, Any] = _new_aggregate(repos)
    pendings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for repo in repos:
        pendings[repo["name"]] = _extract_repo_state(
            repo["name"], repo["path"], aggregate
        )
    _collect_binding_abstentions(aggregate)
    return aggregate, pendings


_VALUE_KINDS = frozenset({"params", "rules"})


def sync_manifest(
    manifest_path: str | Path, db_path: str | Path
) -> dict[str, Any]:
    """Diff fresh extraction against the store; invalidate changed/removed.

    Stable per-fact identity is exact ``(kind, repo, file)`` plus line-free
    content markers; unparseable records never match (abstain, never merge).
    Changed or removed roots go through ``invalidate_cascade`` with a
    ``sync: <file>:<span> changed/removed`` trigger reason; replacements are
    inserted as new rows (never delete; stale rows are preserved). Reviews
    are never touched; a clean re-run writes nothing.
    """
    repos = load_manifest(manifest_path)  # validates before any DB touch
    repo_paths = {r["name"]: r["path"] for r in repos}
    aggregate, pendings = _fresh_aggregate(repos)
    fresh_links = _compute_links(aggregate)

    added = 0
    unchanged = 0
    invalidated_roots: list[dict[str, Any]] = []
    cascaded: list[dict[str, Any]] = []
    seen_cascade: set[tuple[str, Any]] = set()

    pending_kinds = (
        ("params", "policies"),
        ("rules", "guards"),
        ("transitions", "transitions"),
        ("operations", "routes"),
    )
    # Dependents first: invalidating a root cascades toward its dependents,
    # so classifying/applying operations -> transitions -> rules -> params
    # guarantees every kind is diffed against its pre-sync state.
    # Classify-then-apply per kind: first bucket fresh vs stored (active /
    # stale) and flag each fresh item (_sync_live / _sync_changed /
    # _sync_known_stale / _sync_reinsert); then apply invalidates + inserts
    # for that kind before moving to the next. Cascades applied here may
    # stale rows of a later (already-classified) kind, so a liveness
    # fixup below re-checks every flagged-live endpoint before edges are
    # re-derived only among still-active rows. Comment-only: no behavior
    # change; splitting phases further would risk cross-kind drift.
    sync_order = tuple(reversed(pending_kinds))

    def _note_cascade(pairs: list[tuple]) -> None:
        for table, rid in pairs:
            key = (table, rid)
            if key not in seen_cascade:
                seen_cascade.add(key)
                cascaded.append({"kind": table, "id": rid})

    with OntologyStore(str(db_path)) as store:
        for kind, pkey in sync_order:
            fresh_by_id: dict[tuple, list[dict[str, Any]]] = {}
            for repo in repos:
                for item in pendings[repo["name"]][pkey]:
                    ident = _sync_identity(
                        kind,
                        repo["name"],
                        repo["path"],
                        item["record"],
                        item["provenance"],
                    )
                    if ident is None:
                        continue  # unparseable: abstain, never merge
                    item["_sync_identity"] = ident
                    fresh_by_id.setdefault(ident, []).append(item)
            stored_by_id: dict[tuple, dict[str, list[dict]]] = {}
            for row in store.all(kind):
                ident = _stored_identity(kind, row, repo_paths)
                if ident is None:
                    continue
                bucket = stored_by_id.setdefault(
                    ident, {"active": [], "stale": []}
                )
                if _is_active_row(store, kind, row):
                    bucket["active"].append(row)
                else:
                    bucket["stale"].append(row)

            # File-less fallback: a stored file redacted to "[REDACTED]"
            # on insert can never exactly match its fresh counterpart.
            # Match those via the nofile key (repo+content, no file) so a
            # redacted tmp path does not cause permanent removed+added
            # churn. Exact matches always win; each redacted stored ident
            # is consumed at most once (pos-ordered like exact buckets).
            redacted_by_nofile: dict[tuple, list[tuple]] = {}
            for stored_ident in stored_by_id:
                if (
                    len(stored_ident) > 2
                    and stored_ident[2] == "[REDACTED]"
                ):
                    nofile = _nofile_identity(stored_ident)
                    if nofile is not None:
                        redacted_by_nofile.setdefault(nofile, []).append(
                            stored_ident
                        )
            effective_stored: dict[tuple, tuple | None] = {}
            consumed_stored: set[tuple] = set()
            for fresh_ident in fresh_by_id:
                if fresh_ident in stored_by_id:
                    effective_stored[fresh_ident] = fresh_ident
                    consumed_stored.add(fresh_ident)
                else:
                    nofile = _nofile_identity(fresh_ident)
                    candidates = (
                        redacted_by_nofile.get(nofile, []) if nofile else []
                    )
                    chosen = next(
                        (c for c in candidates if c not in consumed_stored),
                        None,
                    )
                    effective_stored[fresh_ident] = chosen
                    if chosen is not None:
                        consumed_stored.add(chosen)
            fresh_nofiles = {
                nf for f in fresh_by_id if (nf := _nofile_identity(f))
            }

            for ident in list(fresh_by_id) + [
                i for i in stored_by_id if i not in consumed_stored
                and i not in fresh_by_id
            ]:
                if ident in fresh_by_id:
                    fresh_items = fresh_by_id.get(ident, [])
                    eff = effective_stored.get(ident)
                    bucket = (
                        stored_by_id.get(eff, {})
                        if eff is not None
                        else {}
                    )
                    actives = list(bucket.get("active", []))
                    stales = list(bucket.get("stale", []))
                else:
                    # Stored-only: skip removal when a redacted row's
                    # file-less key still has a fresh counterpart (the
                    # fallback above already paired it, or a duplicate
                    # shares the content key).
                    if (
                        len(ident) > 2
                        and ident[2] == "[REDACTED]"
                        and _nofile_identity(ident) in fresh_nofiles
                    ):
                        continue
                    fresh_items = []
                    actives = stored_by_id.get(ident, {}).get("active", [])
                    stales = []
                used_active = [False] * len(actives)
                used_stale = [False] * len(stales)
                for item in fresh_items:
                    fp = _fresh_fingerprint(kind, item)
                    # Prefer an active row with the same fingerprint:
                    # unchanged, never touch a stale duplicate.
                    same = next(
                        (
                            j
                            for j, row in enumerate(actives)
                            if not used_active[j]
                            and _stored_fingerprint(kind, row) == fp
                        ),
                        None,
                    )
                    if same is not None:
                        used_active[same] = True
                        unchanged += 1
                        item["_sync_live"] = True
                        item["_sync_id"] = actives[same]["id"]
                        continue
                    # Active row, different fingerprint: modified.
                    other = next(
                        (
                            j
                            for j in range(len(actives))
                            if not used_active[j]
                        ),
                        None,
                    )
                    if other is not None:
                        used_active[other] = True
                        item["_sync_changed"] = True
                        # Remember the exact stored row this changed
                        # item replaces so the apply step invalidates
                        # the fallback row, not a same-shaped exact row.
                        item["_sync_old_id"] = actives[other]["id"]
                        item["_sync_old_span"] = _stored_span(
                            kind, actives[other]
                        )
                        continue
                    # Only stale row(s), same fingerprint:
                    # already-known-stale. Do nothing: no insert, no
                    # audit, no edge re-derivation. Counted unchanged
                    # so a clean re-run accounts every fresh fact.
                    known = next(
                        (
                            k
                            for k, row in enumerate(stales)
                            if not used_stale[k]
                            and _stored_fingerprint(kind, row) == fp
                        ),
                        None,
                    )
                    if known is not None:
                        used_stale[known] = True
                        unchanged += 1
                        item["_sync_known_stale"] = True
                        continue
                    # Stale row, different fingerprint: the source
                    # changed again on a stale fact. Insert a
                    # replacement as new active below; leave the old
                    # stale row alone (no re-audit).
                    replaced = next(
                        (
                            k
                            for k in range(len(stales))
                            if not used_stale[k]
                        ),
                        None,
                    )
                    if replaced is not None:
                        used_stale[replaced] = True
                        item["_sync_reinsert"] = True
                        continue
                    # Brand-new fact: inserted below with no flags.
                for j, row in enumerate(actives):
                    if used_active[j]:
                        continue
                    rel = ident[2] if len(ident) > 2 else ""
                    # For fallback pairs the fresh rel is informative;
                    # "[REDACTED]" alone would hide which file changed.
                    reason = (
                        f"sync: {rel} removed: source fact no longer "
                        f"extracted"
                    )
                    pairs = store.invalidate_cascade(
                        kind, row["id"], reason
                    )
                    if pairs:
                        invalidated_roots.append(
                            {
                                "kind": kind,
                                "id": row["id"],
                                "reason": reason,
                            }
                        )
                        _note_cascade(pairs)

            for ident, fresh_items in fresh_by_id.items():
                for item in fresh_items:
                    if item.get("_sync_known_stale"):
                        continue  # already-known-stale: nothing to do
                    if item.get("_sync_live"):
                        continue  # unchanged active row
                    repo = ident[1]
                    repo_path = repo_paths[repo]
                    provenance = item["provenance"]
                    if kind == "params":
                        record = _param_record(
                            repo, item["record"], provenance
                        )
                    elif kind == "rules":
                        record = _rule_record(
                            repo, repo_path, item["record"], provenance
                        )
                    elif kind == "transitions":
                        record = _transition_record(
                            item["record"], provenance
                        )
                    else:
                        record = _operation_record(
                            repo,
                            item["record"],
                            provenance,
                            item.get("linkable", False),
                        )
                    if item.get("_sync_changed"):
                        old_id = item["_sync_old_id"]
                        rel = ident[2] if len(ident) > 2 else ""
                        span = item.get("_sync_old_span", "L?-L?")
                        if kind == "params":
                            # Never interpolate raw values: the reason is
                            # audited and echoed in the sync summary/CLI.
                            # Old/new values travel only via the store's
                            # redacted original_value/new_value fields.
                            detail = "value changed"
                        elif kind == "rules":
                            detail = "condition changed"
                        elif kind == "transitions":
                            detail = "target/guard changed"
                        else:
                            detail = "route changed"
                        reason = f"sync: {rel}:{span} changed: {detail}"
                        if kind in _VALUE_KINDS:
                            pairs = store.invalidate_cascade(
                                kind,
                                old_id,
                                reason,
                                record.value,
                            )
                        else:
                            pairs = store.invalidate_cascade(
                                kind, old_id, reason
                            )
                        if pairs:
                            invalidated_roots.append(
                                {
                                    "kind": kind,
                                    "id": old_id,
                                    "reason": reason,
                                }
                            )
                            _note_cascade(pairs)
                    new_id = store.insert(record)
                    added += 1
                    item["_sync_live"] = True
                    item["_sync_id"] = new_id

        # Liveness re-check: cascades applied above may have staled rows
        # that were active at classification time; edges are re-derived
        # only where both endpoints are still active.
        for kind, pkey in pending_kinds:
            for repo in repos:
                for item in pendings[repo["name"]][pkey]:
                    if not item.get("_sync_live"):
                        continue
                    row = store.get(kind, item["_sync_id"])
                    if row is None or not _is_active_row(store, kind, row):
                        item["_sync_live"] = False
        # Re-derive explicit edges among live endpoints only; existing
        # edges are skipped instead of raising (idempotent re-run).
        link_agg: dict[str, Any] = {
            "repos": [r["name"] for r in repos],
            "counts": {"dependencies": 0},
            "_param_ids": {},
            "_rule_ids": {},
            "_trans_ids": {},
            "_policies": {},
            "_guards": {},
            "_trans": {},
        }
        for repo in repos:
            link_agg["_policies"][repo["name"]] = []
            link_agg["_param_ids"][repo["name"]] = {}
            link_agg["_guards"][repo["name"]] = []
            link_agg["_rule_ids"][repo["name"]] = {}
            link_agg["_trans"][repo["name"]] = []
            link_agg["_trans_ids"][repo["name"]] = {}
        for repo in repos:
            pending = pendings[repo["name"]]
            for item in pending["policies"]:
                if not item.get("_sync_live"):
                    continue
                link_agg["_policies"].setdefault(
                    repo["name"], []).append(item["record"])
                link_agg["_param_ids"].setdefault(repo["name"], {})[
                    str(item["record"].get("key"))] = item["_sync_id"]
            for item in pending["guards"]:
                if not item.get("_sync_live"):
                    continue
                link_agg["_guards"].setdefault(
                    repo["name"], []).append(item["record"])
                link_agg["_rule_ids"].setdefault(repo["name"], {})[(
                    str(item["record"].get("file")),
                    item["record"].get("guard_if_line"),
                )] = item["_sync_id"]
            for item in pending["transitions"]:
                if not item.get("_sync_live"):
                    continue
                link_agg["_trans"].setdefault(
                    repo["name"], []).append(item["record"])
                link_agg["_trans_ids"].setdefault(repo["name"], {})[(
                    str(item["record"].get("file")),
                    item["record"].get("line"),
                )] = item["_sync_id"]
        _link_dependencies(store, link_agg, ignore_duplicates=True)

        # Ledger: match fresh assertions by stable id, else by line-tolerant
        # key (file-less fallback covers file paths redacted on insert);
        # insert only genuinely new entries (never invalidate ledger).
        fresh_assertions: list[
            tuple[schema.Assertion, tuple | None, tuple | None]
        ] = []
        for link in fresh_links:
            assertion = _contract_assertion(link)
            fresh_assertions.append(
                (
                    assertion,
                    (
                        "contract_link",
                        link.get("client_path"),
                        link.get("openapi_path"),
                        bool(link.get("compatible")),
                    ),
                    None,
                )
            )
        seq = 0
        for entry in aggregate["abstentions"]:
            if entry["kind"] == "unverified_link":
                continue  # already persisted with its contract link
            seq += 1
            key, nofile = _ledger_key(ABSTENTION, dict(entry), repo_paths)
            fresh_assertions.append(
                (_ledger_assertion(entry, seq, ABSTENTION), key, nofile)
            )
        for entry in aggregate["gaps"]:
            seq += 1
            key, nofile = _ledger_key(GAP, dict(entry), repo_paths)
            fresh_assertions.append(
                (_ledger_assertion(entry, seq, GAP), key, nofile)
            )
        stored_ids = set()
        stored_keys = set()
        stored_nofile: set[tuple] = set()
        for row in store.all("assertions"):
            stored_ids.add(row.get("id"))
            key, nofile = _ledger_key(
                row.get("predicate", ""),
                _as_json(row.get("object")) or {},
                repo_paths,
            )
            if key is not None:
                stored_keys.add(key)
                if (
                    nofile is not None
                    and len(key) > 3
                    and key[3] == "[REDACTED]"
                ):
                    stored_nofile.add(nofile)
        for assertion, key, nofile in fresh_assertions:
            if assertion.id in stored_ids:
                continue
            if key is not None and key in stored_keys:
                continue
            if nofile is not None and nofile in stored_nofile:
                continue
            store.add_assertion(assertion)
            stored_ids.add(assertion.id)
            if key is not None:
                stored_keys.add(key)
                if (
                    nofile is not None
                    and len(key) > 3
                    and key[3] == "[REDACTED]"
                ):
                    stored_nofile.add(nofile)

    return {
        "manifest": str(manifest_path),
        "db": str(db_path),
        "repos": [r["name"] for r in repos],
        "added": added,
        "unchanged": unchanged,
        "invalidated_roots": invalidated_roots,
        "cascaded_stale": cascaded,
        "abstentions": aggregate["abstentions"],
        "gaps": aggregate["gaps"],
    }


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
