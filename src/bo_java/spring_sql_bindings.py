"""Spring/SQL binding extractor over frozen fixture repos (stdlib only).

Extracts binding evidence from fixture sources as plain text:

* Spring annotations to operations: ``@PostMapping`` path plus ``@Service``
  and ``@Transactional`` markers, each with a line span.
* YAML policy values via line-based text scan, every record marked
  ``configured``; the stdlib-only limitation is recorded on every record
  (no YAML parser is used: indentation and value types are not validated).
* SQL ``CREATE TABLE`` declarations and columns as static persistence
  evidence (``static: true``).
* OpenAPI paths and operations, plus client-URL to OpenAPI-path contract
  links: a link is ``compatible`` when the client URL path matches an
  OpenAPI path *and* the client method matches the operation method, and
  ``equivalent`` is never true (a client URL carries scheme+host; an
  OpenAPI path is path-only).
* Java method-body guards: each ``if`` whose failure branch throws or
  returns false becomes an ``enforced_rule`` (condition, failure outcome,
  enforcement line spans citing the guard-if body lines, not declarations),
  and each status change guarded by such a rule becomes a ``transition``
  record naming the guard and citing its enforcement lines.

Every record carries: ``origin`` (``deterministic``), ``file`` plus ``line``,
``content_hash`` (sha256 of the file bytes) and ``extractor_version``.
No dependency beyond the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ORIGIN = "deterministic"
EXTRACTOR_VERSION = "bo_java.spring_sql_bindings/1.0.0"
YAML_LIMITATION = (
    "stdlib-only line-based text scan (no YAML parser; indentation and "
    "value types are not validated)"
)

_ANNOTATION_LINE = re.compile(r"^\s*@([A-Za-z_][\w\.]*)\s*(.*)$")
_POSTMAPPING_VALUE = re.compile(r'@PostMapping\(\s*"([^"]*)"\s*\)')
_PATH_LINE = re.compile(r"^\s{2}(/.+?):\s*$")
_METHOD_LINE = re.compile(r"^\s{4}(get|post|put|delete|patch|options|head)\s*:\s*$")
_CREATE_TABLE = re.compile(r"(?i)^\s*CREATE\s+TABLE\s+([A-Za-z_][\w]*)\s*\(?\s*$")
_COLUMN_LINE = re.compile(r"^\s*([A-Za-z_][\w]*)\s+([A-Za-z_][\w()<>, \t]*)\s*(.*)$")
_CLIENT_URL = re.compile(r'https?://[^"\s\)]+')
_REST_TEMPLATE_DECL = re.compile(r"\bRestTemplate\s+([A-Za-z_][\w]*)")
_CALL_METHOD_HINTS = (
    ("postForObject", "post"),
    ("getForObject", "get"),
)
_URL_VAR = re.compile(r'\b([A-Za-z_][\w]*)\s*=\s*["\']https?://')
_HTTP_METHOD = re.compile(r"HttpMethod\.([A-Za-z]+)")
_CONCAT_ASSIGN = re.compile(
    r'\b([A-Za-z_][\w]*)\s*=\s*([A-Za-z_][\w]*)\s*\+\s*["\'](/[^"\']*)["\']'
)
_VALUE_DEFAULT = re.compile(r'@Value\([^)]*:(https?://[^}"\'\s\)]+)')


def _resolve_base_literal(lines: list[str], base: str) -> str | None:
    """Single-literal base value for ``base`` in the same file.

    Collects direct ``base = "http..."`` assignments plus ``@Value``
    defaults (``@Value("...:http...")`` with ``base`` declared within
    the next two lines). Returns the literal only when exactly one
    assignment exists; otherwise None (ambiguous/unknown: abstain).
    """
    vals: list[str] = []
    direct = re.compile(r"\b" + re.escape(base) + r"\s*=\s*[\"'](https?://[^\"']*)[\"']")
    for line in lines:
        match = direct.search(line)
        if match:
            vals.append(match.group(1))
    for idx, line in enumerate(lines):
        vm = _VALUE_DEFAULT.search(line)
        if not vm:
            continue
        lit = vm.group(1)
        for ahead in range(idx, min(idx + 3, len(lines))):
            if re.search(r"\b" + re.escape(base) + r"\b", lines[ahead]):
                vals.append(lit)
                break
    if len(vals) != 1:
        return None
    return vals[0]


def _infer_client_method_and_line(
    url_record: dict[str, Any],
) -> tuple[str | None, int | None]:
    """Method plus invocation line for a client-URL record.

    The variable must reach a supported call (postForObject/
    getForObject/exchange+HttpMethod, ...) in the same method
    (data-flow by name); otherwise (None, None) or (None, call line)
    for unknown-method candidates (never compatible True).

    Hardening: the call receiver must be a supported ``RestTemplate``
    receiver -- a variable typed ``RestTemplate`` in the same file or
    the conventional field ``restTemplate`` (so ``<recv>.postForObject``/
    ``<recv>.getForObject``/``<recv>.exchange`` only counts when
    ``recv`` is allowlisted). Unsupported receivers (e.g. a custom
    ``FakeSender.postForObject``) never yield a method (abstain).
    """
    try:
        lines = Path(url_record["file"]).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None, None
    url = url_record.get("url", "")
    var: str | None = url_record.get("var")
    lineno = url_record.get("line")
    if not var and isinstance(lineno, int) and 1 <= lineno <= len(lines):
        match = _URL_VAR.search(lines[lineno - 1])
        if match:
            var = match.group(1)
        else:
            cm = _CONCAT_ASSIGN.search(lines[lineno - 1])
            if cm:
                var = cm.group(1)

    def _reaches(text: str) -> bool:
        if url and url in text:
            return True
        return bool(var) and re.search(r"\b" + re.escape(var) + r"\b", text) is not None

    allowed: set[str] = {"restTemplate"}
    for _line in lines:
        for _m in _REST_TEMPLATE_DECL.finditer(_line):
            allowed.add(_m.group(1))

    def _recv_ok(text: str, call: str) -> bool:
        for recv in allowed:
            if re.search(
                r"\b" + re.escape(recv) + r"\s*\.\s*" + re.escape(call) + r"\s*\(",
                text,
            ):
                return True
        return False

    scoped: list[tuple[int, str]] = [(i + 1, t) for i, t in enumerate(lines)]
    if isinstance(lineno, int) and var:
        try:
            bodies = _method_bodies(Path(url_record["file"]))
        except (OSError, UnicodeError):
            bodies = []
        for body in bodies:
            start = body["base"]
            end = body["base"] + len(body["lines"]) - 1
            if start <= lineno <= end:
                scoped = [
                    (start + off, text) for off, text in enumerate(body["lines"])
                ]
                break

    for num, text in scoped:
        if "exchange(" in text and _reaches(text):
            if not _recv_ok(text, "exchange"):
                continue
            method = _HTTP_METHOD.search(text)
            if method:
                return method.group(1).lower(), num
            return None, num
        for hint, method in _CALL_METHOD_HINTS:
            if hint + "(" in text and _reaches(text):
                if not _recv_ok(text, hint):
                    continue
                return method, num
    return None, None


def _infer_client_method(url_record: dict[str, Any]) -> str | None:
    """Trivial client-method inference from the URL record's source file.

    Returns the HTTP method when the recorded URL (literal or its assigned
    variable) visibly reaches a ``RestTemplate`` request call
    (``postForObject``/``getForObject``/``exchange`` with an explicit
    ``HttpMethod``, ...); otherwise None (abstain: unknown method).
    """
    method, _ = _infer_client_method_and_line(url_record)
    return method


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base(path: Path, line: int) -> dict[str, Any]:
    return {
        "origin": ORIGIN,
        "extractor_version": EXTRACTOR_VERSION,
        "file": str(path),
        "line": int(line),
        "content_hash": _content_hash(path),
    }


# ---------------------------------------------------------------- annotations


def extract_annotations(path: Path) -> list[dict[str, Any]]:
    """Spring annotation records: @PostMapping operations plus markers."""
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for index, line in enumerate(text.splitlines(), start=1):
        match = _ANNOTATION_LINE.match(line)
        if not match:
            continue
        annotation = match.group(1)
        record = dict(_base(path, index))
        record["annotation"] = annotation
        record["span"] = {"start_line": index, "end_line": index}
        if annotation == "PostMapping":
            value_match = _POSTMAPPING_VALUE.search(line)
            record["kind"] = "operation"
            record["operation"] = "post"
            record["path"] = value_match.group(1) if value_match else match.group(2).strip()
        else:
            record["kind"] = "marker"
            record["operation"] = None
            record["path"] = None
        records.append(record)
    return records


# ---------------------------------------------------------------- YAML policy


def extract_yaml_policy(path: Path) -> list[dict[str, Any]]:
    """YAML policy values via text scan; every value is marked configured."""
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value == "":
            continue
        record = dict(_base(path, index))
        record.update({
            "kind": "yaml_policy",
            "key": key,
            "value": value,
            "configured": True,
            "limitation": YAML_LIMITATION,
            "span": {"start_line": index, "end_line": index},
        })
        records.append(record)
    return records


# ---------------------------------------------------------------- SQL persistence


def extract_sql_persistence(path: Path) -> list[dict[str, Any]]:
    """CREATE TABLE declarations and columns as static persistence evidence."""
    records: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        match = _CREATE_TABLE.match(lines[index])
        if not match:
            index += 1
            continue
        table = match.group(1)
        start = index
        index += 1
        depth = lines[start].count("(") - lines[start].count(")")
        columns: list[dict[str, str]] = []
        while index < len(lines) and depth > 0:
            column_line = lines[index]
            depth += column_line.count("(") - column_line.count(")")
            if depth <= 0:
                break
            col_match = _COLUMN_LINE.match(column_line)
            if col_match:
                columns.append({
                    "name": col_match.group(1),
                    "data_type": col_match.group(2).strip(),
                    "constraints": col_match.group(3).strip(),
                })
            index += 1
        record = dict(_base(path, start + 1))
        record.update({
            "kind": "create_table",
            "table": table,
            "columns": columns,
            "static": True,
            "evidence": "persistence",
            "span": {"start_line": start + 1, "end_line": index + 1},
        })
        records.append(record)
    return records


# ---------------------------------------------------------------- OpenAPI


def extract_openapi(path: Path) -> list[dict[str, Any]]:
    """OpenAPI path items and operations declared under ``paths:``."""
    records: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    in_paths = False
    current_path: str | None = None
    path_start = 0
    while index < len(lines):
        line = lines[index]
        if not in_paths and re.match(r"^\s*paths\s*:", line):
            in_paths = True
            index += 1
            continue
        if in_paths:
            path_match = _PATH_LINE.match(line)
            if path_match:
                current_path = path_match.group(1)
                path_start = index + 1
                index += 1
                continue
            method_match = _METHOD_LINE.match(line)
            if method_match and current_path is not None:
                records.append({
                    **_base(path, index + 1),
                    "kind": "openapi_operation",
                    "openapi_path": current_path,
                    "method": method_match.group(1),
                    "span": {"start_line": path_start, "end_line": index + 1},
                })
            index += 1
            continue
        index += 1
    return records


# ---------------------------------------------------------------- client URLs


def extract_client_urls(path: Path) -> list[dict[str, Any]]:
    """HTTP(S) client URLs found in a source file (with the line they appear on).

    Also resolves narrow variable-held URLs: ``String <v> = "<http...>"``
    is covered by the literal scan, plus ``<v> = <base> + "<path>"``
    where ``base`` resolves to a single literal in the same file with
    no reassignment. The concat binding line becomes the record line;
    ambiguous values (multiple assignments/reassigned/unknown base)
    are skipped (abstain, never guess).
    """
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines, start=1):
        for match in _CLIENT_URL.finditer(line):
            records.append({
                **_base(path, index),
                "kind": "client_url",
                "url": match.group(0),
                "span": {"start_line": index, "end_line": index},
            })
    try:
        bodies = _method_bodies(path)
    except (OSError, UnicodeError):
        return records
    for index, line in enumerate(lines, start=1):
        for cm in _CONCAT_ASSIGN.finditer(line):
            var, base, suffix = cm.group(1), cm.group(2), cm.group(3)
            enclosing: dict[str, Any] | None = None
            for body in bodies:
                start = body["base"]
                end = body["base"] + len(body["lines"]) - 1
                if start <= index <= end:
                    enclosing = body
                    break
            if enclosing is None:
                continue
            assign = re.compile(r"\b" + re.escape(var) + r"\s*=")
            if sum(1 for t in enclosing["lines"] if assign.search(t)) != 1:
                continue
            lit = _resolve_base_literal(lines, base)
            if lit is None:
                continue
            full = lit.rstrip("/") + suffix
            if not urlparse(full).path.startswith("/"):
                continue
            records.append({
                **_base(path, index),
                "kind": "client_url",
                "url": full,
                "var": var,
                "span": {"start_line": index, "end_line": index},
            })
    return records


# ---------------------------------------------------------------- Java guards and transitions

_METHOD_WITH_BODY = re.compile(
    r"^\s*(?:public|private|protected|static\s+)*(?:[\w<>\[\]\s,\?\.]*)\s+([A-Za-z_][\w]*)\s*\([^)]*\)\s*(?:throws\s+[\w\.\[\]\s,]+)?\s*\{?\s*$"
)
_IF_OPEN = re.compile(r"\bif\s*\(")
_GUARD_CALL = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")


def _if_condition(line: str) -> str | None:
    """Return the balanced-parenthesized test of an ``if (...)`` on *line*.

    A ``[^)]*`` regex truncates at the first nested ``)`` (for example
    ``if (!isEligible(request.getPaidAt()))``). Scan paren depth instead so the
    full condition is captured.
    """
    m = _IF_OPEN.search(line)
    if not m:
        return None
    pos = m.end() - 1  # index of '('
    depth = 0
    for j in range(pos, len(line)):
        ch = line[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return line[pos + 1:j]
    return None
_RETURN_STMT = re.compile(r"^\s*return\s+([^;]+?)\s*;")
_COMPARE = re.compile(r"\b([A-Za-z_][\w]*)\s*(<=|>=|==|<|>)\s*([A-Za-z_][\w]*)")
_THROW = re.compile(r"\bthrow\b")
_FALSE_RETURN = re.compile(r"\breturn\s+false\b")
_SET_STATUS = re.compile(
    r"\.set\w*Status\(\s*(?:\"([^\"]*)\""
    r"|([A-Z][A-Z0-9_]*(?:\.[A-Za-z_][\w]*)*|(?:[A-Za-z_][\w]*\.)+[A-Za-z_][\w]*))\s*\)"
)


def _method_bodies(path: Path) -> list[dict[str, Any]]:
    """Method/constructor bodies as (lines, 1-based base line) slices.

    Only braces on the declaration line (or an opening brace within the next
    few lines, for brace-on-next-line style) are counted, so single-line
    bodies and abstract declarations are skipped.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    bodies: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        match = _METHOD_WITH_BODY.match(lines[index])
        if not match:
            index += 1
            continue
        depth = lines[index].count("{") - lines[index].count("}")
        brace_line = index
        if depth <= 0:
            found: int | None = None
            for ahead in range(1, 4):
                if index + ahead >= len(lines):
                    break
                if "{" in lines[index + ahead]:
                    found = index + ahead
                    break
            if found is None:
                index += 1
                continue
            brace_line = found
            depth = sum(
                lines[k].count("{") - lines[k].count("}")
                for k in range(index, brace_line + 1)
            )
            if depth <= 0:
                index += 1
                continue
        end = brace_line
        j = brace_line + 1
        while j < len(lines) and depth > 0:
            depth += lines[j].count("{") - lines[j].count("}")
            end = j
            j += 1
        bodies.append({
            "name": match.group(1),
            "lines": lines[brace_line + 1:end],
            "base": brace_line + 2,
        })
        index = end + 1
    return bodies


