"""Deterministic structural extractor (stdlib ``ast`` only).

Read-only analysis: the analyzed code is parsed, never imported or executed.
No network, no subprocess, no importlib. Comments are inert text and are never
interpreted as instructions. Same input produces the same graph.
"""

from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
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


def _constant_rule(stmt, class_name, file):
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
                rule = _constant_rule(stmt, node.name, file)
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
# Structural ast patterns only; analyzed code is parsed, never executed.
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


def _module_constant_entries(tree):
    """Module-level ``NAME = <constant>`` assignments as (name, value, lineno)."""
    entries = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    entries.append((t.id, node.value.value, node.lineno))
    return entries


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
    """Return (outcome, line) of the first ``return <bool|None constant>``,
    else (None, None). A ``None`` outcome is a resolved deny, not an absence."""
    for st in stmts:
        if (isinstance(st, ast.Return) and isinstance(st.value, ast.Constant)
                and (isinstance(st.value.value, bool) or st.value.value is None)):
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


def _predicate_fields(test, file, local_assigns, module_consts=None, self_const=None):
    """Structured predicate (left op right) from a boolean guard test, else None.

    Comparison guards yield ``left op right`` via the operand helper; negation
    guards yield the negated operand. Unresolvable shapes abstain (None).
    """
    compare = next((n for n in ast.walk(test) if isinstance(n, ast.Compare)), None)
    if compare is not None:
        op = _COMPARE_OPS.get(type(compare.ops[0]), type(compare.ops[0]).__name__)
        left = _operand(compare.left, local_assigns, module_consts, self_const)
        right = _operand(compare.comparators[0], local_assigns, module_consts, self_const)
        return {
            "expression": ast.unparse(compare),
            "left": left,
            "op": op,
            "right": right,
            "evidence": {"file": file, "line": compare.lineno},
        }
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        operand = _operand(test.operand, local_assigns, module_consts, self_const)
        return {
            "expression": ast.unparse(test),
            "left": operand,
            "op": "not",
            "right": operand,
            "evidence": {"file": file, "line": getattr(test, "lineno", 0)},
        }
    return None


