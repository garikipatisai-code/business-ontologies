"""Heuristic concept discovery (C1).

Clusters extractor symbols into candidate BusinessConcepts using transparent
structural heuristics only: shared name affixes, shared parameters, shared
constant values, caller->callee coupling and file co-location. No semantics
are consulted and nothing is held out.

Everything emitted is origin=heuristic, status=proposed. The module abstains
(empty concepts) when no cluster reaches minimum support of two members;
single-member concepts are never invented.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from bo_extract.deterministic import extract

ORIGIN = "heuristic"
STATUS = "proposed"
MIN_SUPPORT = 2
_SKIP_NAMES = {"self"}


def _words(name):
    """Lowercase words of a CamelCase/snake_case symbol name."""
    return [p.lower() for p in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|\d+|[A-Z]+", name.replace("_", " "))]


def _cluster(heuristic, basis, members):
    return {"heuristic": heuristic, "basis": basis, "members": members}


def _index_files(files):
    """One ast pass per file: method/function parameters + simple constants."""
    params = {}
    constants = {}
    for file in files:
        try:
            tree = ast.parse(Path(file).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue

        def visit(node, cls=None):
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sym = f"{cls}.{stmt.name}" if cls else stmt.name
                    args = [a.arg for a in stmt.args.args if a.arg not in _SKIP_NAMES]
                    args += [a.arg for a in stmt.args.kwonlyargs]
                    params[sym] = args
                elif (isinstance(stmt, ast.Assign)
                      and isinstance(stmt.value, ast.Constant)
                      and not isinstance(stmt.value.value, bool)):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and not target.id.startswith("_"):
                            sym = f"{cls}.{target.id}" if cls else target.id
                            constants.setdefault(stmt.value.value, []).append((sym, file))
                elif isinstance(stmt, ast.ClassDef):
                    visit(stmt, cls=stmt.name)

        visit(tree)
    return params, constants


def _prefix_clusters(symbols):
    """Symbols whose names share a leading name-affix (CamelCase words)."""
    groups = {}
    for s in symbols:
        base = s["name"].split(".")[-1] if s["kind"] == "method" else s["name"]
        ws = _words(base)
        for k in range(1, len(ws) + 1):
            groups.setdefault(tuple(ws[:k]), []).append(s)
    return [_cluster("name-prefix", " ".join(p), m) for p, m in groups.items() if len(m) >= MIN_SUPPORT]


def _param_clusters(symbols, params):
    """Methods/functions sharing a parameter name."""
    groups = {}
    for s in symbols:
        if s["kind"] not in ("method", "function"):
            continue
        for p in params.get(s["symbol"], []):
            groups.setdefault(p, []).append(s)
    return [_cluster("shared-param", p, m) for p, m in groups.items() if len(m) >= MIN_SUPPORT]


def _constant_clusters(constants):
    """Simple constants sharing a value, named by their shared name words."""
    out = []
    for value, members in constants.items():
        if len(members) < MIN_SUPPORT:
            continue
        shared = set(_words(members[0][0]))
        for sym, _ in members[1:]:
            shared &= set(_words(sym))
        basis = " ".join(sorted(shared)) if shared else "constant"
        out.append(_cluster("shared-constant", basis,
                            [{"symbol": sym, "kind": "constant", "file": file} for sym, file in members]))
    return out


def _call_coupling_clusters(edges, symbols):
    """Caller->callee pairs whose symbols share a name word."""
    by_symbol = {s["symbol"]: s for s in symbols}
    clusters = []
    seen = set()
    for e in edges:
        if e.get("kind") != "calls":
            continue
        src, tgt = e.get("source"), e.get("target")
        if not src or not tgt:
            continue
        ss, tt = by_symbol.get(src), by_symbol.get(tgt)
        if ss is None or tt is None:
            continue
        if (src, tgt) in seen:
            continue
        seen.add((src, tgt))
        sw = set(_words(ss["name"].split(".")[-1] if ss["kind"] == "method" else ss["name"]))
        tw = set(_words(tt["name"].split(".")[-1] if tt["kind"] == "method" else tt["name"]))
        shared = sw & tw
        if not shared:
            continue
        clusters.append(_cluster("call-coupling", " ".join(sorted(shared)), [ss, tt]))
    return clusters


def _file_clusters(symbols):
    """Symbols co-located in one file."""
    groups = {}
    for s in symbols:
        groups.setdefault(s.get("file"), []).append(s)
    return [_cluster("file-co-location", Path(file).stem, m)
            for file, m in groups.items() if file and len(m) >= MIN_SUPPORT]


def _build_concept(repo, cluster, name):
    members = cluster["members"]
    described = ", ".join(f"{m['kind']} {m['symbol']}"
                          for m in sorted(members, key=lambda m: m["symbol"]))
    definition = (
        f"Heuristic hypothesis: {name} is a proposed business concept grouping "
        f"{len(members)} members ({described}), clustered by {cluster['heuristic']} "
        f"on shared {cluster['basis']}; inferred from code structure, not business truth."
    )
    return {
        "name": name,
        "origin": ORIGIN,
        "status": STATUS,
        "definition": definition,
        "scope": {"repo": repo,
                  "files": sorted({m.get("file") for m in members if m.get("file")})},
        "members": [{"symbol": m["symbol"], "kind": m["kind"], "file": m.get("file")}
                    for m in members],
        "support": len(members),
        "heuristic": cluster["heuristic"],
        "basis": cluster["basis"],
    }


def _attach_rules(concepts, rules, out):
    """Reuse extractor decision-rule records (predicate/exceptions/outcomes kept)."""
    for concept in concepts:
        classes = {m["symbol"] for m in concept["members"] if m["kind"] == "class"}
        for rule in rules:
            if "predicate" not in rule:
                continue
            cls = str(rule.get("symbol", "")).split(".")[0]
            if cls in classes:
                copied = dict(rule)
                copied["origin"] = ORIGIN
                copied["status"] = STATUS
                copied["concept"] = concept["name"]
                out.append(copied)


def _attach_links(symbols, edges, nodes, concepts, out):
    """concept->operation (member methods) and concept->state/event links."""
    used = set()
    class_of = {c["name"]: {m["symbol"] for m in c["members"] if m["kind"] == "class"}
                for c in concepts}

    def add(kind, source, target, evidence):
        key = (kind, source, str(target))
        if key in used:
            return
        used.add(key)
        out.append({"origin": ORIGIN, "status": STATUS, "kind": kind,
                    "source": source, "target": target, "evidence": evidence})

    # concept -> operation: methods of the concept's member classes
    for concept in concepts:
        classes = class_of[concept["name"]]
        for s in symbols:
            if s["kind"] == "method" and s["symbol"].split(".")[0] in classes:
                add("concept→operation", concept["name"], s["symbol"],
                    {"file": s.get("file"), "line": s.get("line"),
                     "excerpt": f"def {s['name'].split('.')[-1]}"})

    # concept -> state: transition records whose trigger references a member class
    for e in edges:
        if e.get("kind") != "transition":
            continue
        caller = (e.get("trigger") or {}).get("caller", "")
        cls = caller.split(".")[0] if caller else ""
        evidence = dict(e.get("evidence", {}))
        evidence["trigger"] = e.get("trigger")
        for concept in concepts:
            if cls in class_of[concept["name"]]:
                add("concept→state", concept["name"], e.get("target"), evidence)
                break

    # concept -> event: event records referencing a member class
    for n in nodes:
        if n.get("kind") != "event" or not n.get("name"):
            continue
        for concept in concepts:
            if n["name"] in class_of[concept["name"]]:
                add("concept→event", concept["name"], n["name"], dict(n.get("evidence", {})))
                break


def discover(repo_paths):
    """Discover proposed heuristic concepts for *repo_paths*.

    Returns {"concepts", "rules", "links", "origin", "status", "abstain", "reason"}.
    """
    concepts = []
    rules_out = []
    links_out = []
    used_names = set()

    for path in repo_paths:
        try:
            nodes, edges, rules = extract(path)
        except (OSError, SyntaxError):
            continue
        repo = Path(path).name
        symbols = [s for s in nodes if s.get("kind") in ("class", "method", "function")]
        files = sorted({s.get("file") for s in symbols if s.get("file")})
        params, constants = _index_files(files)

        clusters = []
        clusters.extend(_prefix_clusters(symbols))
        clusters.extend(_param_clusters(symbols, params))
        clusters.extend(_constant_clusters(constants))
        clusters.extend(_call_coupling_clusters(edges, symbols))
        clusters.extend(_file_clusters(symbols))

        seen_sets, unique = set(), []
        for c in clusters:
            key = frozenset((m["symbol"], m["kind"]) for m in c["members"])
            if key in seen_sets:
                continue
            seen_sets.add(key)
            unique.append(c)

        repo_concepts = []
        for c in unique:
            if len(c["members"]) < MIN_SUPPORT:
                continue
            base = " ".join(part.capitalize() for part in c["basis"].split())
            name = base
            if name in used_names:
                name = f"{base} ({c['heuristic']})"
            k = 1
            while name in used_names:
                k += 1
                name = f"{base} ({c['heuristic']}) {k}"
            used_names.add(name)
            repo_concepts.append(_build_concept(repo, c, name))

        _attach_rules(repo_concepts, rules, rules_out)
        _attach_links(symbols, edges, nodes, repo_concepts, links_out)
        concepts.extend(repo_concepts)

    if not concepts:
        return {"concepts": [], "rules": [], "links": [],
                "origin": ORIGIN, "status": STATUS, "abstain": True,
                "reason": "abstain: no heuristic cluster reached minimum support (>=2 members)"}
    return {"concepts": concepts, "rules": rules_out, "links": links_out,
            "origin": ORIGIN, "status": STATUS, "abstain": False, "reason": None}