def _then_block(
    offset: int,
    match_end: int,
    line: str,
    lines: list[str],
    base: int,
    stack_before: list[tuple],
) -> tuple[tuple, tuple, int] | None:
    """Braced then-block opened by the ``if`` starting on body line *offset*.

    Returns ``(then_key, guard_parent, end_line)``: ``then_key`` is the
    pushed-block id that a directly-contained statement's enclosing-block
    key ends with, ``guard_parent`` the enclosing-block key at the ``if``,
    and ``end_line`` (1-based) the closing-brace line. Returns None for
    unbraced single-statement ``if``s (abstain: no block to contain a
    write) or when no opener is found.
    """
    pos = match_end - 1  # index of the '(' in 'if ('
    depth = 0
    close: int | None = None
    for j in range(pos, len(line)):
        ch = line[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                close = j
                break
    if close is None:
        return None
    brace_line: int | None = None
    brace_col = 0
    k = line.find("{", close + 1)
    if k != -1:
        brace_line, brace_col = offset, k
    else:
        for ahead in range(1, 4):
            nxt = offset + ahead
            if nxt >= len(lines):
                break
            text = lines[nxt]
            if not text.strip():
                continue
            open_idx = text.find("{")
            semi_idx = text.find(";")
            if open_idx == -1 or (semi_idx != -1 and semi_idx < open_idx):
                return None
            brace_line, brace_col = nxt, open_idx
            break
        if brace_line is None:
            return None
    assert brace_line is not None
    eff: list = list(stack_before[offset])
    seq: list[tuple[int, str]] = []
    if brace_line == offset:
        seq = [(offset, line[:brace_col])]
    else:
        seq = [(offset, line)]
        for mid in range(offset + 1, brace_line):
            seq.append((mid, lines[mid]))
        seq.append((brace_line, lines[brace_line][:brace_col]))
    for _idx, prefix in seq:
        for ch in prefix:
            if ch == "{":
                eff.append((_idx, len(eff)))
            elif ch == "}":
                if len(eff) > 1:
                    eff.pop()
    then_key = (brace_line, len(eff))
    guard_parent = tuple(eff)
    stack: list = list(eff)
    stack.append(then_key)
    target = len(stack)
    rest: list[tuple[int, str]] = []
    if brace_line == offset:
        rest.append((offset, line[brace_col + 1:]))
        start = offset + 1
    else:
        rest.append((brace_line, lines[brace_line][brace_col + 1:]))
        start = brace_line + 1
    for idx in range(start, len(lines)):
        rest.append((idx, lines[idx]))
    end_line = base + len(lines) - 1
    for idx, text in rest:
        closed = False
        for ch in text:
            if ch == "{":
                stack.append(("nested", idx))
            elif ch == "}":
                if len(stack) > 1:
                    stack.pop()
                if len(stack) < target:
                    end_line = base + idx
                    closed = True
                    break
        if closed:
            break
    return then_key, guard_parent, end_line


def extract_guards(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Enforced rules and guarded transitions from Java method bodies.

    Each ``if`` whose failure branch throws (``rejection``) or returns false
    (``false return``) becomes an enforced rule: the condition (an
    age/vs-window comparison like ``days <= windowDays`` when present,
    otherwise the guard-if test), the failure outcome, and enforcement lines
    citing the guard-if body lines plus any compared-return line. Each status
    change (``set*Status(...)`` with a quoted literal or an enum/constant
    argument) guarded by a *preceding* enforced rule in the same body
    becomes a transition record naming that nearest-prior guard and citing
    its enforcement lines; a write *contained* in a guard's braced
    then-block (guard_if < write <= guard end, write parent == then-block)
    is governed by that guard's full condition even without a
    throw/return-false. An unguarded write (no prior enforced guard and no
    containing guard, guard-after-write only) carries ``guard None`` with
    empty enforcement -- no guard is synthesized, and a preceding
    same-block ``if`` that does not contain the write never governs it.
    """
    enforced: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    bodies = _method_bodies(path)
    for body in bodies:
        lines = body["lines"]
        base = body["base"]
        # Brace depth at each body line (same counting as _method_bodies).
        # Method entry is depth 1; depth is recorded before the line's
        # own braces so an `if (...) {` sits at its block level.
        # Parent-block identity: stack of enclosing blocks at each line
        # start; each `{` pushes a unique opener id so sibling branches
        # (if/else, sequential ifs) get distinct parents even when their
        # depths match. A guard may only claim a write in the same parent.
        depth_at: list[int] = []
        stack_before: list[tuple] = []
        _depth = 1
        _stack: list[tuple | str] = ["method"]
        for _idx, _line in enumerate(lines):
            depth_at.append(_depth)
            stack_before.append(tuple(_stack))
            for _ch in _line:
                if _ch == "{":
                    _stack.append((_idx, len(_stack)))
                elif _ch == "}":
                    if len(_stack) > 1:
                        _stack.pop()
            _depth += _line.count("{") - _line.count("}")


        def _parent_at(offset: int, match_start: int, line: str) -> tuple:
            """Enclosing-block key for a statement starting at match_start.

            Applies any braces in the line prefix (e.g. `} else if`)
            to the line-start stack so an else-branch guard is keyed by
            its real parent, not the sibling block it closes.
            """
            eff: list[tuple | str] = list(stack_before[offset])
            for _ch in line[:match_start]:
                if _ch == "{":
                    eff.append((offset, len(eff)))
                elif _ch == "}":
                    if len(eff) > 1:
                        eff.pop()
            return tuple(eff)
        condition: str | None = None
        condition_line: int | None = None
        for offset, line in enumerate(lines):
            match = _RETURN_STMT.match(line)
            if match and condition is None:
                compare = _COMPARE.search(match.group(1))
                if compare:
                    condition = f"{compare.group(1)} {compare.group(2)} {compare.group(3)}"
                    condition_line = base + offset
        body_guards: list[dict[str, Any]] = []
        body_positive: list[dict[str, Any]] = []
        for offset, line in enumerate(lines):
            guard = _IF_OPEN.search(line)
            if not guard:
                continue
            condition_text = _if_condition(line)
            if condition_text is None:
                continue
            guard_line = base + offset
            guard_parent = _parent_at(offset, guard.start(), line)
            guard_depth = len(guard_parent)
            guard_depth_scan = depth_at[offset] if offset < len(depth_at) else None
            if guard_depth is None or guard_depth_scan is None:
                continue
            stripped_condition = condition_text.strip()
            if stripped_condition:
                then = _then_block(
                    offset, guard.end(), line, lines, base, stack_before
                )
                if then is not None:
                    then_key, then_parent, then_end = then
                    body_positive.append({
                        "condition": stripped_condition,
                        "guard_if_line": guard_line,
                        "guard_parent": guard_parent,
                        "then_key": then_key,
                        "guard_end_line": then_end,
                    })
            indent = len(line) - len(line.lstrip())
            failure: str | None = None
            failure_line: int | None = None
            scanned: list[int] = []
            tail = line[guard.end():]
            if _THROW.search(tail):
                failure = "rejection"
                failure_line = guard_line
            elif _FALSE_RETURN.search(tail):
                failure = "false return"
                failure_line = guard_line
            else:
                for later_offset, later in enumerate(
                    lines[offset + 1 :], start=offset + 1
                ):
                    stripped = later.strip()
                    if stripped and len(later) - len(later.lstrip()) <= indent:
                        break
                    scanned.append(later_offset)
                    later_depth = (
                        depth_at[later_offset]
                        if later_offset < len(depth_at)
                        else None
                    )
                    # Direct-child failure only: a throw/return deeper than
                    # one level belongs to a nested `if`, not this guard
                    # (e.g. outer `if (flag)` must not claim an inner throw).
                    # Skip it and keep scanning for a direct failure.
                    if (
                        guard_depth_scan is None
                        or later_depth is None
                        or later_depth > guard_depth_scan + 1
                    ):
                        continue
                    if _THROW.search(later):
                        failure = "rejection"
                        failure_line = base + later_offset
                        break
                    if _FALSE_RETURN.search(later):
                        failure = "false return"
                        failure_line = base + later_offset
                        break
            if failure is None:
                continue
            enforcement = [guard_line]
            if failure_line is not None and failure_line not in enforcement:
                enforcement.append(failure_line)
            if condition_line is not None:
                cond_offset = condition_line - base
                if cond_offset in scanned:
                    enforcement.append(condition_line)
            call = _GUARD_CALL.search(condition_text)
            if call:
                guard_name = call.group(1)
            else:
                guard_name = (condition or condition_text.strip())
                if not guard_name.strip():
                    continue
            enforcement = sorted(set(enforcement))
            record = dict(_base(path, guard_line))
            record.update({
                "kind": "enforced_rule",
                "rule": "window_guard",
                "condition": condition or (condition_text or "").strip(),
                "failure_outcome": failure,
                "guard": guard_name,
                "guard_if_line": guard_line,
                "enforcement_lines": enforcement,
                "span": {"start_line": enforcement[0], "end_line": enforcement[-1]},
            })
            enforced.append(record)
            body_guards.append({
                "guard": guard_name,
                "guard_if_line": guard_line,
                "enforcement_lines": enforcement,
                "guard_depth": guard_depth,
                "guard_parent": guard_parent,
            })
        for offset, line in enumerate(lines):
            status = _SET_STATUS.search(line)
            if not status:
                continue
            if status.group(1) is not None:
                target = status.group(1)
            else:
                target = status.group(2).split(".")[-1]
            status_line = base + offset
            write_parent = _parent_at(offset, status.start(), line)
            write_depth = len(write_parent)
            contained = None
            for pg in body_positive:
                if (
                    pg["guard_if_line"] < status_line <= pg["guard_end_line"]
                    and len(write_parent) == len(pg["guard_parent"]) + 1
                    and write_parent[-1] == pg["then_key"]
                    and tuple(write_parent[:-1]) == pg["guard_parent"]
                ):
                    contained = pg
                    break
            if contained is not None:
                transitions.append({
                    **_base(path, status_line),
                    "kind": "transition",
                    "target_status": target,
                    "guard": contained["condition"],
                    "guard_if_line": contained["guard_if_line"],
                    "enforcement_lines": [contained["guard_if_line"]],
                    "span": {"start_line": status_line, "end_line": status_line},
                })
                continue
            prior = [
                g
                for g in body_guards
                if g["guard_if_line"] < status_line
                and g.get("guard_depth") is not None
                and write_depth is not None
                and g["guard_depth"] == write_depth
                and g.get("guard_parent") == write_parent
            ]
            best = max(prior, key=lambda g: g["guard_if_line"]) if prior else None
            transitions.append({
                **_base(path, status_line),
                "kind": "transition",
                "target_status": target,
                "guard": best["guard"] if best is not None else None,
                "guard_if_line": best["guard_if_line"] if best is not None else None,
                "enforcement_lines": list(best["enforcement_lines"]) if best is not None else [],
                "span": {"start_line": status_line, "end_line": status_line},
            })
    return enforced, transitions


def link_client_url(url_record: dict[str, Any], openapi_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contract links between a client URL and OpenAPI paths.

    Compatible only when the client URL path matches an OpenAPI path *and*
    the client method matches the OpenAPI operation method (the client
    method rides on the URL record, or is inferred trivially from
    ``postForObject``/``getForObject``/``exchange`` call sites via a
    supported ``RestTemplate`` receiver; unknown
    method abstains via ``candidate-needs-method``). An unused URL whose
    path matches nothing yields no links. Duplicate destinations
    (same path+method in >1 operation) never yield compatible True
    (ambiguous destination: abstain/candidate). Equivalent is never true (client
    URL carries scheme+host, OpenAPI path is path-only).
    """
    links: list[dict[str, Any]] = []
    client_path = urlparse(url_record["url"]).path
    explicit = url_record.get("method")
    inferred_method, call_line = _infer_client_method_and_line(url_record)
    if explicit is not None and str(explicit).strip() != "":
        client_method: str | None = str(explicit).strip().lower()
    else:
        client_method = inferred_method
    call_text: str | None = None
    if isinstance(call_line, int):
        try:
            call_text = Path(url_record["file"]).read_text(encoding="utf-8").splitlines()[call_line - 1].strip()
        except (OSError, UnicodeError, IndexError):
            call_text = None
    if not any(op.get("openapi_path") == client_path for op in openapi_records):
        return []
    dup_count = 0
    if client_method is not None:
        for _op in openapi_records:
            _m = _op.get("method")
            _norm = (
                str(_m).strip().lower()
                if _m is not None and str(_m).strip() != ""
                else None
            )
            if _op.get("openapi_path") == client_path and _norm == client_method:
                dup_count += 1
    base = {
        **_base(Path(url_record["file"]), url_record["line"]),
        "kind": "contract_link",
        "client_url": url_record["url"],
        "client_path": client_path,
        "compatible": False,
        "equivalent": False,
        "client_call_line": call_line,
        "client_call": call_text,
        "span": {
            "start_line": url_record["line"],
            "end_line": url_record["line"],
        },
    }
    for op in openapi_records:
        link = dict(base)
        link["openapi_path"] = op["openapi_path"]
        link["openapi_file"] = op["file"]
        link["openapi_line"] = op["line"]
        op_method = op.get("method")
        op_method_norm = (
            str(op_method).strip().lower()
            if op_method is not None and str(op_method).strip() != ""
            else None
        )
        path_match = op["openapi_path"] == client_path
        method_match = bool(
            client_method and op_method_norm and client_method == op_method_norm
        )
        link["path_match"] = path_match
        link["method_match"] = method_match
        link["compatible"] = bool(path_match and method_match)
        link["equivalent"] = False
        never_equiv = "equivalent never true (client URL carries scheme+host, OpenAPI path is path-only)"
        if dup_count > 1 and path_match and method_match:
            link["compatible"] = False
            link["reason"] = (
                f"ambiguous destination: {dup_count} operations share path {client_path} "
                f"and method {client_method}; abstain, no verified compatible link "
                f"(candidate, duplicate destination); {never_equiv}"
            )
        elif link["compatible"]:
            op_path_line = op.get("span", {}).get("start_line", op["line"]) if isinstance(op.get("span"), dict) else op["line"]
            binding_line = url_record["line"]
            if call_line is not None:
                link["reason"] = (
                    f"compatible contract: client path {client_path} == openapi path {op['openapi_path']} "
                    f"and method {client_method} == {op_method_norm}; "
                    f"binding L{binding_line} + call L{call_line} + openapi path L{op_path_line} + method L{op['line']}; {never_equiv}"
                )
            else:
                link["reason"] = (
                    f"compatible contract: client path {client_path} == openapi path {op['openapi_path']} "
                    f"and method {client_method} == {op_method_norm}; "
                    f"binding L{binding_line} + openapi path L{op_path_line} + method L{op['line']}; {never_equiv}"
                )
        elif path_match:
            if client_method is None or op_method_norm is None:
                link["reason"] = (
                    f"candidate-needs-method: path {client_path} matches but method is unknown "
                    f"(client {client_method!r} vs openapi {op_method_norm!r}); {never_equiv}"
                )
            else:
                link["reason"] = (
                    f"incompatible contract: method mismatch client {client_method} != openapi "
                    f"{op_method_norm} for path {client_path}; {never_equiv}"
                )
        else:
            link["reason"] = (
                f"incompatible contract: client path {client_path} != openapi path {op['openapi_path']}; "
                f"{never_equiv}"
            )
        links.append(link)
    return links


# ---------------------------------------------------------------- index


def _is_openapi(name: str) -> bool:
    low = name.lower()
    return "openapi" in low and (low.endswith(".yaml") or low.endswith(".yml"))


def index(repo_paths: list[Path | str]) -> dict[str, Any]:
    """Extract all bindings from the given repos; deterministic summary."""
    repos = [Path(p) for p in repo_paths]
    annotations: list[dict[str, Any]] = []
    policies: list[dict[str, Any]] = []
    persistence: list[dict[str, Any]] = []
    openapi: list[dict[str, Any]] = []
    client_urls: list[dict[str, Any]] = []
    enforced_rules: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []

    for repo in repos:
        for root, dirs, names in os.walk(repo):
            dirs.sort()
            for name in sorted(names):
                file = Path(root) / name
                if name.endswith(".java"):
                    annotations.extend(extract_annotations(file))
                    client_urls.extend(extract_client_urls(file))
                    rules, trans = extract_guards(file)
                    enforced_rules.extend(rules)
                    transitions.extend(trans)
                elif _is_openapi(name):
                    openapi.extend(extract_openapi(file))
                elif name in ("application.yml", "application.yaml") or name.endswith(".yml") or name.endswith(".yaml"):
                    policies.extend(extract_yaml_policy(file))
                elif name.endswith(".sql"):
                    persistence.extend(extract_sql_persistence(file))

    contract_links: list[dict[str, Any]] = []
    for url_record in client_urls:
        contract_links.extend(link_client_url(url_record, openapi))

    return {
        "origin": ORIGIN,
        "extractor_version": EXTRACTOR_VERSION,
        "repos": [str(p) for p in repos],
        "records": {
            "annotations": annotations,
            "policies": policies,
            "persistence": persistence,
            "openapi": openapi,
            "client_urls": client_urls,
            "contract_links": contract_links,
            "enforced_rules": enforced_rules,
            "transitions": transitions,
        },
        "counts": {
            "annotations": len(annotations),
            "policies": len(policies),
            "persistence": len(persistence),
            "openapi": len(openapi),
            "client_urls": len(client_urls),
            "contract_links": len(contract_links),
            "enforced_rules": len(enforced_rules),
            "transitions": len(transitions),
        },
        "limitation": YAML_LIMITATION,
    }
