"""Deterministic structural extractor (stdlib ``ast`` only).

Read-only analysis: the analyzed code is parsed, never imported or executed.
No network, no subprocess, no importlib. Comments are inert text and are never
interpreted as instructions. Same input produces the same graph.
"""

from __future__ import annotations

import ast
import os
import re
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


# --------------------------------------------------------------------------
# Deep deterministic ground truth (M2 T4): rules / transitions / event phases.
# Structural ast patterns only; fixture code is parsed, never executed.
# --------------------------------------------------------------------------

_STATE_LITERAL_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_PUBLICATION_HEADS = frozenset({"publish", "emit", "dispatch", "send", "broadcast", "fire"})
_CONSUMPTION_PREFIXES = ("on_", "handle_")
_COMPARE_OPS = {
    ast.LtE: "<=", ast.Lt: "<", ast.GtE: ">=", ast.Gt: ">",
    ast.Eq: "==", ast.NotEq: "!=", ast.Is: "is", ast.IsNot: "is not",
    ast.In: "in", ast.NotIn: "not in",
}


def _module_constants(tree):
    """Module-level ``NAME = <constant>`` assignments, for symbol resolution."""
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value
    return consts


def _functions_with_symbols(tree):
    """Yield (function_node, qualified_symbol) for module functions and methods."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, node.name
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield m, f"{node.name}.{m.name}"


def _const_return(stmts):
    """Return (bool_value, line) of the first ``return <bool constant>``, else (None, None)."""
    for st in stmts:
        if (isinstance(st, ast.Return) and isinstance(st.value, ast.Constant)
                and isinstance(st.value.value, bool)):
            return st.value.value, st.lineno
    return None, None


def _operand(node, local_assigns, module_consts, self_const=None):
    """Structured, non-executed description of a predicate operand."""
    if isinstance(node, ast.Constant):
        return {"value": node.value}
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)):
        info = {"source": ast.unparse(node.args[0]), "attribute": node.args[1].value}
        if len(node.args) > 2:
            info["default"] = _operand(node.args[2], {}, module_consts)
        return info
    if isinstance(node, ast.Name):
        if node.id in local_assigns:
            # local alias: resolve to its assigned expression (no re-scan: cycle-safe)
            return _operand(local_assigns[node.id], {}, module_consts)
        if node.id in module_consts:
            return {"symbol": node.id, "value": module_consts[node.id]}
        return {"name": node.id}
    if self_const is not None and isinstance(node, (ast.Name, ast.Attribute)):
        name, value = self_const
        if ast.unparse(node) in (name, f"self.{name}"):
            return {"symbol": f"self.{name}", "value": value}
    return {"expression": ast.unparse(node)}


def _fmt_operand(opd):
    """Deterministic text rendering of an operand descriptor."""
    if "value" in opd:
        return repr(opd["value"])
    if "attribute" in opd:
        base = f"{opd['source']}.{opd['attribute']}"
        if "default" in opd:
            base += f" (default {_fmt_operand(opd['default'])})"
        return base
    for key in ("symbol", "expression", "name"):
        if key in opd:
            return str(opd[key])
    return "unknown"


def _decision_fields(fn, cls_name, file, module_consts, self_const=None):
    """Structured decision fields for a method with a comparison predicate.

    Returns None when the method is not a boolean decision (no top-level
    if-guard carrying a comparison with boolean constant outcomes).
    """
    body = fn.body
    local_assigns = {}
    for st in body:
        if (isinstance(st, ast.Assign) and len(st.targets) == 1
                and isinstance(st.targets[0], ast.Name)):
            local_assigns[st.targets[0].id] = st.value
    main_if = None
    for st in body:
        if isinstance(st, ast.If) and any(isinstance(n, ast.Compare) for n in ast.walk(st.test)):
            main_if = st
            break
    if main_if is None:
        return None
    compare = next(n for n in ast.walk(main_if.test) if isinstance(n, ast.Compare))
    tv, tv_line = _const_return(main_if.body)
    fv, fv_line = _const_return(main_if.orelse)
    if fv is None:
        fv, fv_line = _const_return(body[body.index(main_if) + 1:])
    if tv is None and fv is None:
        return None
    op = _COMPARE_OPS.get(type(compare.ops[0]), type(compare.ops[0]).__name__)
    left = _operand(compare.left, local_assigns, module_consts, self_const)
    right = _operand(compare.comparators[0], local_assigns, module_consts, self_const)

    eligibility_shaped = "eligib" in fn.name.lower()
    outcomes = []
    if tv is not None:
        entry = {"when": "predicate true", "value": tv, "evidence": {"file": file, "line": tv_line}}
        if eligibility_shaped:
            entry["label"] = "eligible" if tv else "ineligible"
        outcomes.append(entry)
    if fv is not None:
        entry = {"when": "predicate false", "value": fv, "evidence": {"file": file, "line": fv_line}}
        if eligibility_shaped:
            entry["label"] = "eligible" if fv else "ineligible"
        outcomes.append(entry)

    exceptions = []
    for st in body:
        if isinstance(st, ast.If) and st is not main_if:
            ev, ev_line = _const_return(st.body)
            if ev is None:
                continue
            requires = {n.id for n in ast.walk(st.test) if isinstance(n, ast.Name)}
            requires |= {
                f"{_dotted(c.func.value)}.{c.args[0].value}"
                for c in ast.walk(st.test)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and _dotted(c.func.value) and c.args
                and isinstance(c.args[0], ast.Constant)
                and isinstance(c.args[0].value, str)
            }
            exceptions.append({
                "condition": ast.unparse(st.test),
                "outcome": ev,
                "requires": sorted(requires),
                "evidence": {"file": file, "line": st.lineno},
            })

    params = [a.arg for a in fn.args.args if a.arg != "self"]
    params += [a.arg for a in fn.args.kwonlyargs]

    predicate = {
        "expression": ast.unparse(compare),
        "left": left,
        "op": op,
        "right": right,
        "evidence": {"file": file, "line": compare.lineno},
    }
    statement = (
        f"{cls_name}.{fn.name}: if {_fmt_operand(left)} {op} {_fmt_operand(right)} "
        f"then return {tv!r}; otherwise return {fv!r}."
    )
    return {
        "statement": statement,
        "preconditions": [],  # no guard dominates the predicate comparison here
        "predicate": predicate,
        "params": params,
        "exceptions": exceptions,
        "outcomes": outcomes,
        "enforcement_location": {
            "file": file,
            "line": main_if.lineno,
            "symbol": f"{cls_name}.{fn.name}",
        },
        "supporting": [
            {"file": file, "line": main_if.lineno, "excerpt": ast.unparse(main_if.test)},
        ],
        "conflicting": [],
        "limitations": [
            "structural ast analysis; the analyzed code is parsed, never executed",
            "operand values resolved statically from getattr defaults, locals and module constants",
        ],
    }


def _deepen_constant_rules(trees, rules):
    """Attach deep ground-truth fields to class-constant rules via the
    methods in the same class that reference the constant (symbol resolution,
    never hardcoded fixture names)."""
    for rule in rules:
        if "predicate" in rule:
            continue
        symbol = str(rule.get("symbol", ""))
        if not symbol or "." not in symbol:
            continue
        cls_name, const_name = symbol.split(".", 1)
        tree = trees.get(str(rule.get("file")))
        if tree is None:
            continue
        module_consts = _module_constants(tree)
        for cdef in tree.body:
            if not (isinstance(cdef, ast.ClassDef) and cdef.name == cls_name):
                continue
            for m in cdef.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                refs = any(
                    (isinstance(n, ast.Name) and n.id == const_name)
                    or (isinstance(n, ast.Attribute) and n.attr == const_name)
                    for n in ast.walk(m)
                )
                if not refs:
                    continue
                fields = _decision_fields(
                    m, cls_name, str(rule["file"]), module_consts,
                    self_const=(const_name, rule.get("value")),
                )
                if fields is None:
                    continue
                rule.update(fields)
                rule["statement"] = (
                    f"{symbol} = {rule.get('value')!r}; enforced: {fields['statement']}"
                )
                rule["supporting"] = (
                    [{"file": rule["file"], "line": rule["line"],
                      "excerpt": f"{const_name} = {rule.get('value')!r}"}]
                    + fields["supporting"]
                )
                break


def _decision_rules(trees):
    """Rules for boolean decision methods (comparison predicate + constant
    boolean outcomes), independent of any class-constant declaration."""
    out = []
    for file, tree in trees.items():
        module_consts = _module_constants(tree)
        for cdef in tree.body:
            if not isinstance(cdef, ast.ClassDef):
                continue
            for m in cdef.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                fields = _decision_fields(m, cdef.name, file, module_consts)
                if fields is None:
                    continue
                out.append({
                    "name": f"{cdef.name}.{m.name}",
                    "symbol": f"{cdef.name}.{m.name}",
                    "origin": ORIGIN,
                    "file": file,
                    "line": m.lineno,
                    "evidence": {"file": file, "line": m.lineno, "excerpt": f"def {m.name}"},
                    "flow": None,
                    "step": None,
                    "confidence": 1.0,
                    "basis": "stdlib ast boolean decision method (comparison predicate with constant boolean outcomes)",
                    **fields,
                })
    return out


def _transition_edges(trees, index):
    """Detect guarded state transitions: a resolved method call carrying an
    UPPER_SNAKE state literal. The receiver class is resolved through the
    symbol index (chained constructor, local constructor variable, or plain
    dotted name) — never via hardcoded fixture names."""
    edges = []
    def_lookup = {}
    for file, tree in trees.items():
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        def_lookup[(node.name, m.name)] = m
    for file, tree in trees.items():
        for fn, caller in _functions_with_symbols(tree):
            local_ctors = {}
            for st in ast.walk(fn):
                if (isinstance(st, ast.Assign) and isinstance(st.value, ast.Call)
                        and isinstance(st.value.func, ast.Name)
                        and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)):
                    local_ctors[st.targets[0].id] = st.value.func.id
            calls = sorted(
                (n for n in ast.walk(fn) if isinstance(n, ast.Call)),
                key=lambda n: n.lineno,
            )
            guards = [
                {"expression": ast.unparse(g.test), "line": g.lineno}
                for g in sorted(
                    (n for n in ast.walk(fn) if isinstance(n, ast.If)),
                    key=lambda n: n.lineno,
                )
            ]
            for call in calls:
                if not isinstance(call.func, ast.Attribute):
                    continue
                receiver = None
                if isinstance(call.func.value, ast.Name):
                    receiver = local_ctors.get(call.func.value.id)
                    if receiver is None and call.func.value.id in index:
                        receiver = call.func.value.id
                elif (isinstance(call.func.value, ast.Call)
                        and isinstance(call.func.value.func, ast.Name)):
                    receiver = call.func.value.func.id
                candidate = f"{receiver}.{call.func.attr}" if receiver else None
                if candidate is None or candidate not in index:
                    continue
                state_values = [
                    a.value
                    for a in list(call.args) + [k.value for k in call.keywords]
                    if isinstance(a, ast.Constant)
                    and isinstance(a.value, str)
                    and _STATE_LITERAL_RE.match(a.value)
                ]
                if not state_values:
                    continue
                pre = [g for g in guards if g["line"] < call.lineno]
                method = def_lookup.get((candidate.split(".")[0], call.func.attr))
                source_expr = None
                if method is not None:
                    for st in ast.walk(method):
                        if (isinstance(st, ast.Assign) and st.targets
                                and isinstance(st.targets[0], ast.Attribute)):
                            source_expr = ast.unparse(st.targets[0])
                            break
                edges.append({
                    "kind": "transition",
                    "source": {"state": None, "expression": source_expr, "unknown": True},
                    "target": state_values[0],
                    "guard": {
                        "conditions": pre,
                        "evidence": {"file": file, "lines": [g["line"] for g in pre]},
                    },
                    "trigger": {
                        "symbol": candidate,
                        "caller": caller,
                        "file": file,
                        "line": call.lineno,
                    },
                    "evidence": {"file": file, "line": call.lineno, "excerpt": ast.unparse(call)},
                    "unknown_source": True,
                    "conflicting": [],
                    "limitations": [
                        "source state value is not statically recoverable; recorded as explicit None",
                        "guard conditions are the if-guards lexically preceding the transition call in the enclosing function",
                    ],
                    "origin": ORIGIN,
                })
    return edges


def _event_records(trees):
    """Event lifecycle records (C2): declaration/construction found when the
    structural sites exist; publication/consumation abstain explicitly rather
    than inferring coverage. Construction of an event object is never
    publication."""
    nodes = []
    declared = {}
    for file, tree in trees.items():
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == "__init__"
                for m in node.body
            ):
                declared[node.name] = (file, node.lineno)
                nodes.append({
                    "kind": "event",
                    "phase": "declaration",
                    "name": node.name,
                    "file": file,
                    "line": node.lineno,
                    "evidence": {"file": file, "line": node.lineno, "excerpt": f"class {node.name}"},
                    "origin": ORIGIN,
                    "limitations": [
                        "event-like declaration inferred structurally from the presence of __init__",
                        "class purpose is not semantically verified",
                    ],
                })
    constructions = []
    for file, tree in trees.items():
        for fn, symbol in _functions_with_symbols(tree):
            for call in ast.walk(fn):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in declared):
                    constructions.append({
                        "kind": "event",
                        "phase": "construction",
                        "name": symbol,
                        "event": call.func.id,
                        "file": file,
                        "line": call.lineno,
                        "evidence": {"file": file, "line": call.lineno, "excerpt": ast.unparse(call)},
                        "origin": ORIGIN,
                        "limitations": [
                            "construction site only; constructing an event object is not publication (C2)",
                        ],
                    })
    nodes.extend(constructions)

    publication_sites = []
    consumption_sites = []
    for file, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                head = _dotted(node.func).split(".")[0].lower()
                if head in _PUBLICATION_HEADS:
                    publication_sites.append((file, node.lineno, _dotted(node.func)))
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith(_CONSUMPTION_PREFIXES)):
                consumption_sites.append((file, node.lineno, node.name))
    for file, line, name in publication_sites:
        nodes.append({
            "kind": "event", "phase": "publication", "status": "found", "name": name,
            "file": file, "line": line,
            "evidence": {"file": file, "line": line, "excerpt": name},
            "origin": ORIGIN,
            "limitations": ["publication site inferred from publish/emit/dispatch-style call head"],
        })
    for file, line, name in consumption_sites:
        nodes.append({
            "kind": "event", "phase": "consumption", "status": "found", "name": name,
            "file": file, "line": line,
            "evidence": {"file": file, "line": line, "excerpt": name},
            "origin": ORIGIN,
            "limitations": ["consumption site inferred from on_*/handle_* handler naming convention"],
        })
    decl_anchor = next(iter(declared.values()), None)
    if not publication_sites:
        anchor = constructions[0] if constructions else None
        evidence = (
            {"file": anchor["file"], "line": anchor["line"]}
            if anchor is not None
            else ({"file": decl_anchor[0], "line": decl_anchor[1]} if decl_anchor else None)
        )
        nodes.append({
            "kind": "event", "phase": "publication", "status": "abstain",
            "reason": "no publication call sites found; event construction is not publication (C2)",
            "evidence": evidence,
            "origin": ORIGIN,
            "limitations": [
                "absence assertion is scoped to the files extracted in this run",
                "abstain is explicit: no publication surface is asserted from construction evidence",
            ],
        })
    if not consumption_sites:
        evidence = (
            {"file": decl_anchor[0], "line": decl_anchor[1]} if decl_anchor else None
        )
        nodes.append({
            "kind": "event", "phase": "consumption", "status": "abstain",
            "reason": "no consumption sites found (no on_*/handle_* handler names in the extracted files)",
            "evidence": evidence,
            "origin": ORIGIN,
            "limitations": [
                "absence assertion is scoped to the files extracted in this run",
                "handler naming convention (on_*/handle_*) is the only structural consumption signal",
            ],
        })
    return nodes


def _deep_ground_truth(files, rules, index):
    """Per-category deep ground truth pass (C4): deepens rule records and
    produces transition edges and event-phase records from the same files."""
    trees = {}
    for path in files:
        try:
            trees[str(path)] = _parse_file(path)
        except (SyntaxError, ValueError):
            continue
    if not trees:
        # Fail closed: nothing was extracted, so no phase records either
        # (publication/consumption abstains are meaningless without files).
        return [], []
    extra_nodes = _event_records(trees)
    extra_edges = _transition_edges(trees, index)
    _deepen_constant_rules(trees, rules)
    rules.extend(_decision_rules(trees))
    return extra_nodes, extra_edges


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
    deep_nodes, transition_edges = _deep_ground_truth(files, rules, index)

    # Resolve call targets; emit cross-repo relation edges only between
    # qualified methods (C5: business relations are method-level; bare class
    # or function targets stay plain call evidence, never ontology relations).
    relations = []
    for edge in edges:
        if edge.get("kind") != "calls":
            continue
        target = index.get(edge.get("target"))
        if target is not None:
            edge["target_file"] = target["file"]
            edge["target_line"] = target["line"]
            source_node = index.get(edge.get("source"))
            if (target.get("kind") != "method"
                    or source_node is None
                    or source_node.get("kind") != "method"):
                continue
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
    edges.extend(transition_edges)
    nodes.extend(deep_nodes)
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
