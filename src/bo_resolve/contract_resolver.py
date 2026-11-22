"""Contract resolver: static call / event-schema / shared-table links.

Parses (never imports or executes) Python files under ``repo_paths`` and
emits three link lists: ``call_edges``, ``event_schema_links``,
``shared_table_links``.

Compatibility contract (frozen, compatibility != equivalence):
  * every link carries ``compatibility`` = {"compatible": True, "equivalent": False}
    — compatible-not-equivalent by default; a shared table, matching event
    schema, or call edge never implies equivalence;
  * no link is ever ``equivalentTo``/``equivalent`` = True;
  * an ``equivalent`` mapping would require ``human_accepted`` is True
    (never emitted here — no human-accepted mapping fixture exists);
  * static invocation only: dynamic dispatch (``getattr`` on a variable name,
    e.g. repo-e ``CreditGatewayClient.call``) abstains and yields no edge.
"""

from __future__ import annotations

import ast
from pathlib import Path

__all__ = ["resolve"]

_DEFAULT_COMPATIBILITY = {"compatible": True, "equivalent": False}


def _py_files(repo_paths):
    """Sorted, de-duplicated Python files under *repo_paths* (dirs or files)."""
    found = []
    seen = set()
    for repo in repo_paths:
        root = Path(repo)
        candidates = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in candidates:
            if path.is_symlink() or path.suffix != ".py":
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            found.append(path)
    return found


def _dotted(node):
    """Dotted name for an Attribute/Name chain, else ''."""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _constant_dict_assign(tree, name):
    """Assignment node for module-level ``NAME = {..}`` with a Dict value, else None."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Dict)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        ):
            return node
    return None


def _dict_str_items(assign_node):
    """[(str_key, str_value)] for a Dict with string-constant keys and values."""
    items = []
    for k, v in zip(assign_node.keys, assign_node.values):
        if (
            isinstance(k, ast.Constant)
            and isinstance(k.value, str)
            and isinstance(v, ast.Constant)
            and isinstance(v.value, str)
        ):
            items.append((k.value, v.value))
    return items


def resolve(repo_paths):
    """Resolve contract links for *repo_paths*.

    Returns {"call_edges": [...], "event_schema_links": [...],
    "shared_table_links": [...]} — all lists. Every link carries a
    compatibility flag defaulting to compatible-not-equivalent.
    """
    call_edges = []
    event_schema_links = []
    shared_table_links = []

    for path in _py_files(repo_paths):
        file = str(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=file)
        except (OSError, SyntaxError, ValueError):
            continue

        # Static calls only: attribute calls (x.f(...)). A call through a bare
        # name (e.g. ``method(payload)`` resolved via ``getattr(target, name)``)
        # is dynamic dispatch -> abstain, no edge.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                callee = _dotted(node.func)
                if not callee:
                    continue
                call_edges.append({
                    "kind": "call",
                    "source": file,
                    "target": callee,
                    "file": file,
                    "line": node.lineno,
                    "compatibility": dict(_DEFAULT_COMPATIBILITY),
                })

        # Event schema: EVENT_SCHEMA = {"<event>": {...}, ...}
        assign = _constant_dict_assign(tree, "EVENT_SCHEMA")
        if assign is not None:
            for event_name, _ in _dict_str_items(assign.value):
                event_schema_links.append({
                    "kind": "event_schema",
                    "source": f"{file}:EVENT_SCHEMA",
                    "target": event_name,
                    "file": file,
                    "line": assign.lineno,
                    "compatibility": dict(_DEFAULT_COMPATIBILITY),
                })

        # Shared tables: SHARED_TABLES = {"<alias>": "<table>", ...}
        assign = _constant_dict_assign(tree, "SHARED_TABLES")
        if assign is not None:
            for alias, table in _dict_str_items(assign.value):
                shared_table_links.append({
                    "kind": "shared_table",
                    "source": f"{file}:SHARED_TABLES",
                    "target": table,
                    "alias": alias,
                    "file": file,
                    "line": assign.lineno,
                    "compatibility": dict(_DEFAULT_COMPATIBILITY),
                })

    return {
        "call_edges": call_edges,
        "event_schema_links": event_schema_links,
        "shared_table_links": shared_table_links,
    }
