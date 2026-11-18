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
  OpenAPI path, and ``equivalent`` is never true (a client URL carries
  scheme+host; an OpenAPI path is path-only).
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
    """HTTP(S) client URLs found in a source file (with the line they appear on)."""
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for index, line in enumerate(text.splitlines(), start=1):
        for match in _CLIENT_URL.finditer(line):
            records.append({
                **_base(path, index),
                "kind": "client_url",
                "url": match.group(0),
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
_SET_STATUS = re.compile(r"\.set[A-Z]\w*Status\(\s*\"([^\"]*)\"\s*\)")


def _method_bodies(path: Path) -> list[dict[str, Any]]:
    """Method/constructor bodies as (lines, 1-based base line) slices.

    Only braces on the declaration line and the following lines are counted,
    so single-line bodies and abstract declarations are skipped.
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
        if depth <= 0:
            index += 1
            continue
        end = index
        j = index + 1
        while j < len(lines) and depth > 0:
            depth += lines[j].count("{") - lines[j].count("}")
            end = j
            j += 1
        bodies.append({
            "name": match.group(1),
            "lines": lines[index + 1:end],
            "base": index + 2,
        })
        index = end + 1
    return bodies


def extract_guards(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Enforced rules and guarded transitions from Java method bodies.

    Each ``if`` whose failure branch throws (``rejection``) or returns false
    (``false return``) becomes an enforced rule: the condition (an
    age/vs-window comparison like ``days <= windowDays`` when present,
    otherwise the guard-if test), the failure outcome, and enforcement lines
    citing the guard-if body lines plus any compared-return line. Each status
    change (``set*Status(...)``) guarded by a rule in the same method becomes
    a transition record naming the guard and citing its enforcement lines.
    """
    enforced: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    bodies = _method_bodies(path)
    file_methods = {body["name"] for body in bodies}
    for body in bodies:
        lines = body["lines"]
        base = body["base"]
        condition: str | None = None
        condition_line: int | None = None
        for offset, line in enumerate(lines):
            match = _RETURN_STMT.match(line)
            if match and condition is None:
                compare = _COMPARE.search(match.group(1))
                if compare:
                    condition = f"{compare.group(1)} {compare.group(2)} {compare.group(3)}"
                    condition_line = base + offset
        guard_lines: list[int] = []
        for offset, line in enumerate(lines):
            guard = _IF_OPEN.search(line)
            if not guard:
                continue
            condition_text = _if_condition(line)
            guard_line = base + offset
            guard_lines.append(guard_line)
            indent = len(line) - len(line.lstrip())
            failure: str | None = None
            for later in lines[offset + 1:]:
                stripped = later.strip()
                if stripped and len(later) - len(later.lstrip()) <= indent:
                    break
                if _THROW.search(later):
                    failure = "rejection"
                    break
                if _FALSE_RETURN.search(later):
                    failure = "false return"
                    break
            if failure is None:
                continue
            enforcement = [guard_line]
            if condition_line is not None:
                enforcement.append(condition_line)
            call = _GUARD_CALL.search(condition_text)
            guard_name = call.group(1) if call else (condition or "window guard")
            record = dict(_base(path, guard_line))
            record.update({
                "kind": "enforced_rule",
                "rule": "window_guard",
                "condition": condition or (condition_text or "").strip(),
                "failure_outcome": failure,
                "guard": guard_name,
                "guard_if_line": guard_line,
                "enforcement_lines": enforcement,
                "span": {"start_line": guard_line, "end_line": enforcement[-1]},
            })
            enforced.append(record)
        for offset, line in enumerate(lines):
            status = _SET_STATUS.search(line)
            if not status:
                continue
            status_line = base + offset
            guard_name: str | None = None
            guard_line: int | None = None
            for goffset, gline in enumerate(lines):
                guard = _IF_OPEN.search(gline)
                if not guard:
                    continue
                call = _GUARD_CALL.search(_if_condition(gline))
                if call and call.group(1) in file_methods:
                    guard_name = call.group(1)
                    guard_line = base + goffset
                    break
            if guard_name is None:
                for goffset in range(len(lines) - 1, -1, -1):
                    guard = _IF_OPEN.search(lines[goffset])
                    if guard:
                        guard_line = base + goffset
                        guard_name = (_if_condition(lines[goffset]) or "").strip() or "window guard"
                        break
            if guard_name is None:
                guard_name = "window guard"
                guard_line = status_line
            transitions.append({
                **_base(path, status_line),
                "kind": "transition",
                "target_status": status.group(1),
                "guard": guard_name,
                "guard_if_line": guard_line,
                "enforcement_lines": [guard_line] if guard_line is not None else [],
                "span": {"start_line": status_line, "end_line": status_line},
            })
    return enforced, transitions


def link_client_url(url_record: dict[str, Any], openapi_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contract links between a client URL and OpenAPI paths.

    Compatible when the client URL path matches an OpenAPI path; equivalent
    is never true (client URL carries scheme+host, OpenAPI path is path-only).
    """
    links: list[dict[str, Any]] = []
    client_path = urlparse(url_record["url"]).path
    base = {
        **_base(Path(url_record["file"]), url_record["line"]),
        "kind": "contract_link",
        "client_url": url_record["url"],
        "client_path": client_path,
        "compatible": False,
        "equivalent": False,
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
        link["compatible"] = op["openapi_path"] == client_path
        if link["compatible"]:
            link["reason"] = (
                f"compatible contract: client path {client_path} == openapi path {op['openapi_path']}"
            )
        else:
            link["reason"] = (
                f"incompatible contract: client path {client_path} != openapi path {op['openapi_path']}"
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
