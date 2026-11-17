"""Deterministic structural extractor (stdlib ``ast`` only).

Read-only analysis: the analyzed code is parsed, never imported or executed.
No network, no subprocess, no importlib. Comments are inert text and are never
interpreted as instructions. Same input produces the same graph.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from bo_store.manifest import verify_manifest

ORIGIN = "deterministic"

_EXCLUDE_DIRS = {"__pycache__", ".git", ".cgr", ".venv", "node_modules", ".tox"}


def _py_files(root):
    """Return sorted Python files under *root*; symlinks are never listed or followed."""
    root = Path(root)
    if root.is_file():
        return [] if root.is_symlink() else ([root] if root.suffix == ".py" else [])
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _EXCLUDE_DIRS
            and not d.startswith(".")
            and not (Path(dirpath) / d).is_symlink()
        )
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink():
                continue
            if name.endswith(".py"):
                found.append(p)
    return sorted(found)


def _dotted(node):
    """Return the dotted name for a Name/Attribute chain, else ''."""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _repo_of(root, path):
    """Return the top-level repo under *root* for *path*, else None.

    Uses resolve+relative_to so any path (symlink or otherwise) that escapes
    the workspace root is rejected (None) rather than silently ignored.
    """
    try:
        root_real = Path(root).resolve()
        path_real = Path(path).resolve()
    except (OSError, ValueError):
        return None
    try:
        rel = path_real.relative_to(root_real)
    except ValueError:
        return None
    parts = rel.parts
    return parts[0] if len(parts) > 1 else None


def _flow_label(class_name):
    for suffix in ("Service", "Policy", "Controller", "Repository"):
        if class_name.endswith(suffix) and len(class_name) > len(suffix):
            return class_name[: -len(suffix)]
    return class_name


def _constant_rule(stmt, class_name, file, step):
    """Build a Rule from a class-body constant assignment, else None."""
    if not isinstance(stmt, ast.Assign):
        return None
    if not isinstance(stmt.value, ast.Constant):
        return None
    value = stmt.value.value
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    for target in stmt.targets:
        if not isinstance(target, ast.Name) or target.id.startswith("_"):
            continue
        return {
            "name": target.id,
            "value": value,
            "symbol": f"{class_name}.{target.id}",
            "origin": ORIGIN,
            "file": str(file),
            "line": stmt.lineno,
            "evidence": {
                "file": str(file),
                "line": stmt.lineno,
                "excerpt": f"{target.id} = {value!r}",
            },
            "flow": None,
            "step": step,
            "confidence": 1.0,
            "basis": "stdlib ast class-attribute constant assignment",
        }
    return None


def _parse_file(path):
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    return ast.parse(src, filename=str(path))


def extract_file(path):
    """Extract (nodes, edges, rules) from one Python file using stdlib ast."""
    path = Path(path)
    file = str(path)
    try:
        tree = _parse_file(path)
    except (SyntaxError, ValueError):
        return [], [], []

    nodes = [{
        "kind": "module",
        "name": path.stem,
        "file": file,
        "line": 1,
        "origin": ORIGIN,
    }]
    edges = []
    rules = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.class_stack = []
            self.func_stack = []
            self.class_methods = {}

        def visit_ClassDef(self, node):
            parent = self.class_stack[-1] if self.class_stack else None
            symbol = f"{parent}.{node.name}" if parent else node.name
            nodes.append({
                "kind": "class",
                "name": node.name,
                "symbol": symbol,
                "file": file,
                "line": node.lineno,
                "parent": parent,
                "origin": ORIGIN,
            })
            edges.append({
                "kind": "contains",
                "source": parent or path.stem,
                "target": symbol,
                "file": file,
                "line": node.lineno,
                "origin": ORIGIN,
            })
            self.class_stack.append(node.name)
            self.class_methods[node.name] = {}
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node):
            cls = self.class_stack[-1] if self.class_stack else None
            kind = "method" if cls else "function"
            symbol = f"{cls}.{node.name}" if cls else node.name
            nodes.append({
                "kind": kind,
                "name": node.name,
                "symbol": symbol,
                "file": file,
                "line": node.lineno,
                "parent": cls,
                "origin": ORIGIN,
            })
            edges.append({
                "kind": "contains",
                "source": cls or path.stem,
                "target": symbol,
                "file": file,
                "line": node.lineno,
                "origin": ORIGIN,
            })
            refs = set()
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Attribute)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "self"):
                    refs.add(sub.attr)
            if cls:
                self.class_methods[cls][node.name] = refs
            self.func_stack.append(symbol)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_Call(self, node):
            name = _dotted(node.func)
            if name:
                caller = self.func_stack[-1] if self.func_stack else None
                nodes.append({
                    "kind": "call",
                    "name": name,
                    "file": file,
                    "line": node.lineno,
                    "parent": caller,
                    "origin": ORIGIN,
                })
                if caller:
                    edges.append({
                        "kind": "calls",
                        "source": caller,
                        "target": name,
                        "source_file": file,
                        "source_line": node.lineno,
                        "target_file": None,
                        "target_line": None,
                        "origin": ORIGIN,
                    })
            self.generic_visit(node)

    V().visit(tree)

    # Rules: class-body constant assignments (step annotation deferred;
    # the visitor already records per-method self refs in class_methods).
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                rule = _constant_rule(stmt, node.name, file, None)
                if rule is None:
                    continue
                rules.append(rule)

    return nodes, edges, rules


def extract_rule(path):
    """Return the single deterministic Rule found in *path*, else None."""
    _, _, rules = extract_file(path)
    return rules[0] if rules else None


def _symbol_index(nodes):
    index = {}
    for node in nodes:
        kind = node.get("kind")
        if kind not in ("class", "function", "method"):
            continue
        symbol = node.get("symbol") or node.get("name")
        if symbol and symbol not in index:
            index[symbol] = node
    return index


def extract(repo, manifest=None, workspace_root=None):
    """Extract (nodes, edges, rules) for a repo directory, deterministically.

    Fail-closed: if a *manifest* is supplied it is verified (workspace
    confinement, symlink/traversal rejection) before any extraction; on
    verification failure nothing is extracted.
    """
    root = Path(repo)
    if manifest is not None:
        manifest_path = manifest if isinstance(manifest, (str, os.PathLike)) else None
        ok, _errors = verify_manifest(
            manifest, workspace_root=workspace_root, manifest_path=manifest_path
        )
        if not ok:
            return [], [], []
    files = _py_files(root)
    nodes, edges, rules = [], [], []
    for path in files:
        f_nodes, f_edges, f_rules = extract_file(path)
        nodes.extend(f_nodes)
        edges.extend(f_edges)
        rules.extend(f_rules)

    index = _symbol_index(nodes)

    # Resolve call targets and emit cross-repo relation edges.
    relations = []
    for edge in edges:
        if edge.get("kind") != "calls":
            continue
        target = index.get(edge.get("target"))
        if target is not None:
            edge["target_file"] = target["file"]
            edge["target_line"] = target["line"]
            source_repo = _repo_of(root, edge.get("source_file"))
            target_repo = _repo_of(root, target["file"])
            if source_repo and target_repo and source_repo != target_repo:
                relation = {
                    "kind": "invokes",
                    "relation": "invokes",
                    "source": edge["source"],
                    "target": edge["target"],
                    "source_file": edge["source_file"],
                    "source_line": edge["source_line"],
                    "target_file": target["file"],
                    "target_line": target["line"],
                    "source_repo": source_repo,
                    "target_repo": target_repo,
                    "origin": ORIGIN,
                }
                relations.append(relation)

    # Annotate rules with the business flow derived from cross-repo invocations.
    for rule in rules:
        cls = str(rule.get("symbol", "")).split(".")[0]
        for rel in relations:
            if str(rel["target"]).split(".")[0] != cls:
                continue
            source_cls = str(rel["source"]).split(".")[0]
            rule["flow"] = f"{_flow_label(source_cls)}→{_flow_label(cls)}"
            break

    edges.extend(relations)
    return nodes, edges, rules


def abstain(path):
    """Return an abstain record when *path* yields no structural evidence."""
    root = Path(path)
    # Single walk: extract() already lists files; no second pass over the tree.
    nodes, edges, rules = extract(root)
    if not nodes and not edges and not rules:
        return {
            "status": "abstain",
            "reason": "no-evidence: no structural artifacts extracted",
            "origin": ORIGIN,
        }
    return {
        "status": "ok",
        "reason": "evidence",
        "origin": ORIGIN,
        "nodes": len(nodes),
        "edges": len(edges),
        "rules": len(rules),
    }