def _decision_fields(fn, cls_name, file, module_consts, self_const=None):
    """Structured decision fields for a method guarded by a boolean predicate.

    Returns None when no guard carries a resolvable boolean predicate with a
    terminal outcome; abstains rather than emitting an empty predicate.
    """
    body = fn.body
    local_assigns = {}
    for st in ast.walk(fn):
        if (isinstance(st, ast.Assign) and len(st.targets) == 1
                and isinstance(st.targets[0], ast.Name)):
            local_assigns[st.targets[0].id] = st.value
    guards = sorted(
        (n for n in ast.walk(fn) if isinstance(n, ast.If)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    main_if = next(
        (g for g in guards if any(isinstance(n, ast.Compare) for n in ast.walk(g.test))),
        None,
    )
    if main_if is None:
        main_if = next(
            (g for g in guards if isinstance(g.test, (ast.BoolOp, ast.UnaryOp))),
            None,
        )
    if main_if is None:
        return None
    predicate = _predicate_fields(main_if.test, file, local_assigns, module_consts, self_const)
    if predicate is None:
        return None
    tv, tv_line = _const_return(main_if.body)
    fv, fv_line = _const_return(main_if.orelse)
    main_index = next((i for i, st in enumerate(body) if st is main_if), len(body))
    if fv_line is None:
        fv, fv_line = _const_return(body[main_index + 1:])
    if tv_line is None and fv_line is None:
        return None
    op = predicate["op"]
    left = predicate["left"]
    right = predicate["right"]

    eligibility_shaped = "eligib" in fn.name.lower()
    outcomes = []
    if tv_line is not None:
        entry = {"when": "predicate true", "value": tv, "evidence": {"file": file, "line": tv_line}}
        if eligibility_shaped:
            entry["label"] = "eligible" if tv else "ineligible"
        outcomes.append(entry)
    if fv_line is not None:
        entry = {"when": "predicate false", "value": fv, "evidence": {"file": file, "line": fv_line}}
        if eligibility_shaped:
            entry["label"] = "eligible" if fv else "ineligible"
        outcomes.append(entry)

    exceptions = []
    for st in guards:
        if st is main_if:
            continue
        ev, ev_line = _const_return(st.body)
        if ev_line is None:
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

    if tv_line is not None and fv_line is not None:
        statement = (
            f"{cls_name}.{fn.name}: if {_fmt_operand(left)} {op} {_fmt_operand(right)} "
            f"then return {tv!r}; otherwise return {fv!r}."
        )
    else:
        statement = (
            f"{cls_name}.{fn.name}:"
            + "".join(f" when {o['when']}, return {o['value']!r}" for o in outcomes)
            + "."
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


def _has_dispatch_indirection(fn):
    """True only for genuine dispatch/loop control flow: a loop, a dispatch-table
    lookup (subscript with a dynamic key), or a dynamic getattr load."""
    for n in ast.walk(fn):
        if isinstance(n, (ast.For, ast.While)):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr" and len(n.args) >= 2 and isinstance(n.args[1], (ast.Name, ast.Attribute, ast.Call, ast.BinOp, ast.Subscript)):
            return True
        if isinstance(n, ast.Subscript) and isinstance(n.slice, (ast.Name, ast.Attribute, ast.Call, ast.BinOp)):
            return True
    return False


def _dispatch_loop_decision_fields(fn, cls_name, file, module_consts=None):
    """Structured decision fields for methods with genuine dispatch indirection.

    Requires dispatch-table/loop control flow (loop, dispatch-table lookup, or
    dynamic getattr load) plus at least one guard. Returns None otherwise.
    """

    # Collect guard conditions via one structural walk, ordered by position.
    guards = sorted(
        (n for n in ast.walk(fn) if isinstance(n, ast.If)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    if not guards:
        return None
    
    # Check if method returns dict or None (computed values)
    has_computed_returns = False
    for st in ast.walk(fn):
        if not isinstance(st, ast.Return):
            continue
        if isinstance(st.value, ast.Dict):
            has_computed_returns = True
        elif isinstance(st.value, ast.Constant):
            if st.value.value is None:
                has_computed_returns = True
        elif isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Name):
            if st.value.func.id == "getattr" and len(st.value.args) >= 2:
                has_computed_returns = True

    if not has_computed_returns:
        return None
    
    # Real dispatch indirection is required: a loop, a dispatch-table lookup,
    # or a dynamic getattr load. Guards alone are boolean decisions, not dispatch.
    if not _has_dispatch_indirection(fn):
        return None
    
    # Extract guard conditions and outcomes
    outcomes = []
    first_guard = guards[0]
    predicate = _predicate_fields(first_guard.test, file, {}, module_consts)
    if predicate is None:
        return None

    for st in guards:
        # Extract return values from this guard
        ret_val = None
        ret_line = None
        if st.body and isinstance(st.body[0], ast.Return):
            ret_val = st.body[0].value
            ret_line = st.body[0].lineno
        elif st.orelse and isinstance(st.orelse[0], ast.Return):
            ret_val = st.orelse[0].value
            ret_line = st.orelse[0].lineno

        if ret_val is not None:
            # Describe the return value
            if isinstance(ret_val, ast.Dict):
                outcomes.append({
                    "when": ast.unparse(st.test),
                    "value": "dict",
                    "evidence": {"file": file, "line": ret_line},
                    "description": "computed value (dict)",
                })
            elif isinstance(ret_val, ast.Constant):
                if ret_val.value is None:
                    outcomes.append({
                        "when": ast.unparse(st.test),
                        "value": None,
                        "evidence": {"file": file, "line": ret_line},
                        "description": "computed value (None)",
                    })
                else:
                    outcomes.append({
                        "when": ast.unparse(st.test),
                        "value": ret_val.value,
                        "evidence": {"file": file, "line": ret_line},
                        "description": f"computed value ({type(ret_val.value).__name__})",
                    })
            else:
                outcomes.append({
                    "when": ast.unparse(st.test),
                    "value": "complex",
                    "evidence": {"file": file, "line": ret_line},
                    "description": "computed value (complex expression)",
                })
    
    if not outcomes:
        return None
    
    # Build statement describing the method
    statement_parts = []
    for outcome in outcomes:
        if outcome["value"] is None:
            statement_parts.append(f"{cls_name}.{fn.name}: when {outcome['when']}, return None")
        elif outcome["value"] == "dict":
            statement_parts.append(f"{cls_name}.{fn.name}: when {outcome['when']}, return dict")
        elif outcome["value"] == "complex":
            statement_parts.append(f"{cls_name}.{fn.name}: when {outcome['when']}, return complex")
        else:
            statement_parts.append(f"{cls_name}.{fn.name}: when {outcome['when']}, return {outcome['value']!r}")
    
    statement = "; ".join(statement_parts) + "."
    
    # Extract method parameters
    params = [a.arg for a in fn.args.args if a.arg != "self"]
    params += [a.arg for a in fn.args.kwonlyargs]
    
    return {
        "statement": statement,
        "preconditions": [],
        "predicate": predicate,
        "params": params,
        "exceptions": [],
        "outcomes": outcomes,
        "enforcement_location": {
            "file": file,
            "line": first_guard.lineno,
            "symbol": f"{cls_name}.{fn.name}",
        },
        "supporting": [
            {"file": file, "line": first_guard.lineno, "excerpt": ast.unparse(first_guard.test)},
        ],
        "conflicting": [],
        "limitations": [
            "structural ast analysis; the analyzed code is parsed, never executed",
            "computed values resolved from guard conditions and return expressions",
        ],
    }


def _deepen_constant_rules(trees, rules):
    """Attach deep ground-truth fields to class-constant rules via the
    methods in the same class that reference the constant (symbol resolution,
    source names only)."""
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
                    # Try dispatch/loop pattern
                    fields = _dispatch_loop_decision_fields(m, cls_name, str(rule["file"]), module_consts)
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
                    # Try dispatch/loop pattern
                    fields = _dispatch_loop_decision_fields(m, cdef.name, file, module_consts)
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


def _module_constant_links(trees, edges, rules):
    """Detect links between same-type equal-value module constants with a
    shared name token of 4+ characters, recording evidence plus a limitation
    that name overlap is not proof of shared business meaning."""
    # Group constants by value
    value_groups = defaultdict(list)
    
    # Add module constants
    for file, tree in trees.items():
        for name, value, lineno in _module_constant_entries(tree):
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                value_groups[value].append((file, name, value, "module_constant", lineno))
    
    # Add class constants from rules
    for rule in rules:
        if "symbol" in rule and "." in rule["symbol"]:
            cls_name, const_name = rule["symbol"].split(".", 1)
            if "value" in rule and isinstance(rule["value"], (int, float, str)) and not isinstance(rule["value"], bool):
                # Extract file from rule (using first rule's file if not available)
                rule_file = rule.get("file", list(trees.keys())[0] if trees else "")
                value_groups[rule["value"]].append(
                    (rule_file, const_name, rule["value"], "class_constant", rule.get("line") or 0)
                )
    
    # Process each value group
    for value, consts in value_groups.items():
        if len(consts) < 2:  # Need at least 2 constants to link
            continue
        
        # Check each pair for shared name token (4+ chars, alphabetic only)
        for i, entry in enumerate(consts):
            file1, name1, val1, type1, line1 = entry
            for j, entry2 in enumerate(consts[i + 1:], start=i + 1):
                file2, name2, val2, type2, line2 = entry2
                # Extract alphabetic tokens from names (case-insensitive)
                tokens1 = [t.lower() for t in re.findall(r'[a-zA-Z]{4,}', name1)]
                tokens2 = [t.lower() for t in re.findall(r'[a-zA-Z]{4,}', name2)]
                
                # Check for shared token
                shared_tokens = set(tokens1) & set(tokens2)
                if not shared_tokens:
                    continue
                
                # Create edge linking the two constants
                edge = {
                    "kind": "constant_link",
                    "source": {
                        "file": file1,
                        "name": name1,
                        "symbol": name1,
                        "value": value,
                        "type": type1,
                    },
                    "target": {
                        "file": file2,
                        "name": name2,
                        "symbol": name2,
                        "value": value,
                        "type": type2,
                    },
                    "evidence": {
                        "file": file1,
                        "line": line1 or line2 or 0,
                        "excerpt": f"{name1} = {value!r} vs {name2} = {value!r}",
                    },
                    "origin": ORIGIN,
                    "confidence": 1.0,
                    "basis": "same type + equal value + shared name token (4+ chars)",
                    "limitations": [
                        "name overlap is not proof of shared business meaning",
                        "structural analysis only, parsed never executed",
                    ],
                }
                edges.append(edge)


def _transition_edges(trees, index):
    """Detect guarded state transitions: a resolved method call carrying an
    UPPER_SNAKE state literal. The receiver class is resolved through the
    symbol index (chained constructor, local constructor variable, or plain
    dotted name) — never via hardcoded source names."""
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
    # Add constant links between same-type equal-value constants with shared name tokens
    _module_constant_links(trees, extra_edges, rules)
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
    deep_nodes, extra_edges = _deep_ground_truth(files, rules, index)

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
    edges.extend(extra_edges)
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
