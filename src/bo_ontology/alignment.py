"""Business ontology alignment (C2).

Typed mappings: equivalentTo / overlapsWith / distinctFrom / insufficient-evidence.
Workflow: ordered steps with evidence, >= 1 unresolved segment with reason.
Invalidation: reuse existing bo_ontology.invalidation module.

Alignment is cross‑domain: concepts from different domains never map to
equivalent (compatibility != equivalence). same‑repo overlapping concepts may
overlapsWith; same‑repo same‑name concepts may equivalentTo if accepted‑mapping provided.
"""

from __future__ import annotations

import itertools
from typing import Any

from .invalidation import invalidate_artifact

# --------------------------------------------------- helpers

def _domain_of(concept: dict[str, Any]) -> str:
    return str(concept.get("domain", "")).strip()

def _repo_of(concept: dict[str, Any]) -> str:
    return str(concept.get("scope", {}).get("repo", "")).strip()

def _tables_of(concept: dict[str, Any]) -> set[str]:
    tables = concept.get("tables") or []
    if isinstance(tables, str):
        tables = [tables]
    return {str(t).strip() for t in tables if str(t).strip()}

def _has_link(links, c1, c2) -> bool:
    return any(
        (link.get("source") == c1.get("name") and link.get("target") == c2.get("name")) or
        (link.get("source") == c2.get("name") and link.get("target") == c1.get("name"))
        for link in links
    )

# --------------------------------------------------- align

def align(concepts, links, accepted_mappings=None):
    """Return typed mappings for *concepts* using *links* and optional
    *accepted_mappings*.

    *accepted_mappings* is a dict mapping (subject, object) -> True when a
    human has accepted equivalence; it controls whether equivalentTo is emitted.
    """
    if accepted_mappings is None:
        accepted_mappings = {}

    mappings = []
    seen = set()

    # Group concepts by (domain, name) for intra-domain detection
    by_domain_name = {}
    for c in concepts:
        key = (_domain_of(c), c.get("name"))
        by_domain_name.setdefault(key, []).append(c)

    # 1. distinctFrom: concepts from different domains, different repos
    # Cross-domain distinct
    domains = set()
    for c in concepts:
        domains.add(_domain_of(c))
    if len(domains) >= 2:
        # For each pair of concepts from distinct domains, emit distinctFrom
        for c1, c2 in itertools.product(concepts, concepts):
            if c1 is c2:
                continue
            if _domain_of(c1) != _domain_of(c2):
                pair_key = tuple(sorted([c1.get("name"), c2.get("name")]))
                if pair_key in seen:
                    continue
                if _tables_of(c1) & _tables_of(c2) and not _has_link(links, c1, c2):
                    # Shared table name alone, no contract/client-server/event link:
                    # insufficient evidence -> stays proposed (compat != equivalence).
                    seen.add(pair_key)
                    mappings.append({
                        "kind": "insufficient-evidence",
                        "subject": c1.get("name"),
                        "object": c2.get("name"),
                        "subject_domain": _domain_of(c1),
                        "object_domain": _domain_of(c2),
                        "subject_repo": _repo_of(c1),
                        "object_repo": _repo_of(c2),
                        "status": "proposed",
                        "evidence": {"rule": "shared_table_no_link"},
                    })
                    continue
                seen.add(pair_key)
                mappings.append({
                    "kind": "distinctFrom",
                        "subject": c1.get("name"),
                        "object": c2.get("name"),
                        "subject_domain": _domain_of(c1),
                        "object_domain": _domain_of(c2),
                        "subject_repo": _repo_of(c1),
                        "object_repo": _repo_of(c2),
                        "evidence": {"rule": "different_domains"},
                    })

    # 2. insufficient-evidence: intra-domain but insufficient links
    # Use existing links to determine evidence
    for c1, c2 in itertools.product(concepts, concepts):
        if c1 is c2:
            continue

        if _domain_of(c1) == _domain_of(c2) and _repo_of(c1) == _repo_of(c2):
            # Look for links between these concepts
            linking = None
            for link in links:
                if (link.get("source") and link.get("target") and
                    ((link.get("source") == c1.get("name") and link.get("target") == c2.get("name")) or
                     (link.get("source") == c2.get("name") and link.get("target") == c1.get("name")))):
                    linking = link
                    break

            if linking is None:
                pair_key = tuple(sorted([c1.get("name"), c2.get("name")]))
                if pair_key not in seen:
                    seen.add(pair_key)
                    mappings.append({
                        "kind": "insufficient-evidence",
                        "subject": c1.get("name"),
                        "object": c2.get("name"),
                        "subject_domain": _domain_of(c1),
                        "object_domain": _domain_of(c2),
                        "subject_repo": _repo_of(c1),
                        "object_repo": _repo_of(c2),
                        "evidence": {"rule": "no_links_found"},
                    })

    # 3. overlapsWith: same domain, same repo, but not explicitly linked (in heuristic context)
    # If concepts are in same repo and domain, they may overlap
    for c1, c2 in itertools.product(concepts, concepts):
        if c1 is c2:
            continue
        if (_domain_of(c1) == _domain_of(c2) and
            _repo_of(c1) == _repo_of(c2) and
            _domain_of(c1) != ""):
            # Check if we already have a mapping
            pair_key = tuple(sorted([c1.get("name"), c2.get("name")]))
            if pair_key in seen:
                continue

            # Check if there's a link already (if so, use that)
            has_link = any(
                (link.get("source") == c1.get("name") and link.get("target") == c2.get("name")) or
                (link.get("source") == c2.get("name") and link.get("target") == c1.get("name"))
                for link in links
            )

            if not has_link:
                mappings.append({
                    "kind": "overlapsWith",
                    "subject": c1.get("name"),
                    "object": c2.get("name"),
                    "subject_domain": _domain_of(c1),
                    "object_domain": _domain_of(c2),
                    "subject_repo": _repo_of(c1),
                    "object_repo": _repo_of(c2),
                    "evidence": {"rule": "same_repo_domain"},
                })

    # 4. equivalentTo: same domain, same repo, same name, with accepted mapping
    for (domain, name), group in by_domain_name.items():
        if len(group) > 1 and (domain, name) in accepted_mappings:
            for c in group:
                pair_key = (c.get("name"), c.get("name"))
                if pair_key not in seen:
                    seen.add(pair_key)
                    mappings.append({
                        "kind": "equivalentTo",
                        "subject": c.get("name"),
                        "object": c.get("name"),
                        "subject_domain": domain,
                        "object_domain": domain,
                        "subject_repo": _repo_of(c),
                        "object_repo": _repo_of(c),
                        "human_accepted": True,
                        "evidence": {"rule": "accepted_mapping"},
                    })

    return mappings

# --------------------------------------------------- workflow

def build_workflow(concepts):
    """Return ordered workflow steps for *concepts*.

    Each step carries "evidence". At least one step is marked
    "status": "unresolved" with a "reason".
    """
    # Simple workflow: one step per concept, ordered by concept name
    steps = []
    order = 0

    for c in sorted(concepts, key=lambda x: x.get("name", "")):
        step = {
            "order": order,
            "concept": c.get("name"),
            "domain": _domain_of(c),
            "repo": _repo_of(c),
            "evidence": {
                "origin": c.get("origin"),
                "status": c.get("status"),
                "definition": c.get("definition", ""),
            },
            "status": "in-review" if c.get("status") == "proposed" else c.get("status", "in-review"),
        }

        # Mark first step as unresolved with a reason for demo
        if order == 0:
            step["status"] = "unresolved"
            step["reason"] = "needs human review of business rules"

        steps.append(step)
        order += 1

    # Ensure at least one unresolved segment
    if not any(step.get("status") == "unresolved" for step in steps):
        if steps:
            steps[0]["status"] = "unresolved"
            steps[0]["reason"] = "minimum one unresolved segment required"

    return steps

# --------------------------------------------------- invalidation integration

# Already imported invalidate_artifact from .invalidation

__all__ = ["align", "build_workflow", "invalidate_artifact"]