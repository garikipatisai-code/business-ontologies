"""Spring/SQL binding extractor over frozen fixture repos (stdlib only).

Extracts binding evidence from fixture sources as plain text:

* Spring annotations to operations: ``@PostMapping`` path plus ``@Service``
  and ``@Transactional`` markers, each with a line span.
* YAML policy values via line-based text scan, every record marked
  ``configured``; the stdlib-only limitation is recorded on every record
  (no YAML parser is used: indentation and value types are not validated).
* SQL ``CREATE TABLE`` declarations and columns as static persistence
  evidence (``static: true``), plus ``FOREIGN KEY`` constraints -- inline
  ``FOREIGN KEY (col) REFERENCES tbl(col) [ON DELETE/UPDATE action]``
  and ``ALTER TABLE tbl ADD [CONSTRAINT name] FOREIGN KEY ...`` -- each
  with table/column/reference/action evidence and line spans.
* OpenAPI paths and operations, plus client-URL to OpenAPI-path contract
  links: a link is ``compatible`` when the client URL path matches an
  OpenAPI path *and* the client method matches the operation method, and
  ``equivalent`` is never true (a client URL carries scheme+host; an
  OpenAPI path is path-only). Each operation also carries its sibling
  ``operationId`` (when present) and its declared ``responses`` (status
  codes plus header names under ``responses:``, each with its line).
* Bean Validation field constraints (``@NotNull``/``@NotEmpty``/
  ``@NotBlank``/``@Size``/``@Digits``/``@Pattern``/``@Min``/``@Max``/
  ``@Email``/``@Positive``/``@PositiveOrZero``/``@Negative``/
  ``@NegativeOrZero``/``@Past``/``@PastOrPresent``/``@Future``/
  ``@FutureOrPresent``/``@DecimalMin``/``@DecimalMax`` with
  params) on field/getter declarations with
  annotation-to-declaration spans, plus record-component constraints
  (``record Name(...)`` inline annotations, same annotation set, as
  ``record_component_constraint`` records with annotation-to-component
  spans); superclass constraints merge with
  ``inherited: true`` and the parent span cited when the superclass file
  is in the scanned tree, otherwise an ``inheritance_gap`` (unresolved,
  never fabricated); enforcement is a separate ``@Valid``/``@Validated``
  param-or-validator-call check (declared-only otherwise, never an
  HTTP 400 claim).
* JPA-to-SQL links: an ``@Entity``/``@Table(name)`` entity links to its
  ``CREATE TABLE`` only on a unique (exact-or-normalized) match, and a
  ``@JoinColumn``/``@ManyToOne`` column verifies only when an FK on that
  table covers the column and references a known table; anything else
  abstains with a reason.
* Java server interfaces (``interface X`` plus ``;``-terminated method
  signatures with return type/arity) and ``implements`` records (impl
  class, interface names, method name/arity/return, ``@Override``
  presence, body span), plus operation-to-implementation bindings: a
  ``full`` bind needs the operationId to name an interface method AND a
  same-name/same-arity method on an implementing class AND at least one
  corroboration (path literal or return-type match); a missing
  interface file yields ``partial`` (never full), name-only matches
  abstain, multiple implementors abstain as ambiguous, and an impl
  status outside the declared responses yields ``partial`` with
  ``response_supported`` False. Client invocation, destination, and
  delivery are never proved by a binding.
* Java method-body guards: each ``if`` whose failure branch throws or
  returns false becomes an ``enforced_rule`` (condition, failure outcome,
  enforcement line spans citing the guard-if body lines, not declarations),
  and each status change guarded by such a rule becomes a ``transition``
  record naming the guard and citing its enforcement lines.
* TS/JS HTTP clients (``*.ts``/``*.js``): Angular ``HttpClient``
  (``this.http``/``http``/``httpClient`` ``.get/post/put/delete/patch``),
  ``fetch`` (explicit ``{method}`` or default GET) and ``axios`` (method
  forms plus trivial ``axios({method, url})``) with literal/template/
  single-assigned-base URL expressions (``${var}`` becomes ``{var}``;
  ``environment.X`` host prefixes stay unknown, never fabricated).
  Records reuse the ``client_url`` shape with ``ts: True`` on a documented
  ``http://ts.local`` dummy host (path-only linking) so the linkers treat
  them as declared-only candidates (``destination_verified`` False,
  ``equivalent`` never True).

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
_OPERATION_ID_LINE = re.compile(r"^\s*operationId\s*:\s*(.+?)\s*$")
_RESPONSES_LINE = re.compile(r"^\s*responses\s*:\s*$")
_STATUS_LINE = re.compile(r"""^\s*['"]?(\d{3}|default)['"]?\s*:\s*(?:#.*)?$""")
_HEADERS_LINE = re.compile(r"^\s*headers\s*:\s*$")
_HEADER_NAME_LINE = re.compile(r"^\s*([A-Za-z_][\w\-]*)\s*:\s*(?:#.*)?$")
_CREATE_TABLE = re.compile(r"(?i)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)\b.*$")
_IF_NOT_EXISTS = re.compile(r"(?i)\bIF\s+NOT\s+EXISTS\b")
_INLINE_FK = re.compile(
    r"(?i)^\s*(?:CONSTRAINT\s+([A-Za-z_][\w]*)\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)"
    r"\s*REFERENCES\s+([A-Za-z_][\w]*)\s*(?:\(([^)]+)\))?\s*(.*?)\s*,?\s*$"
)
_ALTER_FK = re.compile(
    r"(?i)^\s*ALTER\s+TABLE\s+([A-Za-z_][\w]*)\s+ADD\s+"
    r"(?:CONSTRAINT\s+([A-Za-z_][\w]*)\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)"
    r"\s*REFERENCES\s+([A-Za-z_][\w]*)\s*(?:\(([^)]+)\))?\s*(.*?)\s*;?\s*$"
)
_FK_ON_DELETE = re.compile(
    r"(?i)\bON\s+DELETE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION)"
)
_FK_ON_UPDATE = re.compile(
    r"(?i)\bON\s+UPDATE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION)"
)
_SKIP_COLUMN_DEF = re.compile(
    r"(?i)^\s*(PRIMARY|FOREIGN|UNIQUE|CONSTRAINT|CHECK|KEY|EXCLUDE)\b"
)
_COLUMN_LINE = re.compile(r"^\s*([A-Za-z_][\w]*)\s+([A-Za-z_][\w]*(?:\s*\([^)]*\))?(?:\s*\[\s*\])*)\s*(.*)$")
_CLIENT_URL = re.compile(r'https?://[^"\s\)]+')
_REST_TEMPLATE_DECL = re.compile(r"\bRestTemplate\s+([A-Za-z_][\w]*)")
_CALL_METHOD_HINTS = (
    ("postForObject", "post"),
    ("getForObject", "get"),
    ("put", "put"),
    ("delete", "delete"),
    ("patchForObject", "patch"),
)
_URL_VAR = re.compile(r'\b([A-Za-z_][\w]*)\s*=\s*["\']https?://')
_HTTP_METHOD = re.compile(r"HttpMethod\.([A-Za-z]+)")
_CONCAT_ASSIGN = re.compile(
    r'\b([A-Za-z_][\w]*)\s*=\s*([A-Za-z_][\w]*)\s*\+\s*["\'](/[^"\']*)["\']'
)
_VALUE_DEFAULT = re.compile(r'@Value\([^)]*:(https?://[^}"\'\s\)]+)')
_FLUENT_VERB = re.compile(r"\.(get|post|put|delete|patch)\s*\(\s*\)")
_FLUENT_URI = re.compile(r"\.uri\s*\(")
_FLUENT_TYPE_DECL = re.compile(
    r"\b(?:WebClient|RestClient)(?:\.Builder)?\s+([A-Za-z_][\w]*)"
)
_FLUENT_CREATE = re.compile(r"\b(?:WebClient|RestClient)\s*\.\s*(?:create|builder)\s*\(")
_FLUENT_BUILD_ASSIGN = re.compile(
    r"\b([A-Za-z_][\w]*)\s*=\s*([A-Za-z_][\w]*)\s*\.\s*build\s*\(\s*\)"
)
_FLUENT_CONCAT_ARG = re.compile(r'^\s*([A-Za-z_][\w]*)\s*\+\s*"([^"]*)"')
_FLUENT_LITERAL_ARG = re.compile(r'^\s*"([^"]*)"')


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

    Fluent records (WebClient/RestClient ``.verb().uri(...)``) already carry
    their invocation evidence (verb plus ``.uri()`` call line); it is reused
    directly so the linkers treat them as declared-only candidates with a
    call line.

    The variable must reach a supported call (postForObject/
    getForObject/put/delete/patchForObject/exchange+HttpMethod, ...)
    in the same method (data-flow by name); otherwise (None, None) or
    (None, call line) for unknown-method candidates (never compatible
    True).

    Hardening: the call receiver must be a supported ``RestTemplate``
    receiver -- a variable typed ``RestTemplate`` in the same file or
    the conventional field ``restTemplate`` (so ``<recv>.postForObject``/
    ``<recv>.getForObject``/``<recv>.put``/``<recv>.delete``/
    ``<recv>.patchForObject``/``<recv>.exchange`` only counts when
    ``recv`` is allowlisted). Unsupported receivers (e.g. a custom
    ``FakeSender.postForObject``) never yield a method (abstain).
    """
    if isinstance(url_record, dict) and url_record.get("fluent") is True:
        raw = url_record.get("client_method", url_record.get("method"))
        fluent_method = (
            str(raw).strip().lower()
            if raw is not None and str(raw).strip() != ""
            else None
        )
        fluent_call = url_record.get("call_line")
        return fluent_method, fluent_call if isinstance(fluent_call, int) else None
    if isinstance(url_record, dict) and url_record.get("ts") is True:
        # TS/JS records (extract_ts_client_urls) carry their own invocation
        # evidence: the receiver (HttpClient/fetch/axios) plus the call line
        # are recorded at extraction, so the linkers reuse them directly as
        # declared-only candidates (destination_verified False, equivalent
        # never True), mirroring the fluent-carry above.
        raw = url_record.get("client_method", url_record.get("method"))
        ts_method = (
            str(raw).strip().lower()
            if raw is not None and str(raw).strip() != ""
            else None
        )
        ts_call = url_record.get("call_line")
        return ts_method, ts_call if isinstance(ts_call, int) else None
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
    (``postForObject``/``getForObject``/``put``/``delete``/
    ``patchForObject``/``exchange`` with an explicit ``HttpMethod``,
    ...); otherwise None (abstain: unknown method).
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


# ---------------------------------------------------------------- mapping routes
# (Spring @RequestMapping class/method join; OpenAPI-absent linking)


_MAPPING_VERB_BY_PREFIX = {
    "Get": "get",
    "Post": "post",
    "Put": "put",
    "Delete": "delete",
    "Patch": "patch",
}
_REQUESTMETHOD_VERB = {
    "GET": "get",
    "POST": "post",
    "PUT": "put",
    "DELETE": "delete",
    "PATCH": "patch",
    "OPTIONS": "options",
    "HEAD": "head",
}
_MAPPING_SHORT_NAMES = frozenset({
    "GetMapping", "PostMapping", "PutMapping", "DeleteMapping",
    "PatchMapping", "RequestMapping",
})


def _mapping_args_text(line: str) -> str:
    """Annotation argument text (``(...)``) on a mapping line, else ``""``."""
    pos = line.find("(")
    if pos == -1:
        return ""
    text = line[pos:].strip()
    if text.endswith("{"):
        text = text[:-1].rstrip()
    return text


def _mapping_paths_from_args(args_text: str) -> tuple:
    """Path literals from mapping args; dynamic/non-literal abstains.

    Returns ``(paths, dynamic, reason)`` where ``paths`` is a list of
    literals (``[]`` means no path declared: inherit the class base),
    ``dynamic`` True means non-literal (concatenation, variable, no
    quotes) with ``paths`` None and a reason (never fabricated).
    Only ``value=``/``path=`` (or positional) literals count; other
    params (``produces=``/``consumes=``) are ignored.
    """
    text = (args_text or "").strip()
    if not text.startswith("("):
        return [], False, None
    inner = text[1:]
    if inner.endswith(")"):
        inner = inner[:-1]
    if inner.strip() == "":
        return [], False, None
    if "+" in inner:
        return None, True, (
            "dynamic: concatenation in mapping args (abstain, no path claimed)"
        )
    array = re.search(r"(?:value|path)\s*=\s*\{([^}]*)\}", inner)
    if array is not None:
        literals = re.findall(r'"([^"]*)"', array.group(1))
        if literals:
            return literals, False, None
        return None, True, (
            "dynamic: non-literal value/path array (abstain, no path claimed)"
        )
    single = re.search(r'(?:value|path)\s*=\s*"([^"]*)"', inner)
    if single is not None:
        return [single.group(1)], False, None
    stripped = inner.strip()
    if stripped.startswith("{"):
        head = stripped.split("}", 1)[0] + "}"
        literals = re.findall(r'"([^"]*)"', head)
        if literals:
            return literals, False, None
        return None, True, (
            "dynamic: non-literal positional array (abstain, no path claimed)"
        )
    if stripped.startswith('"'):
        first = re.match(r'\s*"([^"]*)"', stripped)
        if first is not None:
            # Positional multi without braces (e.g. @GetMapping("/x","/y"),
            # invalid without {}) is ambiguous: abstain, never claim ["/x"].
            if "," in stripped[first.end():]:
                return None, True, (
                    "dynamic: ambiguous positional multi without braces "
                    "(abstain, no path claimed)"
                )
            return [first.group(1)], False, None
    return None, True, (
        "dynamic: non-literal mapping path (abstain, no path claimed)"
    )


def _parse_method_token(token: str) -> str | None:
    """Single ``method=`` token to a lowercase verb, or None (abstain).

    Accepts qualified ``RequestMethod.X`` and bare ``X`` (static import);
    ``X`` must be one of the seven known verbs in uppercase
    (GET/POST/PUT/DELETE/PATCH/OPTIONS/HEAD, case-sensitive: bare
    lowercase ``get``/``post`` is a variable, abstain, never fabricated).
    Anything else (``FOO``, ``someVar``, ``A | B``, quoted strings,
    fully-qualified ``org.springframework...RequestMethod.GET``) returns
    None: abstain, never fabricated (fully-qualified abstains by design,
    documented, not fixed).
    """
    text = (token or "").strip()
    match = re.match(r"^(?:RequestMethod\s*\.\s*)?([A-Za-z_][\w]*)$", text)
    if not match:
        return None
    return _REQUESTMETHOD_VERB.get(match.group(1))


def _requestmethods_from_args(args_text: str) -> tuple:
    """HTTP methods from ``@RequestMapping(method=...)``.

    Qualified ``RequestMethod.X`` and bare ``X`` (static import) both
    resolve when ``X`` is a known verb (GET/POST/PUT/DELETE/PATCH/
    OPTIONS/HEAD); single yields ``([verb], False)`` and an array
    ``{A, B}`` (qualified, bare, or mixed) yields ``([a, b], False)``
    in source order. Unrecognized/dynamic (``FOO``, ``someVar``,
    ``A | B``, quoted, empty) or absent yields ``(None, True)``
    (honest: never fabricated).
    """
    text = args_text or ""
    array = re.search(r"method\s*=\s*\{([^}]*)\}", text)
    if array is not None:
        parts = [part.strip() for part in array.group(1).split(",")]
        parts = [part for part in parts if part != ""]
        if not parts:
            return None, True
        verbs: list[str] = []
        for part in parts:
            verb = _parse_method_token(part)
            if verb is None:
                return None, True
            verbs.append(verb)
        return verbs, False
    single = re.search(r"method\s*=\s*([^,\)\}]+)", text)
    if single is not None:
        # Invalid Java missing braces (method=GET, POST captures GET only):
        # a comma before ")" after the single token is ambiguous: abstain.
        tail = text[single.end():]
        if "," in tail.split(")", 1)[0]:
            return None, True
        verb = _parse_method_token(single.group(1))
        if verb is None:
            return None, True
        return [verb], False
    return None, True


def _join_mapping_paths(class_base: str | None, method_path: str | None) -> str:
    """Join class + method paths with a single ``/`` (``{var}`` kept)."""
    base = (class_base or "").strip()
    sub = (method_path or "").strip()
    if base and sub:
        joined = base.rstrip("/") + "/" + sub.lstrip("/")
    else:
        joined = base or sub
    joined = re.sub(r"/{2,}", "/", joined)
    if joined and not joined.startswith("/"):
        joined = "/" + joined
    return joined


def _annotation_path_match(pattern: str | None, actual: str | None) -> bool:
    """Path equality with ``{var}``/``*`` template segments (one each).

    A whole-segment ``*`` (Spring single-segment wildcard, pinned:
    ``owners/*/pets/{petId}``) matches any single non-empty segment,
    mirroring ``{var}``; anything else stays literal (``**`` included:
    multi-segment abstains, fail-closed). A literal ``*`` client segment
    counts as non-empty (URLs never contain bare ``*``; harmless, unchanged).
    """
    if not pattern or not actual:
        return False
    if pattern == actual:
        return True
    expected = pattern.split("/")
    seen = actual.split("/")
    if len(expected) != len(seen):
        return False
    for want, got in zip(expected, seen):
        if len(want) >= 2 and want.startswith("{") and want.endswith("}"):
            if not got:
                return False
            continue
        if want == "*":
            if not got:
                return False
            continue
        if want != got:
            return False
    return True


_MAPPING_METHOD_DECL = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|synchronized|abstract|native|default)\s+)*"
    r"[\w<>\[\]\?,\s\.@]+\s+([A-Za-z_][\w]*)\s*\("
)
_MAPPING_NON_METHOD = re.compile(
    r"^(?:if|for|while|switch|catch|synchronized|new|return)\b"
)
# Wrapped (multi-line) method declarations join continuation lines until the
# parens balance, bounded so genuinely unparseable declarations abstain
# instead of swallowing the rest of the file. 8 covers real wrapped
# signatures (e.g. 6 continuation lines) while far-shorter-than-file.
_MAPPING_DECL_JOIN_MAX = 8


def extract_mapping_routes(path: Path) -> list[dict[str, Any]]:
    """Class+method Spring mapping routes with spans (stdlib only).

    Each method-level ``@Get/Post/Put/Delete/PatchMapping`` (single
    verb) or ``@RequestMapping`` (explicit single or array ``method=``
    of known verbs yields that verb list; unknown/dynamic/absent
    yields ``http_methods`` None with
    ``verb_unknown`` True, never fabricated) joins its literal path
    (``value=``/``path=`` or positional; ``{var}`` kept) onto the
    enclosing class ``@RequestMapping`` base (single literal; multiple
    literals expand to one record each). Dynamic/non-literal paths
    (concatenation, bare identifiers) abstain with ``path`` None plus
    a reason. Every record carries ``source`` ``spring_annotation``.

    NOTE (documented limitations, not fixes): a class-level ``method=``
    constraint is ignored (a bare method-level ``@RequestMapping``
    without its own ``method=`` still abstains as verb-unknown even
    when the class base declares one); only a class-level
    ``@RequestMapping`` counts as a base (a class-level ``@GetMapping``
    or other shortcut is ignored, base ``""``); multi-line annotations
    abstain by design (args must be on the annotation line: a wrapped
    ``(...)`` yields no path literal, never half-parsed); an annotation
    sharing the declaration line (``@GetMapping("/x") public ...``)
    is not picked up (annotations are read from the consecutive lines
    directly above the declaration only). Wrapped (multi-line) method
    declarations join up to ``_MAPPING_DECL_JOIN_MAX`` continuation lines
    until the parens balance (the span covers the joined lines); a
    declaration still unbalanced beyond that bound abstains with no record
    (never half-parsed), and single-line declarations behave exactly as
    before.
    """
    records: list[dict[str, Any]] = []
    file = Path(path)
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    try:
        decls = _class_decls(file)
    except (OSError, UnicodeError):
        decls = []

    def _class_bases(decl_line: int) -> tuple[list, dict | None]:
        block = _preceding_annotations(lines, decl_line)
        target = None
        for entry in block:
            if entry[1].split(".")[-1] == "RequestMapping":
                target = entry
        if target is None:
            return [""], None
        anno_line, _, args = target
        paths, dynamic, reason = _mapping_paths_from_args(args)
        info: dict[str, Any] = {
            "annotation": "RequestMapping",
            "line": anno_line,
            "span": {"start_line": anno_line, "end_line": anno_line},
            "args": args,
        }
        if dynamic:
            return [None], info
        if not paths:
            return [""], info
        return list(paths), info

    class_infos: list[dict[str, Any]] = []
    for decl in decls:
        bases, anno = _class_bases(int(decl["line"]))
        class_infos.append({
            "class": decl["class"],
            "line": int(decl["line"]),
            "bases": bases,
            "annotation": anno,
        })

    def _owner(decl_line: int) -> dict[str, Any]:
        current: dict[str, Any] = {"class": None, "bases": [""], "annotation": None}
        for info in class_infos:
            if info["line"] < decl_line:
                current = info
            else:
                break
        return current

    consumed_until = 0
    for decl_line, decl_text in enumerate(lines, start=1):
        if decl_line <= consumed_until:
            continue
        decl_match = _MAPPING_METHOD_DECL.match(decl_text)
        if not decl_match:
            continue
        stripped = decl_text.strip()
        if stripped.startswith("@"):
            continue
        if _MAPPING_NON_METHOD.match(stripped):
            continue
        if re.search(r"\b(class|interface|enum)\b", decl_text):
            continue
        end_line = decl_line
        if _strip_string_literals(decl_text).count("(") != _strip_string_literals(
            decl_text
        ).count(")"):
            # Wrapped (multi-line) declaration: join continuation lines
            # until the parens balance, bounded so a genuinely unparseable
            # declaration abstains instead of swallowing the file. Counts run
            # on string-stripped text so a ``)``/``(`` inside a literal
            # (``value = ")"``) never fakes an unbalanced declaration.
            balanced = False
            for ahead in range(1, _MAPPING_DECL_JOIN_MAX + 1):
                if decl_line + ahead > len(lines):
                    break
                decl_text += " " + lines[decl_line + ahead - 1].strip()
                end_line = decl_line + ahead
                if _strip_string_literals(decl_text).count(
                    "("
                ) == _strip_string_literals(decl_text).count(")"):
                    balanced = True
                    break
            if not balanced:
                continue
        consumed_until = max(consumed_until, end_line)
        body = {"name": decl_match.group(1), "decl": decl_line}
        owner = _owner(decl_line)
        block = _preceding_annotations(lines, decl_line)
        for anno_line, name, args in block:
            short = name.split(".")[-1]
            if short not in _MAPPING_SHORT_NAMES:
                continue
            if short == "RequestMapping":
                methods, unknown = _requestmethods_from_args(args)
            else:
                prefix = short.replace("Mapping", "")
                methods = [_MAPPING_VERB_BY_PREFIX[prefix]]
                unknown = False
            method_paths, dynamic, reason = _mapping_paths_from_args(args)
            if dynamic:
                candidates: list = [None]
            elif not method_paths:
                candidates = [""]
            else:
                candidates = list(method_paths)
            for base in owner["bases"]:
                for sub in candidates:
                    if dynamic or base is None or sub is None:
                        joined = None
                        why = reason or (
                            "dynamic: non-literal class or method path "
                            "(abstain, no path claimed)"
                        )
                    else:
                        joined = _join_mapping_paths(str(base), str(sub))
                        why = None
                        if unknown:
                            why = (
                                "verb unknown: @RequestMapping without a "
                                "single method (abstain, never compatible True)"
                            )
                    record = dict(_base(file, anno_line))
                    record.update({
                        "kind": "mapping_route",
                        "class": owner["class"],
                        "method": body["name"],
                        "http_methods": list(methods) if methods is not None else None,
                        "verb_unknown": bool(unknown),
                        "path": joined,
                        "source": "spring_annotation",
                        "class_annotation": owner["annotation"],
                        "method_annotation": {
                            "annotation": short,
                            "line": anno_line,
                            "span": {"start_line": anno_line, "end_line": anno_line},
                            "args": args,
                        },
                        "reason": why,
                        "span": {"start_line": anno_line, "end_line": end_line},
                    })
                    records.append(record)
    return records


def emit_annotation_operations(mapping_routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotation-backed operations (OpenAPI-absent linking source).

    Routes with a single known verb plus a literal path become
    ``linkable`` operations reusing the ``openapi_path`` key (plus
    ``source`` ``spring_annotation`` and ``operation_id`` None) so
    link code can reuse path logic; verb-unknown or path-None routes
    yield ``linkable`` False with an unresolved reason (never
    compatible True).

    NOTE (documented, not fixed): an empty ``path == ""`` (no path
    declared, inherits empty base) shares the dynamic/non-literal
    unresolved reason (no literal path claimed, never compatible True).
    """
    operations: list[dict[str, Any]] = []
    for route in mapping_routes or []:
        if not isinstance(route, dict) or route.get("kind") != "mapping_route":
            continue
        try:
            file = Path(str(route.get("file", "")))
            line = int(route.get("line", 1))
        except (ValueError, TypeError):
            continue
        try:
            record = dict(_base(file, line))
        except (OSError, UnicodeError):
            record = {
                "origin": ORIGIN,
                "extractor_version": EXTRACTOR_VERSION,
                "file": str(route.get("file", "")),
                "line": line,
                "content_hash": str(route.get("content_hash", "")),
            }
        methods = route.get("http_methods")
        route_path = route.get("path")
        single = isinstance(methods, list) and len(methods) == 1
        known_path = isinstance(route_path, str) and route_path != ""
        linkable = bool(single and known_path)
        if linkable:
            reason = None
        elif not known_path:
            reason = (
                "unresolved: dynamic/non-literal annotation path "
                "(abstain, never compatible True)"
            )
        else:
            reason = (
                "unresolved: annotation route without a single HTTP method "
                "(abstain, never compatible True)"
            )
        record.update({
            "kind": "annotation_operation",
            "openapi_path": route_path if isinstance(route_path, str) else None,
            "method": methods[0] if single else None,
            "operation_id": None,
            "operation_id_line": None,
            "responses": [],
            "responses_span": None,
            "source": "spring_annotation",
            "linkable": linkable,
            "verb_unknown": bool(route.get("verb_unknown", not single)),
            "handler_class": route.get("class"),
            "handler_method": route.get("method"),
            "mapping_span": route.get("span"),
            "reason": reason,
            "span": route.get("span") or {"start_line": line, "end_line": line},
        })
        operations.append(record)
    return operations


def link_client_to_annotation(
    url_record: dict[str, Any],
    annotation_ops: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Declared-only links between a client URL and annotation operations.

    Compatible only when the client path matches the annotation path
    (equality or ``{var}``/``*`` single-segment templates) *and* the
    client method matches
    the operation verb *and* invocation evidence exists (a supported
    ``RestTemplate`` call line in the same method) *and* the route is
    unambiguous (a single operation for path+method; duplicates
    abstain as ambiguous). ``destination_verified`` stays False and
    ``equivalent`` never True (operation-only match, path-only
    operation). Unmatched URLs yield no links.
    """
    links: list[dict[str, Any]] = []
    try:
        client_path = urlparse(url_record["url"]).path
    except (KeyError, ValueError, TypeError, AttributeError):
        return []
    if not client_path:
        return []
    try:
        client_host = urlparse(url_record["url"]).hostname
    except ValueError:
        client_host = None
    ops = [op for op in annotation_ops or [] if isinstance(op, dict)]
    if not any(
        isinstance(op.get("openapi_path"), str)
        and _annotation_path_match(str(op.get("openapi_path")), client_path)
        for op in ops
    ):
        return []
    explicit = url_record.get("method")
    inferred_method, call_line = _infer_client_method_and_line(url_record)
    if explicit is not None and str(explicit).strip() != "":
        client_method: str | None = str(explicit).strip().lower()
    else:
        client_method = inferred_method
    call_text: str | None = None
    if isinstance(call_line, int):
        try:
            call_text = Path(url_record["file"]).read_text(
                encoding="utf-8"
            ).splitlines()[call_line - 1].strip()
        except (OSError, UnicodeError, IndexError, KeyError, TypeError):
            call_text = None
    dup_count = 0
    if client_method is not None:
        for op in ops:
            norm = op.get("method")
            norm = (
                str(norm).strip().lower()
                if norm is not None and str(norm).strip() != ""
                else None
            )
            if (
                isinstance(op.get("openapi_path"), str)
                and _annotation_path_match(str(op.get("openapi_path")), client_path)
                and norm == client_method
                and op.get("linkable", True) is not False
                and norm is not None
            ):
                dup_count += 1
    base = {
        **_base(Path(url_record["file"]), url_record["line"]),
        "kind": "contract_link",
        "client_url": url_record["url"],
        "client_path": client_path,
        "compatible": False,
        "equivalent": False,
        "destination_verified": False,
        "destination_evidence": client_host,
        "client_call_line": call_line,
        "client_call": call_text,
        "source": "spring_annotation",
        "span": {
            "start_line": url_record["line"],
            "end_line": url_record["line"],
        },
        "method_reason": url_record.get("method_reason"),
        "default_method": url_record.get("default_method", False),
    }
    for op in ops:
        link = dict(base)
        op_path = op.get("openapi_path")
        link["openapi_path"] = op_path
        link["openapi_file"] = op.get("file")
        link["openapi_line"] = op.get("line")
        link["annotation_file"] = op.get("file")
        link["annotation_line"] = op.get("line")
        op_method = op.get("method")
        op_norm = (
            str(op_method).strip().lower()
            if op_method is not None and str(op_method).strip() != ""
            else None
        )
        path_match = (
            isinstance(op_path, str)
            and _annotation_path_match(op_path, client_path)
        )
        method_match = bool(
            client_method and op_norm and client_method == op_norm
        )
        link["path_match"] = path_match
        link["method_match"] = method_match
        linkable = (
            path_match is True
            and op.get("linkable", True) is not False
            and isinstance(op_path, str)
            and op_norm is not None
        )
        link["compatible"] = bool(
            path_match and method_match and call_line is not None
            and linkable and dup_count <= 1
        )
        link["equivalent"] = False
        never_equiv = "equivalent never true (client URL carries scheme+host, annotation path is path-only)"
        op_only = (
            "operation-only match (path+method), not delivery/schema/equivalence; "
            f"destination host {client_host!r} unverified (destination_verified false); "
            "source spring_annotation"
        )
        if dup_count > 1 and path_match and method_match:
            link["compatible"] = False
            link["reason"] = (
                f"ambiguous destination: {dup_count} annotation operations share path "
                f"{client_path} and method {client_method}; abstain, no verified "
                f"compatible link (candidate, duplicate destination); {never_equiv}; {op_only}"
            )
        elif link["compatible"]:
            op_line = op.get("span", {}).get("start_line", op["line"]) if isinstance(op.get("span"), dict) else op["line"]
            path_verb = (
                "matches annotation template"
                if isinstance(op_path, str) and op_path != client_path
                else "== annotation path"
            )
            link["reason"] = (
                f"compatible contract (annotation-backed): client path {client_path} {path_verb} "
                f"{op_path} and method {client_method} == {op_norm}; "
                f"binding L{url_record['line']} + call L{call_line} + annotation path "
                f"L{op_line} + method L{op['line']}; {never_equiv}; {op_only}"
            )
        elif path_match:
            if method_match:
                link["reason"] = (
                    f"candidate-no-invocation: path {client_path} matches annotation path "
                    f"{op_path} and method {client_method} == {op_norm} but no invocation "
                    f"evidence (no-invocation, compatible requires call line); "
                    f"(candidate, explicit-only without call); {never_equiv}; {op_only}"
                )
            elif client_method is None or op_norm is None:
                link["reason"] = (
                    f"candidate-needs-method: path {client_path} matches annotation path "
                    f"{op_path} but method is unknown (client {client_method!r} vs "
                    f"annotation {op_norm!r}); {never_equiv}; {op_only}"
                )
            else:
                link["reason"] = (
                    f"incompatible contract: method mismatch client {client_method} != "
                    f"annotation {op_norm} for path {client_path}; {never_equiv}; {op_only}"
                )
        else:
            link["reason"] = (
                f"incompatible contract: client path {client_path} != annotation path "
                f"{op_path}; {never_equiv}; {op_only}"
            )
        links.append(link)
    return links

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


def _fk_action(match: re.Match | None) -> str | None:
    """Normalized FK referential action (upper, single spaces) or None."""
    if match is None:
        return None
    return " ".join(match.group(1).upper().split())


def extract_sql_persistence(path: Path) -> list[dict[str, Any]]:
    """CREATE TABLE declarations/columns plus FOREIGN KEY constraints.

    Tables record ``if_not_exists``; single-line and multi-line
    ``CREATE TABLE`` headers both match (the header regex allows trailing
    ``(...)`` on the same line). Inline ``FOREIGN KEY (cols)
    REFERENCES tbl(cols) [ON DELETE/UPDATE action]`` lines (including an
    inline ``CONSTRAINT name``) and ``ALTER TABLE tbl ADD [CONSTRAINT name]
    FOREIGN KEY ...`` statements each become an ``fk_constraint`` record
    (table, columns, ref table/columns, actions or None, line span).
    Constraint-definition lines (PRIMARY / FOREIGN / UNIQUE / ...) are
    never collected as columns. ``ALTER TABLE`` statements may span
    continuation lines (joined until ``;`` or up to four following lines);
    single-line statements remain the canonical form. A closing-line FK
    (the line that balances the outer ``CREATE TABLE`` parens, e.g. ending
    in ``);``) is still processed before the depth break.
    """
    records: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        match = _CREATE_TABLE.match(lines[index])
        if match:
            table = match.group(1)
            start = index
            if_not_exists = bool(_IF_NOT_EXISTS.search(lines[start]))
            index += 1
            depth = lines[start].count("(") - lines[start].count(")")
            columns: list[dict[str, str]] = []
            while index < len(lines) and depth > 0:
                column_line = lines[index]
                depth += column_line.count("(") - column_line.count(")")
                # MED3: check FK before thedepth<=0 break so a closing-line
                # FK (e.g. ending in ");") is still recorded.
                fk_match = _INLINE_FK.match(column_line)
                if fk_match:
                    rest = fk_match.group(5) or ""
                    record = dict(_base(path, index + 1))
                    record.update({
                        "kind": "fk_constraint",
                        "table": table,
                        "columns": [
                            c.strip()
                            for c in fk_match.group(2).split(",")
                            if c.strip()
                        ],
                        "ref_table": fk_match.group(3),
                        "ref_columns": [
                            c.strip()
                            for c in (fk_match.group(4) or "").split(",")
                            if c.strip()
                        ],
                        # NOTE (deferred, maintainability out of scope):
                        # on_delete/on_update extraction duplicates the
                        # ALTER-FK path below (367-368/412-413 in review);
                        # intentional, do not refactor.
                        "on_delete": _fk_action(_FK_ON_DELETE.search(rest)),
                        "on_update": _fk_action(_FK_ON_UPDATE.search(rest)),
                        "name": fk_match.group(1),
                        "span": {"start_line": index + 1, "end_line": index + 1},
                    })
                    records.append(record)
                elif depth <= 0:
                    break
                elif not _SKIP_COLUMN_DEF.match(column_line):
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
                "if_not_exists": if_not_exists,
                "static": True,
                "evidence": "persistence",
                "span": {"start_line": start + 1, "end_line": index + 1},
            })
            records.append(record)
            continue
        alter_match = _ALTER_FK.match(lines[index])
        alter_end = index
        if alter_match is None and re.match(
            r"(?i)^\s*ALTER\s+TABLE\b", lines[index]
        ):
            # Multi-line ALTER: join continuation lines (single-line is
            # canonical; joining is best-effort up to 4 following lines).
            joined = lines[index]
            for ahead in range(1, 5):
                if index + ahead >= len(lines):
                    break
                joined += " " + lines[index + ahead].strip()
                alter_end = index + ahead
                if ";" in lines[index + ahead] or _ALTER_FK.match(joined):
                    break
            alter_match = _ALTER_FK.match(joined)
            if alter_match is None:
                alter_end = index
        if alter_match:
            rest = alter_match.group(6) or ""
            record = dict(_base(path, index + 1))
            record.update({
                "kind": "fk_constraint",
                "table": alter_match.group(1),
                "columns": [
                    c.strip()
                    for c in alter_match.group(3).split(",")
                    if c.strip()
                ],
                "ref_table": alter_match.group(4),
                "ref_columns": [
                    c.strip()
                    for c in (alter_match.group(5) or "").split(",")
                    if c.strip()
                ],
                # NOTE (deferred, maintainability out of scope): duplicates
                # the inline-FK action extraction above; intentional.
                "on_delete": _fk_action(_FK_ON_DELETE.search(rest)),
                "on_update": _fk_action(_FK_ON_UPDATE.search(rest)),
                "name": alter_match.group(2),
                "span": {"start_line": index + 1, "end_line": alter_end + 1},
            })
            records.append(record)
            index = alter_end + 1
            continue
        index += 1
    return records


# ---------------------------------------------------------------- validation constraints and JPA entities


_CONSTRAINT_ANNOTATIONS = {
    "NotNull",
    "NotEmpty",
    "NotBlank",
    "Size",
    "Digits",
    "Pattern",
    "Min",
    "Max",
    "Email",
    "Positive",
    "PositiveOrZero",
    "Negative",
    "NegativeOrZero",
    "Past",
    "PastOrPresent",
    "Future",
    "FutureOrPresent",
    "DecimalMin",
    "DecimalMax",
}
_CLASS_DECL = re.compile(
    r"^\s*(?:(?:public|protected|private|abstract|final|sealed)\s+)*"
    r"class\s+([A-Za-z_][\w]*)"
    r"(?:\s+extends\s+([A-Za-z_][\w]*))?"
)
_FIELD_DECL = re.compile(
    r"^\s*(?!(?:return|throw|new|else)\b)(?:(?:private|protected|public)\s+)?"
    r"(?:(?:static|final|transient|volatile)\s+)*"
    r"[\w<>\[\]\?,\s\.@]+\s+([A-Za-z_][\w]*)\s*(?:=[^;]*)?;\s*$"
)
_GETTER_DECL = re.compile(
    r"^\s*(?:public|protected|private)\s+[\w<>\[\]\?,\s\.@]+\s+"
    r"(?:get|is)([A-Za-z_][\w]*)\s*\(\s*\)"
)
_TABLE_NAME = re.compile(r"@Table\s*\([^)]*name\s*=\s*\"([^\"]+)\"")
_JOINCOLUMN_NAME = re.compile(
    r"@JoinColumn\s*\([^)]*name\s*=\s*\"([^\"]+)\""
)
_MANY_TO_ONE = re.compile(r"^\s*@ManyToOne\b")
_ENTITY_MARKER = re.compile(r"^\s*@Entity\b")
_VALID_PARAM = re.compile(r"@Valid(?:ated)?\b")
_VALIDATOR_CALL = re.compile(r"\bvalidator\s*\.\s*validate\s*\(")
_VALIDATOR_FACTORY = re.compile(
    r"\bValidation\s*\.\s*buildDefaultValidatorFactory\s*\("
)


def _preceding_annotations(
    lines: list[str], decl_line: int
) -> list[tuple[int, str, str]]:
    """Consecutive annotation lines directly above 1-based ``decl_line``.

    Returns ``[(line, name, args)]`` bottom-up source order (topmost
    first); stops at the first blank/non-annotation line (a blank line or
    comment between the annotation and the declaration breaks the block
    by design: abstain, documented, not fixed).
    """
    entries: list[tuple[int, str, str]] = []
    idx = decl_line - 1
    while idx >= 1:
        match = _ANNOTATION_LINE.match(lines[idx - 1])
        if not match:
            break
        entries.append((idx, match.group(1), match.group(2).strip()))
        idx -= 1
    entries.reverse()
    return entries


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _scan_balanced_parens(text: str, open_idx: int) -> int | None:
    """Matching ``)`` for ``(`` at ``open_idx`` (quote+escape aware)."""
    depth = 0
    in_str: str | None = None
    idx = open_idx
    while idx < len(text):
        ch = text[idx]
        if in_str is not None:
            if ch == "\\" and idx + 1 < len(text):
                idx += 2
                continue
            if ch == in_str:
                in_str = None
            idx += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            idx += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    return None


def _strip_string_literals(text: str) -> str:
    """Same-length blanking of ``'...'``/``\"...\"`` (newlines kept).

    Quote+backslash-escape aware so ``"a,b"`` or ``"a\\"b"`` never
    leaks into name searches (substring false-positive guard).
    """
    out = list(text)
    in_str: str | None = None
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if in_str is not None:
            if ch == "\\" and idx + 1 < len(text):
                if out[idx] != "\n":
                    out[idx] = " "
                if text[idx + 1] != "\n":
                    out[idx + 1] = " "
                idx += 2
                continue
            if ch == in_str:
                out[idx] = " "
                in_str = None
            else:
                if ch != "\n":
                    out[idx] = " "
            idx += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            out[idx] = " "
            idx += 1
            continue
        idx += 1
    return "".join(out)


def _strip_java_comments(text: str) -> str:
    """Remove ``//``/``/* */`` outside strings (quote+escape aware)."""
    out: list[str] = []
    in_str: str | None = None
    idx = 0
    n = len(text)
    while idx < n:
        ch = text[idx]
        nxt = text[idx + 1] if idx + 1 < n else ""
        if in_str is not None:
            out.append(ch)
            if ch == "\\" and idx + 1 < n:
                out.append(text[idx + 1])
                idx += 2
                continue
            if ch == in_str:
                in_str = None
            idx += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            out.append(ch)
            idx += 1
            continue
        if ch == "/" and nxt == "/":
            while idx < n and text[idx] != "\n":
                idx += 1
            continue
        if ch == "/" and nxt == "*":
            idx += 2
            while idx < n and not (
                text[idx] == "*" and idx + 1 < n and text[idx + 1] == "/"
            ):
                if text[idx] == "\n":
                    out.append("\n")
                idx += 1
            idx += 2 if idx < n else 0
            out.append(" ")
            continue
        out.append(ch)
        idx += 1
    return "".join(out)


def _blank_strings_and_comments(text: str) -> str:
    """Same-length blanking of strings/comments (newlines kept).

    Used for ``record`` decl search so a record keyword inside a
    comment or string literal never fabricates a declaration; offsets
    (and therefore line numbers) are preserved.
    """
    out = list(text)
    in_str: str | None = None
    idx = 0
    n = len(text)
    while idx < n:
        ch = text[idx]
        if in_str is not None:
            if ch == "\\" and idx + 1 < n:
                if out[idx] != "\n":
                    out[idx] = " "
                if text[idx + 1] != "\n":
                    out[idx + 1] = " "
                idx += 2
                continue
            if ch == in_str:
                out[idx] = " "
                in_str = None
            else:
                if ch != "\n":
                    out[idx] = " "
            idx += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            out[idx] = " "
            idx += 1
            continue
        if ch == "/" and idx + 1 < n and text[idx + 1] == "/":
            out[idx] = " "
            out[idx + 1] = " "
            idx += 2
            while idx < n and text[idx] != "\n":
                out[idx] = " "
                idx += 1
            continue
        if ch == "/" and idx + 1 < n and text[idx + 1] == "*":
            out[idx] = " "
            out[idx + 1] = " "
            idx += 2
            while idx < n:
                if text[idx] == "*" and idx + 1 < n and text[idx + 1] == "/":
                    out[idx] = " "
                    out[idx + 1] = " "
                    idx += 2
                    break
                if text[idx] != "\n":
                    out[idx] = " "
                idx += 1
            continue
        idx += 1
    return "".join(out)


def _find_inline_annotations(comp_text: str) -> list[tuple[str, str, int]]:
    """``(name, args_text, start)`` with balanced-paren args.

    Quote+escape aware so ``@Size(min=Math.max(1,2))`` keeps its full
    args and ``@Pattern(regexp="a,b")`` keeps the quoted comma.
    Unbalanced args yield ``""`` (abstain, never half-parsed).
    """
    results: list[tuple[str, str, int]] = []
    for match in re.finditer(r"@([A-Za-z_][\w\.]*)", comp_text):
        name = match.group(1)
        idx = match.end()
        while idx < len(comp_text) and comp_text[idx].isspace():
            idx += 1
        if idx < len(comp_text) and comp_text[idx] == "(":
            close = _scan_balanced_parens(comp_text, idx)
            if close is None:
                results.append((name, "", match.start()))
                continue
            results.append((name, comp_text[idx : close + 1], match.start()))
        else:
            results.append((name, "", match.start()))
    return results


def _annotation_params(args_text: str) -> dict[str, str]:
    """Bare ``(k=v, ...)`` annotation args; quote+escape and ``()`` aware.

    Splits on top-level commas only (paren depth 0, outside strings)
    so ``Math.max(1,2)`` and quoted commas (``"a,b"``) survive intact;
    strings unquoted. Backslash escapes inside strings respected.
    """
    params: dict[str, str] = {}
    text = (args_text or "").strip()
    if not text.startswith("("):
        return params
    close = _scan_balanced_parens(text, 0)
    if close is None:
        return params
    inner = text[1:close]
    parts: list[str] = []
    buf = ""
    depth = 0
    in_str: str | None = None
    idx = 0
    while idx < len(inner):
        ch = inner[idx]
        if in_str is not None:
            buf += ch
            if ch == "\\" and idx + 1 < len(inner):
                buf += inner[idx + 1]
                idx += 2
                continue
            if ch == in_str:
                in_str = None
            idx += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            buf += ch
            idx += 1
            continue
        if ch == "(":
            depth += 1
            buf += ch
            idx += 1
            continue
        if ch == ")":
            if depth > 0:
                depth -= 1
            buf += ch
            idx += 1
            continue
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
            idx += 1
            continue
        buf += ch
        idx += 1
    parts.append(buf)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip()] = _unquote(value)
        else:
            params["value"] = _unquote(part)
    return params


def _class_decls(path: Path) -> list[dict[str, Any]]:
    """``class C [extends P]`` declarations with 1-based lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    decls: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        match = _CLASS_DECL.match(line)
        if match:
            decls.append({
                "class": match.group(1),
                "superclass": match.group(2),
                "line": index,
            })
    return decls


def _is_field_line(line: str) -> bool:
    """Reject method-like matches (``(`` before any ``=``)."""
    if "(" not in line:
        return True
    eq = line.find("=")
    return eq != -1 and eq < line.find("(")


def extract_field_constraints(path: Path) -> list[dict[str, Any]]:
    """Bean Validation constraints on field/getter declarations.

    Each ``@NotNull``/``@NotEmpty``/``@NotBlank``/``@Size``/``@Digits``/
    ``@Pattern``/``@Min``/``@Max``/``@Email``/``@Positive``/
    ``@PositiveOrZero``/``@Negative``/``@NegativeOrZero``/``@Past``/
    ``@PastOrPresent``/``@Future``/``@FutureOrPresent``/``@DecimalMin``/
    ``@DecimalMax`` annotation in
    the consecutive block directly above a field (or getter, recorded
    under its derived property name) becomes a ``field_constraint``
    record with params, annotation-to-declaration span, and
    ``inherited: False`` (inheritance is resolved separately).
    Unannotated declarations yield nothing (abstain). Package-private
    fields (no visibility modifier) are supported.

    NOTE (deferred, maintainability out of scope): the field/getter scan
    duplicates the join-column scan in ``extract_jpa_entities``; kept
    separate intentionally, do not refactor.
    """
    records: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    classes: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        match = _CLASS_DECL.match(line)
        if match:
            classes.append((index, match.group(1)))

    def _owner(index: int) -> str | None:
        current: str | None = None
        for decl_line, name in classes:
            if decl_line < index:
                current = name
            else:
                break
        return current

    for index, line in enumerate(lines, start=1):
        field_match = _FIELD_DECL.match(line)
        field: str | None = None
        if field_match and _is_field_line(line):
            field = field_match.group(1)
        else:
            getter_match = _GETTER_DECL.match(line)
            if getter_match:
                suffix = getter_match.group(1)
                # JavaBeans decap: getURL -> URL (second char uppercase keeps
                # suffix), else lower first char (getLabel -> label).
                if suffix and len(suffix) > 1 and suffix[1].isupper():
                    field = suffix
                else:
                    field = suffix[0].lower() + suffix[1:] if suffix else None
        if not field:
            continue
        found = [
            (line_no, name, args)
            for line_no, name, args in _preceding_annotations(lines, index)
            if name.split(".")[-1] in _CONSTRAINT_ANNOTATIONS
        ]
        if not found:
            continue
        for line_no, name, args in found:
            record = dict(_base(path, index))
            record.update({
                "kind": "field_constraint",
                "class": _owner(index),
                "field": field,
                "annotation": name.split(".")[-1],
                "params": _annotation_params(args),
                "inherited": False,
                "span": {"start_line": line_no, "end_line": index},
            })
            records.append(record)
    return records


_RECORD_DECL = re.compile(
    r"(?<![\w.])record\s+([A-Za-z_][\w]*)\s*(?:<[^;{}]*>)?\s*\("
)
_INLINE_ANNOTATION = re.compile(
    r"@([A-Za-z_][\w\.]*)\s*(\((?:[^()\"']|\"[^\"]*\"|'[^']*')*\))?"
)
# NOTE: _INLINE_ANNOTATION is retained for compatibility but superseded by
# _find_inline_annotations (balanced-paren, quote+escape aware) for nested
# calls like Math.max(1,2); do not use it for arg capture.


def _line_starts(text: str) -> list[int]:
    """Absolute offsets where each 1-based line starts."""
    starts = [0]
    for match in re.finditer(r"\n", text):
        starts.append(match.end())
    return starts


def _line_of(starts: list[int], offset: int) -> int:
    """1-based line number containing ``offset``."""
    import bisect

    return bisect.bisect_right(starts, offset)


def _split_components(inner: str) -> list[tuple[str, int, int]]:
    """Split record-header inner text on top-level commas.

    Returns ``[(text, start_offset, end_offset)]`` relative to ``inner``;
    ``<>[](){}`` nesting plus single/double quotes are respected so
    generic defaults and quoted commas never split (abstain-safe).
    """
    parts: list[tuple[str, int, int]] = []
    depth_angle = depth_paren = depth_brack = depth_brace = 0
    in_str: str | None = None
    start = 0
    idx = 0
    while idx < len(inner):
        ch = inner[idx]
        if in_str is not None:
            if ch == "\\" and idx + 1 < len(inner):
                idx += 2
                continue
            if ch == in_str:
                in_str = None
            idx += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            idx += 1
            continue
        if ch == "<":
            depth_angle += 1
        elif ch == ">":
            if depth_angle > 0:
                depth_angle -= 1
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            if depth_paren > 0:
                depth_paren -= 1
        elif ch == "[":
            depth_brack += 1
        elif ch == "]":
            if depth_brack > 0:
                depth_brack -= 1
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            if depth_brace > 0:
                depth_brace -= 1
        elif ch == "," and not (
            depth_angle or depth_paren or depth_brack or depth_brace
        ):
            parts.append((inner[start:idx], start, idx))
            start = idx + 1
        idx += 1
    parts.append((inner[start:], start, len(inner)))
    return parts


def extract_record_constraints(path: Path) -> list[dict[str, Any]]:
    """Bean Validation constraints on Java ``record`` components.

    Each ``record Name[<...}](...)`` header (single- or multi-line,
    balanced parens, same file, optional generic type-params) is
    scanned; every inline ``@X[(params)]`` on a component whose short
    name is in ``_CONSTRAINT_ANNOTATIONS`` (core ``@NotNull`` etc plus
    ``@Email``/``@Positive``/``@PositiveOrZero``/``@Negative``/
    ``@NegativeOrZero``/``@Past``/``@PastOrPresent``/``@Future``/
    ``@FutureOrPresent``/``@DecimalMin``/``@DecimalMax``) becomes a
    ``record_component_constraint`` record (``record``/``class`` name,
    ``component``/``field`` name, annotation, params, annotation line,
    annotation-to-component span, plus the ``record_header`` span).
    Annotation args use balanced-paren parsing (quote+escape aware) so
    ``Math.max(1,2)`` and quoted commas survive. Unannotated
    components and non-validation annotations yield nothing; regular
    method params (outside a record header) are never emitted; compact
    constructors (``Name(...)``, no ``record`` keyword) never
    double-count; unbalanced headers abstain (no records, no crash).
    A header whose inner contains another ``record`` decl, ``{`` or
    ``;`` (quote/string stripped) is malformed (unbounded scan past a
    missing close) and abstains; over-long headers (>5000 chars)
    abstain. ``record`` inside comments/strings never declares
    (same-length blanking preserves offsets). ``//``/``/* */``
    comments are stripped before component naming so ``/* note */``
    never becomes the component.

    NOTE (deferred, documented not fixed): ``record`` in a comment or
    string is blanked for discovery (fixed); interface decls without a
    body are out of scope; ``\\`` escapes inside literals are handled
    in scanners (blanking/splitting/params).
    """
    records: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    starts = _line_starts(text)
    search_text = _blank_strings_and_comments(text)
    for decl in _RECORD_DECL.finditer(search_text):
        name = decl.group(1)
        open_paren = decl.end() - 1
        depth = 0
        in_str: str | None = None
        close: int | None = None
        idx = open_paren
        while idx < len(text):
            ch = text[idx]
            if in_str is not None:
                if ch == "\\" and idx + 1 < len(text):
                    idx += 2
                    continue
                if ch == in_str:
                    in_str = None
                idx += 1
                continue
            if ch in ("'", '"'):
                in_str = ch
                idx += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close = idx
                    break
            idx += 1
        if close is None:
            continue
        inner = text[open_paren + 1 : close]
        if (close - open_paren) > 5000:
            continue
        probe = _strip_string_literals(_strip_java_comments(inner))
        if re.search(
            r"\brecord\s+[A-Za-z_][\w]*\s*(?:<[^;{}]*>)?\s*\(", probe
        ):
            continue
        if "{" in probe or ";" in probe:
            continue
        header_start_line = _line_of(starts, decl.start())
        header_end_line = _line_of(starts, close)
        for comp_text, rel_start, rel_end in _split_components(inner):
            comp_start = open_paren + 1 + rel_start
            comp_end = open_paren + 1 + rel_end
            if not comp_text.strip():
                continue
            comp_nocomments = _strip_java_comments(comp_text)
            annotations = _find_inline_annotations(comp_nocomments)
            if not annotations:
                continue
            stripped = re.sub(
                r"@[A-Za-z_][\w\.]*(\s*\((?:[^()\"']|\"(?:[^\"\\]|\\.)*\""
                r"|'(?:[^'\\]|\\.)*')*\))?",
                " ",
                comp_nocomments,
            )
            stripped = _strip_java_comments(stripped)
            stripped = _strip_string_literals(stripped)
            tokens = re.findall(r"[A-Za-z_][\w]*", stripped)
            if not tokens:
                continue
            component = tokens[-1]
            comp_end_line = _line_of(starts, max(comp_end - 1, comp_start))
            for full_name, args_text, rel_pos in annotations:
                short = full_name.split(".")[-1]
                if short not in _CONSTRAINT_ANNOTATIONS:
                    continue
                anno_line = _line_of(starts, comp_start + rel_pos)
                record = dict(_base(path, anno_line))
                record.update({
                    "kind": "record_component_constraint",
                    "record": name,
                    "class": name,
                    "component": component,
                    "field": component,
                    "annotation": short,
                    "params": _annotation_params(args_text or ""),
                    "inherited": False,
                    "span": {
                        "start_line": anno_line,
                        "end_line": comp_end_line,
                    },
                    "record_header": {
                        "start_line": header_start_line,
                        "end_line": header_end_line,
                    },
                })
                records.append(record)
    return records


def link_record_enforcement(
    record_name: str, candidate_files: list[Path | str]
) -> dict[str, Any]:
    """Enforcement linkage for a record type used as a bound parameter.

    ``verified`` True only with same-method evidence: ONE top-level
    parameter chunk naming ``record_name`` that also carries
    ``@Valid``/``@Validated`` in that same chunk (sibling-``@Valid``
    on another param never verifies; param-list only, return position
    excluded via :func:`_signature_param_text`; string literals
    stripped before matching so ``"Parcel"`` never verifies), or a
    parameter list naming the record in a method whose body makes an
    explicit ``validator.validate(...)`` call. A bare
    ``Validation.buildDefaultValidatorFactory()`` without
    ``validate(`` abstains (never verified alone). Empty
    ``record_name`` abstains (verified False). Otherwise ``verified``
    False (declared-only, never an HTTP 400 claim).

    NOTE (documented, not fixed): multi-line controller signatures
    abstain by design (single-line only, no half-parse); interface
    decls without bodies are out of scope; ``@Validated(OnCreate)``
    counts the same as ``@Validated`` (variant not distinguished).
    """
    files = [Path(p) for p in candidate_files or []]
    methods: list[dict[str, Any]] = []
    clean_name = str(record_name or "").strip()
    want = (
        re.compile(r"\b" + re.escape(clean_name) + r"\b")
        if clean_name
        else None
    )
    for file in files:
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        try:
            bodies = _method_bodies(file)
        except (OSError, UnicodeError):
            bodies = []
        for body in bodies:
            if want is None:
                continue
            signature = "\n".join(lines[body["decl"] - 1 : body["base"] - 1])
            param_text = _signature_param_text(signature)
            if param_text is None:
                continue
            bound = False
            for chunk, _, _ in _split_components(
                _strip_java_comments(param_text)
            ):
                probe = _strip_string_literals(
                    _strip_java_comments(chunk)
                )
                if want.search(probe) and _VALID_PARAM.search(probe):
                    bound = True
                    break
            kinds: list[tuple[str, int]] = []
            if bound:
                kinds.append(("valid_param", body["decl"]))
            else:
                probe_all = _strip_string_literals(
                    _strip_java_comments(param_text)
                )
                if not want.search(probe_all):
                    continue
            for offset, text in enumerate(body["lines"]):
                if _VALIDATOR_CALL.search(text):
                    kinds.append(
                        ("validator_call", body["base"] + offset)
                    )
                    break
            if kinds:
                start = min([body["decl"]] + [line for _, line in kinds])
                end = max([line for _, line in kinds])
                methods.append({
                    "file": str(file),
                    "method": body["name"],
                    "decl_line": body["decl"],
                    "evidence": [kind for kind, _ in kinds],
                    "span": {"start_line": start, "end_line": end},
                })
    if files:
        try:
            base = dict(_base(files[0], methods[0]["span"]["start_line"] if methods else 1))
        except (OSError, UnicodeError):
            base = {
                "origin": ORIGIN,
                "extractor_version": EXTRACTOR_VERSION,
                "file": str(files[0]),
                "line": 1,
                "content_hash": "",
            }
    else:
        base = {
            "origin": ORIGIN,
            "extractor_version": EXTRACTOR_VERSION,
            "file": "",
            "line": 1,
            "content_hash": "",
        }
    if methods:
        detail = "; ".join(
            f"{m['method']} via {','.join(m['evidence'])} "
            f"(L{m['span']['start_line']}-L{m['span']['end_line']})"
            for m in methods
        )
        reason = (
            f"enforced: record {record_name!r} bound with validation "
            f"evidence: {detail}; same-method evidence only"
        )
    else:
        reason = (
            f"declared-only: no @Valid/@Validated param binding "
            f"record {record_name!r} or validator call in candidate "
            "files (no enforcement evidence)"
        )
    base.update({
        "kind": "record_enforcement",
        "record": record_name,
        "verified": bool(methods),
        "methods": methods,
        "evidence_spans": [m["span"] for m in methods],
        "reason": reason,
    })
    return base


def resolve_inheritance(paths: list[Path | str]) -> list[dict[str, Any]]:
    """Merge superclass constraints / report gaps over a scanned tree.

    When a ``class C extends P`` superclass file is present in ``paths``,
    P's constraints (own plus transitive) merge as copies with
    ``class`` C, ``inherited: True``, ``parent_class`` P, and the parent
    span cited (evidence stays at the parent file/line). When P is absent,
    one ``inheritance_gap`` record (``resolved: False``) is emitted and no
    inherited rules are fabricated. Simple-name duplicates (same class
    name in different files, different packages) are ambiguous: no merge,
    one ``inheritance_gap`` with ``resolved`` False (abstain, never merge
    the wrong parent).
    """
    files = [Path(p) for p in paths]
    own: dict[str, list[dict[str, Any]]] = {}
    decl_file: dict[str, Path] = {}
    parents: dict[str, str | None] = {}
    decl_lines: dict[str, tuple[Path, int]] = {}
    ambiguous: set[str] = set()
    order: list[str] = []
    for file in files:
        try:
            decls = _class_decls(file)
        except (OSError, UnicodeError):
            continue
        for decl in decls:
            if decl["class"] in decl_file and decl_file[decl["class"]] != file:
                # Duplicate simple name in a different file: ambiguous, no
                # merge (never pick the wrong parent by simple name).
                ambiguous.add(decl["class"])
                continue
            parents.setdefault(decl["class"], decl["superclass"])
            decl_lines.setdefault(decl["class"], (file, decl["line"]))
            if decl["class"] not in decl_file:
                decl_file[decl["class"]] = file
                order.append(decl["class"])
        try:
            constraints = extract_field_constraints(file)
        except (OSError, UnicodeError):
            constraints = []
        for record in constraints:
            own.setdefault(str(record.get("class")), []).append(record)
    memo: dict[str, list[dict[str, Any]]] = {}

    def _inherited(cls: str, seen: frozenset) -> list[dict[str, Any]]:
        if cls in memo:
            return memo[cls]
        if cls in seen:
            memo[cls] = []
            return []
        if cls in ambiguous:
            memo[cls] = []
            return []
        parent = parents.get(cls)
        if not parent or parent not in decl_file or parent in ambiguous:
            memo[cls] = []
            return []
        merged: list[dict[str, Any]] = []
        for record in own.get(parent, []):
            copy = dict(record)
            copy["class"] = cls
            copy["inherited"] = True
            copy["parent_class"] = parent
            merged.append(copy)
        for record in _inherited(parent, seen | {cls}):
            copy = dict(record)
            copy["class"] = cls
            # MED1: transitive copies re-point parent_class to the immediate
            # parent; file/span stay at the original evidence (never rewritten).
            copy["parent_class"] = parent
            merged.append(copy)
        memo[cls] = merged
        return merged

    resolved: list[dict[str, Any]] = []
    for cls in order:
        parent = parents.get(cls)
        if not parent:
            continue
        if cls in ambiguous or parent in ambiguous:
            file, line = decl_lines[cls]
            gap = dict(_base(file, line))
            gap.update({
                "kind": "inheritance_gap",
                "class": cls,
                "superclass": parent,
                "resolved": False,
                "reason": (
                    f"ambiguous: duplicate simple name for {parent!r} or "
                    f"{cls!r} in different files (abstain, no inherited "
                    "rules claimed)"
                ),
                "span": {"start_line": line, "end_line": line},
            })
            resolved.append(gap)
            continue
        if parent in decl_file:
            resolved.extend(_inherited(cls, frozenset()))
        else:
            file, line = decl_lines[cls]
            gap = dict(_base(file, line))
            gap.update({
                "kind": "inheritance_gap",
                "class": cls,
                "superclass": parent,
                "resolved": False,
                "reason": (
                    f"unresolved: superclass {parent!r} of {cls!r} not "
                    "present in scanned tree (abstain, no inherited "
                    "rules claimed)"
                ),
                "span": {"start_line": line, "end_line": line},
            })
            resolved.append(gap)
    return resolved


def _signature_param_text(signature: str) -> str | None:
    """Parameter-list text inside the outer parens of a signature.

    Balanced-paren scan (quote-aware) so ``@Valid`` in the return position
    is never mistaken for a bound param. Returns None when the parens do
    not balance (abstain, never half-parse).
    """
    start = signature.find("(")
    if start == -1:
        return None
    depth = 0
    in_str: str | None = None
    idx = start
    while idx < len(signature):
        ch = signature[idx]
        if in_str is not None:
            if ch == "\\" and idx + 1 < len(signature):
                idx += 2
                continue
            if ch == in_str:
                in_str = None
            idx += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return signature[start + 1:idx]
        idx += 1
    return None


def has_valid_enforcement(path: Path) -> dict[str, Any]:
    """File-level enforcement verdict for declared constraints.

    ``verified`` True only with same-method evidence: a ``@Valid``/
    ``@Validated`` bound param inside the parameter list (``@Valid`` in
    the return position alone never verifies), or an explicit
    ``validator.validate(...)`` call in the body. A bare
    ``Validation.buildDefaultValidatorFactory()`` without
    ``validate(`` abstains (never verified alone). Otherwise
    ``verified`` False (declared only; never an HTTP 400 claim).

    NOTE (deferred, documented not fixed): the validator receiver name is
    fixed (``validator``); custom-named ``Validator`` variables abstain.
    Multi-line signatures abstain by design (single-line only).
    """
    file = Path(path)
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        record = dict(_base(file, 1)) if file.exists() else {
            "origin": ORIGIN,
            "extractor_version": EXTRACTOR_VERSION,
            "file": str(file),
            "line": 1,
            "content_hash": "",
        }
        record.update({
            "kind": "enforcement",
            "verified": False,
            "methods": [],
            "evidence_spans": [],
            "reason": "declared-only: file unreadable (no enforcement evidence)",
        })
        return record
    try:
        bodies = _method_bodies(file)
    except (OSError, UnicodeError):
        bodies = []
    methods: list[dict[str, Any]] = []
    for body in bodies:
        signature = "\n".join(lines[body["decl"] - 1:body["base"] - 1])
        kinds: list[tuple[str, int]] = []
        # MED2: require @Valid/@Validated inside the param list (paren
        # text), not the return position. Strip strings/comments first so
        # "@Valid" inside a literal (e.g. @RequestBody(" @Valid ")) never
        # verifies (match link_record_enforcement).
        param_text = _signature_param_text(signature)
        if param_text is not None:
            probe = _strip_string_literals(_strip_java_comments(param_text))
            if _VALID_PARAM.search(probe):
                kinds.append(("valid_param", body["decl"]))
        for offset, text in enumerate(body["lines"]):
            if _VALIDATOR_CALL.search(text):
                kinds.append(("validator_call", body["base"] + offset))
                break
        if kinds:
            start = min([body["decl"]] + [line for _, line in kinds])
            end = max([line for _, line in kinds])
            methods.append({
                "method": body["name"],
                "decl_line": body["decl"],
                "evidence": [kind for kind, _ in kinds],
                "span": {"start_line": start, "end_line": end},
            })
    verified = bool(methods)
    spans = [m["span"] for m in methods]
    record = dict(_base(file, spans[0]["start_line"] if spans else 1))
    if verified:
        detail = "; ".join(
            f"{m['method']} via {','.join(m['evidence'])} "
            f"(L{m['span']['start_line']}-L{m['span']['end_line']})"
            for m in methods
        )
        reason = f"enforced: {detail}; same-method evidence only"
    else:
        reason = (
            "declared-only: no @Valid/@Validated param or validator call "
            "in file (no enforcement evidence)"
        )
    record.update({
        "kind": "enforcement",
        "verified": verified,
        "methods": methods,
        "evidence_spans": spans,
        "reason": reason,
    })
    return record


def extract_jpa_entities(path: Path) -> list[dict[str, Any]]:
    """``@Entity`` classes: table name plus join-column declarations.

    Only classes with an ``@Entity`` marker in the annotation block
    directly above the class declaration are recorded; the table comes
    from ``@Table(name=...)`` (None when absent: abstain, never guessed).
    Each field/getter whose annotation block holds ``@JoinColumn`` yields
    a join-column entry (column from ``name=...``, None when absent) with
    its ``@ManyToOne`` presence and span.

    NOTE (deferred, maintainability out of scope): the field/getter scan
    duplicates ``extract_field_constraints``; kept separate intentionally.
    """
    records: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines, start=1):
        decl = _CLASS_DECL.match(line)
        if not decl:
            continue
        block = _preceding_annotations(lines, index)
        if not any(_ENTITY_MARKER.match(lines[n - 1]) for n, _, _ in block):
            continue
        table: str | None = None
        for n, _, _ in block:
            table_match = _TABLE_NAME.search(lines[n - 1])
            if table_match:
                table = table_match.group(1)
        joins: list[dict[str, Any]] = []
        for findex, fline in enumerate(lines, start=1):
            if findex <= index:
                continue
            field_match = _FIELD_DECL.match(fline)
            field: str | None = None
            if field_match and _is_field_line(fline):
                field = field_match.group(1)
            else:
                getter_match = _GETTER_DECL.match(fline)
                if getter_match:
                    suffix = getter_match.group(1)
                    # JavaBeans decap: getURL -> URL (second char uppercase
                    # keeps suffix), else lower first char.
                    if suffix and len(suffix) > 1 and suffix[1].isupper():
                        field = suffix
                    else:
                        field = suffix[0].lower() + suffix[1:] if suffix else None
            if not field:
                continue
            fblock = _preceding_annotations(lines, findex)
            column: str | None = None
            found_join = False
            for n, name, args in fblock:
                if name.split(".")[-1] == "JoinColumn":
                    found_join = True
                    params = _annotation_params(args)
                    column = params.get("name")
            if not found_join:
                continue
            many = any(
                _MANY_TO_ONE.match(lines[n - 1]) for n, _, _ in fblock
            )
            start = fblock[0][0] if fblock else findex
            joins.append({
                "field": field,
                "column": column,
                "many_to_one": many,
                "line": findex,
                "span": {"start_line": start, "end_line": findex},
            })
        start = block[0][0] if block else index
        record = dict(_base(path, index))
        record.update({
            "kind": "jpa_entity",
            "class": decl.group(1),
            "superclass": decl.group(2),
            "table": table,
            "join_columns": joins,
            "span": {"start_line": start, "end_line": index},
        })
        records.append(record)
    return records


def _normalize_table(name: str | None) -> str:
    """Normalized table identity: strip quotes/backticks, lower, drop _/-.

    Quoted identifiers (``"widgets"``, ```widgets```) are already covered
    by this normalization. Only table identity is normalized; column
    matching stays case-insensitive exact (documented, not normalized).
    """
    text = str(name or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"', "`"):
        text = text[1:-1]
    text = text.strip().strip("\"'`")
    return re.sub(r"[_\\-]", "", text.lower())


def link_jpa_to_sql(
    entity_records: list[dict[str, Any]],
    sql_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Link JPA entities/join columns to CREATE TABLE/FK evidence.

    One record per (entity, join column) -- plus one table-only record for
    entities declaring no join columns. ``verified`` True requires all
    three: the entity table matches exactly one ``CREATE TABLE`` (exact or
    normalized -- normalization strips quotes/backticks, lowercases, and
    removes ``_``/``-``; zero or 2+ abstain as unresolved), an FK on that
    table covers the join column, and the FK references a known table.
    Anything else abstains (``verified`` False) with a reason; ambiguity
    or absence never verifies. A bare ``col REFERENCES tbl`` without the
    ``FOREIGN KEY`` keyword is not an FK (abstain, documented, not fixed).
    """
    tables = [r for r in sql_records if r.get("kind") == "create_table"]
    fks = [r for r in sql_records if r.get("kind") == "fk_constraint"]
    known = {_normalize_table(str(t.get("table", ""))) for t in tables}
    links: list[dict[str, Any]] = []

    def _link(
        entity: dict[str, Any],
        table: dict[str, Any] | None,
        fk: dict[str, Any] | None,
        column: str | None,
        verified: bool,
        reason: str,
    ) -> dict[str, Any]:
        record = dict(_base(Path(str(entity["file"])), int(entity["line"])))
        record.update({
            "kind": "jpa_sql_link",
            "entity": entity.get("class"),
            "table": entity.get("table"),
            "column": column,
            "fk": fk,
            "verified": verified,
            "reason": reason,
            "spans": {
                "entity": entity.get("span"),
                "table": table.get("span") if table else None,
                "fk": fk.get("span") if fk else None,
            },
        })
        return record

    for entity in entity_records:
        if entity.get("kind") != "jpa_entity":
            continue
        want = entity.get("table")
        matches: list[dict[str, Any]] = []
        if want:
            want_norm = _normalize_table(str(want))
            matches = [
                t for t in tables
                if _normalize_table(str(t.get("table", ""))) == want_norm
            ]
        columns = entity.get("join_columns") or []
        if not columns:
            if not want:
                links.append(_link(
                    entity, None, None, None, False,
                    "unresolved: no @Table name and no join columns "
                    "(abstain, nothing to link)",
                ))
            elif len(matches) != 1:
                links.append(_link(
                    entity, None, None, None, False,
                    f"unresolved: table {want!r} matches "
                    f"{len(matches)} CREATE TABLE declarations "
                    "(abstain, table-only, no join columns to verify)",
                ))
            else:
                links.append(_link(
                    entity, matches[0], None, None, False,
                    f"table-only: entity {entity.get('class')!r} matches "
                    f"table {want!r} but declares no join columns "
                    "(no FK link claimed)",
                ))
            continue
        for join in columns:
            column = join.get("column")
            if not want:
                links.append(_link(
                    entity, None, None, column, False,
                    f"unresolved: entity {entity.get('class')!r} has no "
                    "@Table name (abstain, table unknown)",
                ))
            elif len(matches) == 0:
                links.append(_link(
                    entity, None, None, column, False,
                    f"unresolved: table {want!r} matches no CREATE TABLE "
                    "(abstain, table unknown)",
                ))
            elif len(matches) > 1:
                links.append(_link(
                    entity, None, None, column, False,
                    f"unresolved: table {want!r} matches {len(matches)} "
                    "CREATE TABLE declarations "
                    "(ambiguous, abstain, never verified)",
                ))
            elif not column:
                links.append(_link(
                    entity, matches[0], None, None, False,
                    f"partial: table {want!r} matches but join field "
                    f"{join.get('field')!r} declares no column name "
                    "(abstain, column unknown)",
                ))
            else:
                table = matches[0]
                wanted = str(column).lower()
                covered = [
                    fk for fk in fks
                    if str(fk.get("table", "")).lower()
                    == str(table.get("table", "")).lower()
                    and wanted in [
                        str(c).lower() for c in fk.get("columns", [])
                    ]
                ]
                if not covered:
                    links.append(_link(
                        entity, table, None, column, False,
                        f"partial: table {want!r} matches but no FK on "
                        f"that table covers column {column!r} "
                        "(abstain, FK unknown)",
                    ))
                elif len(covered) > 1:
                    links.append(_link(
                        entity, table, None, column, False,
                        f"unresolved: {len(covered)} FKs on table "
                        f"{want!r} cover column {column!r} "
                        "(ambiguous, abstain, never verified)",
                    ))
                else:
                    fk = covered[0]
                    ref = str(fk.get("ref_table") or "")
                    if ref and _normalize_table(ref) in known:
                        links.append(_link(
                            entity, table, fk, column, True,
                            f"verified: entity {entity.get('class')!r} -> "
                            f"table {want!r} "
                            f"(L{table.get('line')}) + column {column!r} "
                            f"in FK (L{fk.get('line')}) -> {ref!r}; "
                            "entity+column+FK all match",
                        ))
                    else:
                        links.append(_link(
                            entity, table, fk, column, False,
                            f"partial: entity {entity.get('class')!r} -> "
                            f"table {want!r} + column {column!r} in FK "
                            f"(L{fk.get('line')}) but ref table {ref!r} "
                            "is not a known table (abstain)",
                        ))
    return links


# ---------------------------------------------------------------- OpenAPI


def extract_openapi(path: Path) -> list[dict[str, Any]]:
    """OpenAPI path items and operations declared under ``paths:``.

    Each operation record keeps the path/method/span fields and adds
    the sibling ``operationId`` (``operation_id`` plus
    ``operation_id_line``, None when absent) and the declared
    ``responses``: one entry per status code (``status``, ``line``,
    ``headers`` as ``[{name, line}]``) plus a ``responses_span`` (None
    when no ``responses:`` block is present).
    """
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
                method_indent = len(line) - len(line.lstrip())
                operation_id: str | None = None
                operation_id_line: int | None = None
                responses: list[dict[str, Any]] = []
                responses_start: int | None = None
                responses_end: int | None = None
                resp_indent: int | None = None
                current: dict[str, Any] | None = None
                current_indent = 0
                header_indent: int | None = None
                scan = index + 1
                while scan < len(lines):
                    sline = lines[scan]
                    if sline.strip():
                        sindent = len(sline) - len(sline.lstrip())
                        if sindent <= method_indent:
                            break
                        if operation_id is None:
                            op_match = _OPERATION_ID_LINE.match(sline)
                            if op_match:
                                operation_id = op_match.group(1).strip().strip("'\"")
                                operation_id_line = scan + 1
                        if resp_indent is None:
                            if _RESPONSES_LINE.match(sline):
                                resp_indent = sindent
                                responses_start = scan + 1
                                responses_end = scan + 1
                        else:
                            status_match = (
                                _STATUS_LINE.match(sline)
                                if sindent > resp_indent
                                else None
                            )
                            if status_match:
                                current = {
                                    "status": status_match.group(1),
                                    "line": scan + 1,
                                    "headers": [],
                                }
                                responses.append(current)
                                current_indent = sindent
                                header_indent = None
                                responses_end = scan + 1
                            elif current is not None:
                                if header_indent is None:
                                    if (
                                        sindent > current_indent
                                        and _HEADERS_LINE.match(sline)
                                    ):
                                        header_indent = sindent
                                        responses_end = scan + 1
                                else:
                                    if sindent > header_indent:
                                        hname = _HEADER_NAME_LINE.match(sline)
                                        if hname:
                                            current["headers"].append({
                                                "name": hname.group(1),
                                                "line": scan + 1,
                                            })
                                            responses_end = scan + 1
                                    else:
                                        header_indent = None
                    scan += 1
                records.append({
                    **_base(path, index + 1),
                    "kind": "openapi_operation",
                    "openapi_path": current_path,
                    "method": method_match.group(1),
                    "operation_id": operation_id,
                    "operation_id_line": operation_id_line,
                    "responses": responses,
                    "responses_span": (
                        {"start_line": responses_start, "end_line": responses_end}
                        if responses_start is not None
                        else None
                    ),
                    "span": {"start_line": path_start, "end_line": index + 1},
                })
            index += 1
            continue
        index += 1
    return records


# ---------------------------------------------------------------- Java interfaces and implementations


_INTERFACE_DECL = re.compile(
    r"^\s*(?:(?:public|protected|private|abstract|sealed)\s+)*interface\s+([A-Za-z_][\w]*)"
)
_INTERFACE_METHOD = re.compile(
    r"^\s*(?:(?:public|protected|private|static|default|abstract|final|synchronized)\s+)*"
    r"(?:<[^;{}]*>\s*)?"
    r"([\w<>\[\]\?,\s\.@]+?)\s+([A-Za-z_][\w]*)\s*\(([^;{}]*)\)"
    r"\s*(?:throws\s+[\w\.\s,]+)?;\s*$"
)
_CLASS_IMPLEMENTS = re.compile(
    r"^\s*(?:(?:public|protected|private|abstract|final|sealed)\s+)*"
    r"class\s+([A-Za-z_][\w]*)"
    r"(?:\s+extends\s+[\w\.,\s<>?]+?)?"
    r"\s+implements\s+([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*)"
)
_MODIFIERS = re.compile(
    r"^(?:\s*(?:public|private|protected|static|final|abstract|synchronized|native|default|transient|volatile)\s+)+"
)
_MAPPING_ANNOTATION = re.compile(
    r"@(?:(Get|Post|Put|Delete|Patch)Mapping|RequestMapping)"
    r"(?:\(\s*(?:value\s*=\s*)?\"([^\"]*)\"[^)]*\))?"
)
_HTTP_STATUS_ENUM = re.compile(r"\bHttpStatus\.([A-Z][A-Z0-9_]*)")
_STATUS_ENUM_TO_CODE = {
    "OK": 200,
    "CREATED": 201,
    "ACCEPTED": 202,
    "NO_CONTENT": 204,
    "BAD_REQUEST": 400,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "UNPROCESSABLE_ENTITY": 422,
    "INTERNAL_SERVER_ERROR": 500,
}
_RESPONSE_STATUS_CALL = re.compile(
    r"\bResponseEntity\s*\.\s*status\s*\(\s*(\d{3})\s*\)"
)
_CREATED_CALL = re.compile(r"\.\s*created\s*\(")
_HEADER_CALL = re.compile(r"\.\s*header\s*\(\s*\"([^\"]+)\"")
_SET_LOCATION_CALL = re.compile(r"\.\s*setLocation\s*\(")
_HEADERS_SET_CALL = re.compile(r"\.\s*set\s*\(\s*\"([^\"]+)\"\s*,")
_MAPPING_METHOD = {
    "Get": "get",
    "Post": "post",
    "Put": "put",
    "Delete": "delete",
    "Patch": "patch",
}


def _strip_modifiers(prefix: str) -> str:
    """Remove leading Java modifiers from a declaration prefix."""
    text = prefix.strip()
    while True:
        stripped = _MODIFIERS.sub("", text, count=1).strip()
        if stripped == text:
            return text
        text = stripped


def _decl_return_type(decl_line: str, name: str) -> str | None:
    """Return type preceding ``name(`` on a declaration line (None for ctors)."""
    pos = decl_line.find(name + "(")
    if pos == -1:
        return None
    remainder = _strip_modifiers(decl_line[:pos])
    remainder = re.sub(r"^<[^;{}]*>\s*", "", remainder).strip()
    return remainder or None


def extract_java_interfaces(path: Path) -> list[dict[str, Any]]:
    """``interface X`` declarations plus ``;``-terminated method signatures.

    Each record names the interface, the method, its raw return type and
    arity, with file/line/span evidence (regex only, no solver; generic
    method type parameters are tolerated, multi-line signatures abstain).
    """
    records: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    current: str | None = None
    current_line = 0
    depth = 0
    body_depth: int | None = None
    for index, line in enumerate(lines, start=1):
        if current is None:
            decl = _INTERFACE_DECL.match(line)
            if decl:
                current = decl.group(1)
                current_line = index
                depth += line.count("{") - line.count("}")
                body_depth = depth if "{" in line else None
            continue
        if body_depth is None:
            depth += line.count("{") - line.count("}")
            if "{" in line:
                body_depth = depth
            elif re.match(r"^\s*;\s*$", line) or not line.strip():
                continue
            else:
                current = None
            continue
        if depth == body_depth:
            sig = _INTERFACE_METHOD.match(line)
            if sig and "(" in line:
                params = sig.group(3).strip()
                record = dict(_base(path, index))
                record.update({
                    "kind": "java_interface",
                    "interface": current,
                    "interface_line": current_line,
                    "method": sig.group(2),
                    "return_type": sig.group(1).strip(),
                    "arity": _param_count(params),
                    "params": params,
                    "span": {"start_line": index, "end_line": index},
                })
                records.append(record)
        depth += line.count("{") - line.count("}")
        if depth < (body_depth or 0):
            current = None
            body_depth = None
    return records


def extract_implements(path: Path) -> list[dict[str, Any]]:
    """``class C implements I,...`` plus its method definitions.

    Methods come from :func:`_method_bodies` ( reused, so only braced
    bodies count); each record carries the impl class, the declared
    interface names, method name/arity/return type, ``@Override``
    presence (annotation within the three lines above the declaration),
    and the body span.
    """
    records: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    decls: list[tuple[int, str, list[str]]] = []
    for index, line in enumerate(lines, start=1):
        match = _CLASS_IMPLEMENTS.match(line)
        if match:
            decls.append((
                index,
                match.group(1),
                [part.strip() for part in match.group(2).split(",")],
            ))
    if not decls:
        return []
    try:
        bodies = _method_bodies(path)
    except (OSError, UnicodeError):
        return []
    for body in bodies:
        owner: tuple[int, str, list[str]] | None = None
        for decl in decls:
            if decl[0] < body["decl"]:
                owner = decl
            else:
                break
        if owner is None:
            continue
        decl_line = lines[body["decl"] - 1]
        override = any(
            lines[k].strip().startswith("@Override")
            for k in range(max(0, body["decl"] - 4), body["decl"] - 1)
        )
        end_line = body["base"] + len(body["lines"]) - 1
        record = dict(_base(path, body["decl"]))
        record.update({
            "kind": "java_implementation",
            "class": owner[1],
            "interfaces": list(owner[2]),
            "method": body["name"],
            "arity": int(body.get("param_count", 0)),
            "return_type": _decl_return_type(decl_line, body["name"]),
            "override": override,
            "span": {"start_line": body["decl"], "end_line": max(end_line, body["decl"])},
        })
        records.append(record)
    return records


def _impl_response_construction(
    file_text: list[str], start: int, end: int
) -> dict[str, Any] | None:
    """Response-construction evidence inside impl body lines (1-based span).

    Detects ``ResponseEntity.status(NNN)``, ``HttpStatus.X`` enums (mapped
    to codes), ``.created(`` (201), ``.header("Name")``,
    ``.setLocation(`` (Location) and ``HttpHeaders.set("Name", ...)``.
    Returns None when nothing is found (abstain, never synthesize).
    """
    statuses: list[dict[str, Any]] = []
    headers: list[dict[str, Any]] = []
    for num in range(start, end + 1):
        if not 1 <= num <= len(file_text):
            continue
        text = file_text[num - 1]
        for match in _RESPONSE_STATUS_CALL.finditer(text):
            statuses.append({"status": int(match.group(1)), "line": num})
        for match in _HTTP_STATUS_ENUM.finditer(text):
            code = _STATUS_ENUM_TO_CODE.get(match.group(1))
            if code is not None:
                statuses.append({"status": code, "line": num})
        if _CREATED_CALL.search(text) and not any(
            s["line"] == num and s["status"] == 201 for s in statuses
        ):
            statuses.append({"status": 201, "line": num})
        for match in _HEADER_CALL.finditer(text):
            headers.append({"name": match.group(1), "line": num})
        if _SET_LOCATION_CALL.search(text):
            headers.append({"name": "Location", "line": num})
        for match in _HEADERS_SET_CALL.finditer(text):
            headers.append({"name": match.group(1), "line": num})
    if not statuses and not headers:
        return None
    bound = [entry["line"] for entry in statuses + headers]
    return {
        "statuses": statuses,
        "headers": headers,
        "span": {"start_line": min(bound), "end_line": max(bound)},
    }


def _impl_mapping_clues(
    file_text: list[str], decl_line: int
) -> tuple[str | None, str | None]:
    """Method-level mapping annotation just above ``decl_line``.

    Returns ``(http_method, path)`` where each is None when the
    annotation is absent (RequestMapping without a method yields a None
    method, never a guess).
    """
    for num in range(max(1, decl_line - 3), decl_line):
        if not 1 <= num <= len(file_text):
            continue
        match = _MAPPING_ANNOTATION.search(file_text[num - 1])
        if match:
            kind, value = match.group(1), match.group(2)
            method = _MAPPING_METHOD.get(kind) if kind else None
            return method, value
    return None, None


def bind_operation_to_implementation(
    openapi_op: dict[str, Any],
    interfaces: list[dict[str, Any]],
    implementations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind one OpenAPI operation to its Java implementation, if provable.

    ``full`` requires all three: (a) the operationId names an interface
    method from a present interface file, (b) a class implementing that
    interface defines a same-name method with compatible (equal) arity,
    and (c) at least one corroboration -- the OpenAPI path cited in the
    impl file or a matching interface/impl return type -- plus an impl
    status inside the declared responses. A missing interface file is
    ``partial`` (declared + impl present, interface unresolved, never
    full); name-only matches (arity/signature mismatch, path/method
    contradiction, or no corroboration) are ``unresolved``; two or more
    implementors abstain as ambiguous; an impl status outside the
    declared responses is ``partial`` with ``response_supported`` False.
    Client invocation, destination, and delivery are never proved
    (explicit False fields).
    """
    try:
        op_file = Path(str(openapi_op.get("file", "")))
        op_base = dict(_base(op_file, int(openapi_op.get("line", 1))))
    except (OSError, UnicodeError, ValueError, TypeError):
        op_base = {
            "origin": ORIGIN,
            "extractor_version": EXTRACTOR_VERSION,
            "file": str(openapi_op.get("file", "")),
            "line": openapi_op.get("line", 1),
            "content_hash": "",
        }
    declared = {
        "method": openapi_op.get("method"),
        "path": openapi_op.get("openapi_path"),
        "operation_id": openapi_op.get("operation_id"),
        "responses": openapi_op.get("responses", []),
        "spans": {
            "operation": openapi_op.get("span"),
            "operation_id_line": openapi_op.get("operation_id_line"),
            "responses_span": openapi_op.get("responses_span"),
        },
    }
    not_proved = (
        "operation match only (path+method/name/signature evidence); "
        "client invocation, destination, and delivery are NOT proved"
    )

    def _result(
        binding: str,
        reason: str,
        interface: dict[str, Any] | None,
        implementation: dict[str, Any] | None,
        response_construction: dict[str, Any] | None,
        response_supported: bool,
    ) -> dict[str, Any]:
        record = dict(op_base)
        record.update({
            "kind": "operation_binding",
            "declared": declared,
            "interface": interface,
            "implementation": implementation,
            "response_construction": response_construction,
            "response_supported": response_supported,
            "binding": binding,
            "verified": binding == "full",
            "reason": f"{reason}; {not_proved}",
            "client_invocation_proved": False,
            "destination_verified": False,
            "delivery_proved": False,
            "span": openapi_op.get("span", {
                "start_line": openapi_op.get("line", 1),
                "end_line": openapi_op.get("line", 1),
            }),
        })
        return record

    op_id = openapi_op.get("operation_id")
    if not op_id or not str(op_id).strip():
        return _result(
            "unresolved", "no bind: operation has no operationId",
            None, None, None, False,
        )
    op_id = str(op_id)
    iface_hits = [i for i in interfaces if i.get("method") == op_id]
    impl_hits = [m for m in implementations if m.get("method") == op_id]

    def _construction(
        impl: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        info = {
            "file": impl.get("file"),
            "line": impl.get("line"),
            "signature": (
                f"{impl.get('return_type')} {impl.get('method')}"
                f"({impl.get('arity')} params)"
                if impl.get("return_type") else None
            ),
            "class": impl.get("class"),
            "override": impl.get("override"),
        }
        try:
            text = Path(str(impl.get("file"))).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return info, None
        span = impl.get("span") or {}
        start = int(span.get("start_line", impl.get("line", 1)))
        end = int(span.get("end_line", start))
        return info, _impl_response_construction(text, start, end)

    def _supported(
        declared_responses: list[dict[str, Any]],
        construction: dict[str, Any] | None,
    ) -> bool:
        if not construction:
            return False
        declared_codes = {
            str(r.get("status")) for r in declared_responses or []
        }
        if "default" in declared_codes:
            return bool(construction.get("statuses"))
        return any(
            str(s.get("status")) in declared_codes
            for s in construction.get("statuses", [])
        )

    if not iface_hits:
        if not impl_hits:
            return _result(
                "unresolved",
                f"no bind: operationId {op_id!r} matches no source "
                "interface method and no implementation method",
                {"file": None, "line": None,
                 "reason": "interface-unresolved: no source interface declares "
                 f"{op_id!r}"},
                None, None, False,
            )
        distinct = {(m.get("file"), m.get("class")) for m in impl_hits}
        if len(distinct) > 1:
            return _result(
                "unresolved",
                f"no verified bind: {len(distinct)} implementations share "
                f"method name {op_id!r} with no resolving interface "
                "(ambiguous-multiple-implementations, abstain)",
                {"file": None, "line": None,
                 "reason": "interface-unresolved: no source interface declares "
                 f"{op_id!r}"},
                None, None, False,
            )
        impl = impl_hits[0]
        info, construction = _construction(impl)
        return _result(
            "partial",
            f"partial: operationId {op_id!r} matches implementation "
            f"{impl.get('class')}.{impl.get('method')} but no source "
            "interface file declares it (interface-unresolved, never full)",
            {"file": None, "line": None,
             "reason": "interface-unresolved: no source interface declares "
             f"{op_id!r}"},
            info, construction,
            _supported(declared["responses"], construction),
        )

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for iface in iface_hits:
        for impl in impl_hits:
            if iface.get("interface") in (impl.get("interfaces") or []) and impl.get(
                "arity"
            ) == iface.get("arity"):
                pairs.append((iface, impl))
    if len({(p[1].get("file"), p[1].get("class")) for p in pairs}) > 1:
        return _result(
            "unresolved",
            f"no verified bind: {len(pairs)} implementations match interface "
            f"method {op_id!r} (ambiguous-multiple-implementations, abstain)",
            {"file": iface_hits[0].get("file"), "line": iface_hits[0].get("line")},
            None, None, False,
        )
    if len(pairs) == 1:
        iface, impl = pairs[0]
        info, construction = _construction(impl)
        try:
            file_text = Path(str(impl.get("file"))).read_text(
                encoding="utf-8"
            ).splitlines()
            op_path = str(openapi_op.get("openapi_path") or "")
            op_method = str(openapi_op.get("method") or "").strip().lower()
            impl_line = int(impl.get("line", 1))
            map_method, map_path = _impl_mapping_clues(file_text, impl_line)
            if map_method and op_method and map_method != op_method:
                return _result(
                    "unresolved",
                    f"no bind: name {op_id!r} matches interface "
                    f"{iface.get('interface')} and implementation "
                    f"{impl.get('class')} but method contradicts "
                    f"(impl @{map_method.upper()} vs openapi "
                    f"{op_method.upper()}; mapping contradiction, no bind)",
                    {"file": iface.get("file"), "line": iface.get("line")},
                    info, construction,
                    _supported(declared["responses"], construction),
                )
            span = impl.get("span") or {}
            try:
                span_start = int(span.get("start_line", impl_line))
            except (TypeError, ValueError):
                span_start = impl_line
            try:
                span_end = int(span.get("end_line", span_start))
            except (TypeError, ValueError):
                span_end = span_start
            quoted_double = f'"{op_path}"' if op_path else ""
            quoted_single = f"'{op_path}'" if op_path else ""
            scope_nums = set(range(span_start, span_end + 1)) | set(
                range(max(1, impl_line - 3), impl_line)
            )
            path_lines: list[int] = sorted(
                num
                for num in scope_nums
                if 1 <= num <= len(file_text)
                and bool(op_path)
                and (
                    (quoted_double and quoted_double in file_text[num - 1])
                    or (quoted_single and quoted_single in file_text[num - 1])
                )
            )
            path_match = bool(path_lines)
            body_cited = any(
                1 <= num <= len(file_text)
                and bool(op_path)
                and (
                    (quoted_double and quoted_double in file_text[num - 1])
                    or (quoted_single and quoted_single in file_text[num - 1])
                )
                for num in range(span_start, span_end + 1)
            )
            if (
                map_path
                and op_path
                and map_path != op_path
                and not body_cited
            ):
                return _result(
                    "unresolved",
                    f"no bind: name {op_id!r} matches interface "
                    f"{iface.get('interface')} and implementation "
                    f"{impl.get('class')} but path contradicts "
                    f"(impl {map_path!r} vs openapi {op_path!r}; "
                    "mapping contradiction, no bind)",
                    {"file": iface.get("file"), "line": iface.get("line")},
                    info, construction,
                    _supported(declared["responses"], construction),
                )
        except (OSError, UnicodeError, ValueError, TypeError):
            file_text = []
            path_match = False
            path_lines = []
        iface_ret = str(iface.get("return_type") or "").strip()
        impl_ret = str(impl.get("return_type") or "").strip()
        return_match = bool(iface_ret) and iface_ret == impl_ret
        if not (path_match or return_match):
            return _result(
                "unresolved",
                f"no bind: name {op_id!r} matches interface "
                f"{iface.get('interface')} and implementation "
                f"{impl.get('class')} but no corroboration (no path citation, "
                "no return-type match; name-only, abstain)",
                {"file": iface.get("file"), "line": iface.get("line")},
                info, construction,
                _supported(declared["responses"], construction),
            )
        supported = _supported(declared["responses"], construction)
        if not supported:
            return _result(
                "partial",
                f"partial: {op_id!r} binds interface "
                f"{iface.get('interface')} to {impl.get('class')} "
                "(path/return corroborated) but the constructed status is "
                "outside the declared responses (response-unsupported)",
                {"file": iface.get("file"), "line": iface.get("line")},
                info, construction, False,
            )
        return _result(
            "full",
            f"full: operationId {op_id!r} == interface "
            f"{iface.get('interface')}.{iface.get('method')} "
            f"(arity {iface.get('arity')}) == implementation "
            f"{impl.get('class')}.{impl.get('method')}"
            + (
                f" with path citation (lines {path_lines[0]}-{path_lines[-1]})"
                if path_match
                else ""
            )
            + (
                " with return-type match"
                if return_match
                else ""
            ),
            {"file": iface.get("file"), "line": iface.get("line")},
            info, construction, True,
        )

    same_name = impl_hits
    arity_clash = any(
        m.get("arity") != iface.get("arity")
        for m in same_name
        for iface in iface_hits
        if iface.get("interface") in (m.get("interfaces") or [])
    )
    if arity_clash:
        cause = (
            f"name {op_id!r} matches but arity/signature mismatches "
            "(name-only, no bind)"
        )
    else:
        contradiction: str | None = None
        for m in same_name:
            try:
                text = Path(str(m.get("file"))).read_text(
                    encoding="utf-8"
                ).splitlines()
            except (OSError, UnicodeError):
                continue
            map_method, map_path = _impl_mapping_clues(text, int(m.get("line", 1)))
            op_method = str(openapi_op.get("method") or "").strip().lower()
            op_path = str(openapi_op.get("openapi_path") or "")
            if map_method and op_method and map_method != op_method:
                contradiction = (
                    f"method contradicts (impl @{map_method.upper()} vs "
                    f"openapi {op_method.upper()})"
                )
                break
            if (
                map_path
                and op_path
                and map_path != op_path
                and not any(op_path in line for line in text)
            ):
                contradiction = (
                    f"path contradicts (impl {map_path!r} vs openapi {op_path!r})"
                )
                break
        cause = (
            f"name {op_id!r} matches but {contradiction} (name-only, no bind)"
            if contradiction
            else f"name {op_id!r} declared by an interface but no class "
            "implements it with a compatible signature (no-implementor, no bind)"
        )
    return _result(
        "unresolved", f"no bind: {cause}",
        {"file": iface_hits[0].get("file"), "line": iface_hits[0].get("line")},
        None, None, False,
    )


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


def _fluent_assign_sites(lines: list[str], base: str) -> list[int]:
    """1-based lines assigning ``base`` (any right-hand side).

    Counts ``base = ...`` and ``this.base = ...`` while excluding ``==``.
    A field initializer plus a setter-body assignment therefore counts as
    two sites (reassignable: abstain), even when only one assigns a literal.
    """
    pattern = re.compile(
        r"(?<![=!<>])\b(?:this\s*\.\s*)?" + re.escape(base) + r"\s*=(?![=>])"
    )
    return [num for num, text in enumerate(lines, start=1) if pattern.search(text)]


def _fluent_receivers(
    lines: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    """WebClient/RestClient receivers: typed vars plus builder-built vars.

    ``allowed`` maps each variable declared with a ``WebClient``/
    ``RestClient`` (or ``.Builder``) type to its declaration line;
    ``built`` maps each ``<var> = <allowed>.build()`` target to its line.
    """
    allowed: dict[str, int] = {}
    for num, text in enumerate(lines, start=1):
        for match in _FLUENT_TYPE_DECL.finditer(text):
            allowed.setdefault(match.group(1), num)
    built: dict[str, int] = {}
    for num, text in enumerate(lines, start=1):
        for match in _FLUENT_BUILD_ASSIGN.finditer(text):
            if match.group(2) in allowed:
                built.setdefault(match.group(1), num)
    return allowed, built


def _fluent_root_ok(
    segment: str, allowed: dict[str, int], built: dict[str, int]
) -> tuple[str | None, int | None]:
    """Receiver check for one fluent statement segment.

    Returns ``(receiver, decl_line)`` when the segment chains a declared
    ``WebClient``/``RestClient`` (or ``.Builder``) variable -- optionally via
    ``.build()`` -- or a builder-built variable into a bare fluent verb
    call, or uses ``WebClient.create(...)``/``WebClient.builder()`` (or the
    ``RestClient`` equivalents) inline (decl line None). Anything else
    (arbitrary ``receiver.uri(...)``) yields ``(None, None)`` (abstain,
    never a record).
    """
    for name, decl in {**allowed, **built}.items():
        if re.search(
            r"\b" + re.escape(name) + r"\s*\.\s*"
            r"(?:build\s*\(\s*\)\s*\.\s*)?"
            r"(?:get|post|put|delete|patch)\s*\(\s*\)",
            segment,
        ):
            return name, decl
    if _FLUENT_CREATE.search(segment):
        return "create", None
    return None, None


def _fluent_uri_args(line: str, col: int) -> str | None:
    """Balanced-paren argument text for the ``.uri(`` call at ``col``.

    Single-line only; ``None`` when the parens do not balance on the line
    (abstain, never half-parse).
    """
    pos = line.find("(", col)
    if pos == -1:
        return None
    depth = 0
    in_str: str | None = None
    for idx in range(pos, len(line)):
        ch = line[idx]
        if in_str is not None:
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return line[pos + 1:idx]
    return None


def extract_fluent_client_urls(path: Path) -> list[dict[str, Any]]:
    """Fluent WebClient/RestClient ``.verb().uri(...)`` client URLs.

    A record needs all three in one method: the file must mention
    ``WebClient`` or ``RestClient`` (type gate: a custom builder with its own
    ``.uri()`` never counts), a bare fluent verb (``.get()``/``.post()``/
    ``.put()``/``.delete()``/``.patch()``) chained from a declared
    ``WebClient``/``RestClient`` (or ``.Builder``) receiver -- optionally via
    ``.build()`` -- or a builder-built variable (arbitrary receivers
    abstain), and a ``.uri(...)`` argument that is either an absolute
    ``http(s)`` literal or ``<base> + "<path>"`` where ``base`` has exactly
    one assignment site in the file resolving to a single literal
    (reassigned -- e.g. field initializer plus setter -- dynamic, or
    unresolved bases abstain with no record). Query strings are kept on the
    cited expression while ``urlparse`` path matching strips them; verb-less
    chains (unknown method) yield no record (never compatible True).
    Only single-line ``.uri(...)`` calls are parsed: a call whose parens do
    not balance on one line abstains (never half-parsed). A literal or
    ``base + "<path>"`` argument with trailing ``+ <dynamic>`` content after
    the match (``.uri("http://h/api/" + id)``,
    ``.uri(base + "/api/orders/" + orderId)``) is dynamic: abstains with no
    record (never a truncated URL); only an empty remainder or template
    variables (``.uri(".../{id}", id)``) are accepted. A ``base`` assigned
    after its use site (use-before-def) abstains rather than emitting an
    inverted span.

    Records reuse the ``client_url`` shape (``url``, ``var``, ``method`` from
    the fluent verb, ``call_line``/``call_text`` at the ``.uri()`` site,
    ``receiver`` evidence, ``base_span`` plus a ``span`` covering base
    definition through call site) plus ``fluent: True`` so the linkers pick
    up the carried invocation evidence as declared-only candidates
    (``destination_verified`` False, ``equivalent`` never True).
    """
    file = Path(path)
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    if not re.search(r"\b(?:WebClient|RestClient)\b", "\n".join(lines)):
        return []
    allowed, built = _fluent_receivers(lines)
    if not allowed and not _FLUENT_CREATE.search("\n".join(lines)):
        return []
    try:
        bodies = _method_bodies(file)
    except (OSError, UnicodeError):
        bodies = []
    scopes: list[tuple[int, list[str]]] = []
    if bodies:
        for body in bodies:
            scopes.append((int(body["base"]), list(body["lines"])))
    else:
        scopes.append((1, lines))
    records: list[dict[str, Any]] = []
    for base, body in scopes:
        verbs: list[tuple[int, str]] = []
        uris: list[tuple[int, int]] = []
        for offset, text in enumerate(body):
            lineno = base + offset
            for match in _FLUENT_VERB.finditer(text):
                verbs.append((lineno, str(match.group(1)).lower()))
            for match in _FLUENT_URI.finditer(text):
                uris.append((lineno, match.start()))
        for lineno, col in uris:
            args = _fluent_uri_args(lines[lineno - 1], col)
            if args is None:
                continue
            verb: str | None = None
            verb_line: int | None = None
            for vline, vname in verbs:
                if vline > lineno:
                    continue
                if any(vline < ulineno < lineno for ulineno, _ in uris):
                    continue
                if vline == lineno:
                    prefix = lines[vline - 1][:col]
                    if not re.search(
                        r"\." + re.escape(vname) + r"\s*\(\s*\)", prefix
                    ):
                        continue
                before_uri = "".join(lines[vline - 1:lineno]).split(".uri(")[0]
                if ";" in before_uri:
                    continue
                verb, verb_line = vname, vline
            if verb is None or verb_line is None:
                continue
            start = verb_line
            for back in range(verb_line - 1, 0, -1):
                if re.search(r"[;{}]", lines[back - 1]):
                    start = back + 1
                    break
                start = back
            segment = "\n".join(lines[start - 1:lineno])
            receiver, decl_line = _fluent_root_ok(segment, allowed, built)
            if receiver is None:
                continue
            uri_expr = args.strip()
            base_var: str | None = None
            base_span: dict[str, int] | None = None
            literal = _FLUENT_LITERAL_ARG.match(args)
            if literal is not None:
                if args[literal.end():].strip().startswith("+"):
                    continue
                full = literal.group(1)
                if not full.startswith(("http://", "https://")):
                    continue
            else:
                concat = _FLUENT_CONCAT_ARG.match(args)
                if concat is None:
                    continue
                if args[concat.end():].strip().startswith("+"):
                    continue
                base_var = concat.group(1)
                suffix = concat.group(2)
                if "://" in suffix:
                    continue
                sites = _fluent_assign_sites(lines, base_var)
                if len(sites) != 1:
                    continue
                if sites[0] > lineno:
                    continue
                lit = _resolve_base_literal(lines, base_var)
                if lit is None:
                    continue
                full = lit.rstrip("/") + "/" + suffix.lstrip("/")
                base_span = {"start_line": sites[0], "end_line": sites[0]}
            parsed = urlparse(full)
            if parsed.scheme not in ("http", "https"):
                continue
            if not parsed.path.startswith("/"):
                continue
            call_text = lines[lineno - 1].strip()
            record = dict(_base(file, lineno))
            record.update({
                "kind": "client_url",
                "url": full,
                "var": base_var,
                "method": verb,
                "client_method": verb,
                "call_line": lineno,
                "call_text": call_text,
                "receiver": receiver,
                "receiver_line": decl_line,
                "uri_expr": uri_expr,
                "verb_line": verb_line,
                "base_span": base_span,
                "span": {
                    "start_line": base_span["start_line"] if base_span else lineno,
                    "end_line": lineno,
                },
                "fluent": True,
            })
            records.append(record)
    return records


# ---------------------------------------------------------------- TS/JS HTTP clients

_TS_HTTP_VERBS = ("get", "post", "put", "delete", "patch")
_TS_DUMMY_HOST = "http://ts.local"

# Receiver-name only (no type gate): a shadowed ``http`` (e.g. a local
# ``foohttp`` or ``other.http``) that textually matches would otherwise
# mis-emit -- out of scope by design; prefer the type-gated Java
# RestTemplate pattern where types are available. The ``(?<![\w$.])``
# guard mirrors fetch/axios so ``foohttp.get(...)`` / ``other.http.get(...)``
# abstain (no match on the inner ``http.get``).
# Limitation (abstain-by-design, no crash): nested generics such as
# ``get<Foo<Bar>>`` do not match (single-level ``<...>`` only) and yield
# no record.
_TS_HTTPCLIENT_CALL = re.compile(
    r"(?<![\w$.])(?P<recv>(?:this\s*\.\s*)?(?:http|httpClient))"
    r"\s*\.\s*(?P<verb>get|post|put|delete|patch)"
    r"\s*(?:<[^()<>]*>)?\s*\("
)
_TS_FETCH_CALL = re.compile(r"(?<![\w$.])fetch\s*\(")
_TS_AXIOS_CALL = re.compile(
    r"(?<![\w$.])axios\s*\.\s*(?P<verb>get|post|put|delete|patch)"
    r"\s*(?:<[^()<>]*>)?\s*\("
)
_TS_AXIOS_CONFIG = re.compile(r"(?<![\w$.])axios\s*\(")
_TS_NAME = re.compile(
    r"^(?:this\s*\.\s*)?[A-Za-z_$][\w$]*"
    r"(?:\s*\.\s*[A-Za-z_$][\w$]*)*$"
)
_TS_METHOD_KV = re.compile(r"method\s*:\s*['\"]([A-Za-z]+)['\"]")
_TS_URL_KV = re.compile(r"\burl\s*:")
_TS_DOTTED = re.compile(r"^[A-Za-z_$][\w$]*(\s*\.\s*[A-Za-z_$][\w$]*)+$")


def _blank_ts_ignorable(text: str, blank_strings: bool) -> str:
    """Same-length blanking of TS comments (plus strings when asked).

    Newlines are kept so offsets map 1:1 back to the original text.
    Template literals honor ``${...}`` nesting: literal characters are
    blanked but embedded expressions stay code. Comment-stripped text
    keeps strings intact (for URL extraction); fully-masked text blanks
    both (for call/assignment detection, so a string like
    ``"http.post('/x')"`` or a ``// http.get('/x')`` comment never emits).
    """
    out = list(text)
    n = len(text)
    stack: list[str] = ["code"]
    expr_braces: list[int] = []
    i = 0
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        top = stack[-1]
        if top == "code":
            if ch in ("'", '"'):
                stack.append(ch)
                if blank_strings:
                    out[i] = " "
                i += 1
                continue
            if ch == "`":
                stack.append(ch)
                if blank_strings:
                    out[i] = " "
                i += 1
                continue
            if ch == "/" and nxt == "/":
                while i < n and text[i] != "\n":
                    out[i] = " "
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                out[i] = " "
                if i + 1 < n:
                    out[i + 1] = " "
                i += 2
                while i < n and not (
                    text[i] == "*" and i + 1 < n and text[i + 1] == "/"
                ):
                    if text[i] != "\n":
                        out[i] = " "
                    i += 1
                if i < n:
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                continue
            if ch == "{" and expr_braces:
                expr_braces[-1] += 1
                i += 1
                continue
            if ch == "}" and expr_braces:
                if expr_braces[-1] > 0:
                    expr_braces[-1] -= 1
                else:
                    expr_braces.pop()
                    stack.pop()
                i += 1
                continue
            i += 1
            continue
        if top in ("'", '"'):
            if ch == "\\" and i + 1 < n:
                if blank_strings:
                    out[i] = " "
                    if text[i + 1] != "\n":
                        out[i + 1] = " "
                i += 2
                continue
            if ch == top:
                if blank_strings:
                    out[i] = " "
                stack.pop()
            elif blank_strings and ch != "\n":
                out[i] = " "
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            if blank_strings:
                out[i] = " "
                if text[i + 1] != "\n":
                    out[i + 1] = " "
            i += 2
            continue
        if ch == "`":
            if blank_strings:
                out[i] = " "
            stack.pop()
            i += 1
            continue
        if ch == "$" and nxt == "{":
            if blank_strings:
                out[i] = " "
                out[i + 1] = " "
            stack.append("code")
            expr_braces.append(0)
            i += 2
            continue
        if blank_strings and ch != "\n":
            out[i] = " "
        i += 1
    return "".join(out)


def _ts_balanced_args(text: str, open_idx: int) -> str | None:
    """Argument text for the ``(`` at ``open_idx`` (string/template aware).

    ``None`` when the parens never balance (abstain, never half-parse);
    calls may span lines (offsets map back via line starts).
    """
    depth = 0
    stack: list[str] = ["code"]
    expr_braces: list[int] = []
    start: int | None = None
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        top = stack[-1]
        if top == "code":
            if ch in ("'", '"', "`"):
                stack.append(ch)
                i += 1
                continue
            if ch == "(":
                depth += 1
                if depth == 1:
                    start = i + 1
                i += 1
                continue
            if ch == ")":
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start:i]
                if depth < 0:
                    return None
                i += 1
                continue
            if ch == "{" and expr_braces:
                expr_braces[-1] += 1
                i += 1
                continue
            if ch == "}" and expr_braces:
                if expr_braces[-1] > 0:
                    expr_braces[-1] -= 1
                else:
                    expr_braces.pop()
                    stack.pop()
                i += 1
                continue
            i += 1
            continue
        if top in ("'", '"'):
            if ch == "\\":
                i += 2
                continue
            if ch == top:
                stack.pop()
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            stack.pop()
            i += 1
            continue
        if ch == "$" and nxt == "{":
            stack.append("code")
            expr_braces.append(0)
            i += 2
            continue
        i += 1
    return None


def _ts_split_top(text: str, sep: str) -> list[str] | None:
    """Split on top-level ``sep`` (string/template/paren/brace aware).

    ``None`` when a string/template is left unterminated (abstain).
    """
    parts: list[str] = []
    depth_paren = depth_brack = depth_brace = 0
    stack: list[str] = ["code"]
    expr_braces: list[int] = []
    current: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        top = stack[-1]
        if top == "code":
            if ch in ("'", '"', "`"):
                stack.append(ch)
                current.append(ch)
                i += 1
                continue
            if ch in ("(", "[", "{"):
                if ch == "(":
                    depth_paren += 1
                elif ch == "[":
                    depth_brack += 1
                else:
                    depth_brace += 1
                    if expr_braces:
                        expr_braces[-1] += 1
                current.append(ch)
                i += 1
                continue
            if ch in (")", "]", "}"):
                if ch == ")" and depth_paren > 0:
                    depth_paren -= 1
                elif ch == "]" and depth_brack > 0:
                    depth_brack -= 1
                elif ch == "}":
                    if expr_braces and expr_braces[-1] > 0:
                        expr_braces[-1] -= 1
                        depth_brace -= 1
                    elif expr_braces:
                        expr_braces.pop()
                        stack.pop()
                        current.append(ch)
                        i += 1
                        continue
                    elif depth_brace > 0:
                        depth_brace -= 1
                    else:
                        return None
                else:
                    return None
                current.append(ch)
                i += 1
                continue
            if (
                ch == sep
                and depth_paren == 0
                and depth_brack == 0
                and depth_brace == 0
            ):
                parts.append("".join(current))
                current = []
                i += 1
                continue
            current.append(ch)
            i += 1
            continue
        if top in ("'", '"'):
            current.append(ch)
            if ch == "\\" and i + 1 < n:
                current.append(text[i + 1])
                i += 2
                continue
            if ch == top:
                stack.pop()
            i += 1
            continue
        current.append(ch)
        if ch == "\\" and i + 1 < n:
            current.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            stack.pop()
            i += 1
            continue
        if ch == "$" and nxt == "{":
            current.append(nxt)
            stack.append("code")
            expr_braces.append(0)
            i += 2
            continue
        i += 1
    if len(stack) != 1 or stack[0] != "code":
        return None
    parts.append("".join(current))
    return parts


def _ts_assign_sites(
    masked_lines: list[str], base: str
) -> list[tuple[int, int]]:
    """1-based ``(line, rhs_start)`` sites assigning ``base``.

    Matches ``base = ...`` plus ``this.base = ...`` (including compound
    ``base += ...`` rebinding, which counts as a second site: reassigned)
    while excluding ``==``/``===``/``=>``. A different receiver
    (``other.base =``) never counts: only a bare or ``this.``-qualified
    target does.
    """
    bare = base.strip()
    if bare.startswith("this."):
        bare = bare[5:].strip()
    bare = bare.split(".")[0].strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", bare):
        return []
    pattern = re.compile(
        r"(?<![\w$.])(?:this\s*\.\s*)?" + re.escape(bare)
        + r"\s*(?::\s*[^=;]+)?\s*(?:[+\-*/%&|^])?=(?![=>])"
    )
    sites: list[tuple[int, int]] = []
    for num, text in enumerate(masked_lines, start=1):
        for match in pattern.finditer(text):
            sites.append((num, match.end()))
    return sites


def _ts_block_scopes(
    masked_lines: list[str], use_line: int
) -> list[tuple[int, int]]:
    """Innermost-to-outermost ``{...}`` scopes enclosing ``use_line``.

    Brace matching runs on masked text (strings/comments blanked, so only
    real braces pair). Each scope is a 1-based ``(start, end)`` line range;
    the last entry is always the whole file, so a scope with no local
    assignment falls back to today's file-wide single-assignment behavior
    (module consts, class fields). Unbalanced braces never crash: unmatched
    openers/closers are ignored and the file fallback still applies.
    """
    n = len(masked_lines)
    if n == 0:
        return [(1, 1)]
    if not 1 <= use_line <= n:
        return [(1, n)]
    text = "\n".join(masked_lines)
    starts = _line_starts(text)
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for idx, ch in enumerate(text):
        if ch == "{":
            stack.append(idx)
        elif ch == "}":
            if stack:
                pairs.append((stack.pop(), idx))
    scopes: list[tuple[int, int, int, int]] = []
    for opener, closer in pairs:
        open_line = _line_of(starts, opener)
        close_line = _line_of(starts, closer)
        if open_line <= use_line <= close_line:
            scopes.append((open_line, close_line, opener, closer))
    scopes.sort(key=lambda s: (-s[2], s[3]))
    return [(open_line, close_line) for open_line, close_line, _, _ in scopes] + [
        (1, n)
    ]


def _ts_assign_sites_scoped(
    masked_lines: list[str], base: str, use_line: int
) -> list[tuple[int, int]]:
    """Assignment sites for ``base`` visible from ``use_line``.

    The innermost enclosing block of ``use_line`` with a local assignment
    wins alone (shadowing: a method-local ``let``/``const`` keeps a
    same-named outer value out, and a reassignment in a sibling method no
    longer poisons a statically resolvable use here). When the innermost
    block assigns nothing, every file-wide site counts (conservative
    union of the visible chain: module consts plus class fields/flows), so
    an outer ``const`` plus a same-class reassignment abstains as dynamic
    instead of resolving to one of the two values.
    """
    all_sites = _ts_assign_sites(masked_lines, base)
    if not all_sites:
        return []
    scopes = _ts_block_scopes(masked_lines, use_line)
    if not scopes:
        return []
    inner_start, inner_end = scopes[0]
    in_inner = [
        (line_no, rhs_start)
        for line_no, rhs_start in all_sites
        if inner_start <= line_no <= inner_end
    ]
    if in_inner:
        return in_inner
    return all_sites


def _ts_rhs_text(stripped_line: str, rhs_start: int) -> str:
    """Right-hand side on one line: up to ``;`` outside strings, or EOL."""
    stack: list[str] = ["code"]
    i = rhs_start
    n = len(stripped_line)
    while i < n:
        ch = stripped_line[i]
        top = stack[-1]
        if top == "code":
            if ch in ("'", '"', "`"):
                stack.append(ch)
            elif ch == ";":
                return stripped_line[rhs_start:i]
            i += 1
            continue
        if top in ("'", '"'):
            if ch == "\\":
                i += 2
                continue
            if ch == top:
                stack.pop()
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            stack.pop()
            i += 1
            continue
        i += 1
    return stripped_line[rhs_start:]


def _ts_eval_operand(
    op: str,
    stripped_lines: list[str],
    masked_lines: list[str],
    seen: frozenset[str],
    sites_out: list[int],
    use_line: int,
) -> tuple[str, str]:
    """One ``+``-chain operand: ``(status, value)``.

    ``ok`` carries the resolved text (``${}`` already substituted);
    ``unknown`` is an unresolvable dotted/config reference (contributes
    nothing, marks an unknown prefix); ``dynamic`` (calls, ternaries and
    anything non-literal) poisons the whole URL (abstain).
    """
    text = op.strip()
    if not text:
        return ("unknown", "")
    if len(text) >= 2 and text[0] in ("'", '"') and text[-1] == text[0]:
        return ("ok", text[1:-1])
    if len(text) >= 2 and text[0] == "`" and text[-1] == "`":
        return _ts_eval_template(
            text[1:-1], stripped_lines, masked_lines, seen, sites_out, use_line
        )
    if "(" in text or "=>" in text:
        return ("dynamic", "")
    if "?" in text:
        # ``?`` outside a string literal position starts a ternary (or
        # optional chaining): not statically resolvable, abstain. A ``?``
        # inside quotes (e.g. '?status=') arrives here only as part of a
        # larger non-literal operand, which is likewise dynamic.
        stripped_probe = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", "", text)
        if "?" in stripped_probe:
            return ("dynamic", "")
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return ("ok", text)
    if _TS_NAME.match(text):
        return _ts_resolve_name(
            text, stripped_lines, masked_lines, seen, sites_out, use_line
        )
    return ("dynamic", "")


def _ts_eval_template(
    inner: str,
    stripped_lines: list[str],
    masked_lines: list[str],
    seen: frozenset[str],
    sites_out: list[int],
    use_line: int,
) -> tuple[str, str]:
    """Template body: ``${expr}`` becomes the resolved value or a token.

    A resolvable base (single-assigned field/const) substitutes its value;
    an unresolvable dotted reference (``environment.X``) contributes nothing
    (unknown prefix); an unresolvable plain name (a path param) stays a
    ``{name}`` token; anything complex (calls, ternaries) is dynamic.
    """
    buf: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch == "\\" and i + 1 < n:
            buf.append(inner[i:i + 2])
            i += 2
            continue
        if ch == "$" and i + 1 < n and inner[i + 1] == "{":
            depth = 1
            j = i + 2
            jstack: list[str] = ["code"]
            while j < n and depth > 0:
                cj = inner[j]
                top = jstack[-1]
                if top == "code":
                    if cj in ("'", '"', "`"):
                        jstack.append(cj)
                    elif cj == "{":
                        depth += 1
                    elif cj == "}":
                        depth -= 1
                else:
                    if cj == "\\":
                        j += 1
                    elif cj == top:
                        jstack.pop()
                j += 1
            if depth != 0:
                return ("dynamic", "")
            expr_text = inner[i + 2:j - 1].strip()
            if _TS_NAME.match(expr_text):
                status, value = _ts_resolve_name(
                    expr_text, stripped_lines, masked_lines, seen, sites_out,
                    use_line,
                )
                if status == "dynamic":
                    return ("dynamic", "")
                if status == "unknown":
                    if _TS_DOTTED.match(expr_text.replace("this.", "")) or "." in expr_text:
                        buf.append("")
                    else:
                        buf.append("{" + re.sub(r"\s+", "", expr_text) + "}")
                else:
                    buf.append(value)
            else:
                return ("dynamic", "")
            i = j
            continue
        buf.append(ch)
        i += 1
    return ("ok", "".join(buf))


def _ts_resolve_name(
    name: str,
    stripped_lines: list[str],
    masked_lines: list[str],
    seen: frozenset[str],
    sites_out: list[int],
    use_line: int,
) -> tuple[str, str]:
    """Resolve one identifier: single-assignment only, else unknown/dynamic.

    Assignment sites are counted in the innermost scope enclosing the use
    that holds any (widening to the file only when the local scope assigns
    nothing), so a reassignment in a sibling method no longer poisons a
    statically resolvable use here. Exactly one in-scope site resolves
    recursively (literal, template or further composition); zero sites
    means a parameter (plain names become ``{name}`` placeholders
    downstream, dotted config names are unknown prefixes); reassignment
    (2+ sites: field initializer plus setter, ``let`` rebinding, ...)
    abstains the whole URL as dynamic. Cycles resolve as dynamic (never
    infinite).
    """
    bare = name.strip()
    if bare.startswith("this."):
        bare = bare[5:].strip()
    key = bare.split(".")[0].strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", key):
        return ("dynamic", "")
    if key in seen:
        return ("dynamic", "")
    if "." in bare:
        # Dotted config reference (environment.X, config.Y): only a
        # single-assigned alias can rescue it; otherwise unknown prefix.
        sites = _ts_assign_sites_scoped(masked_lines, bare, use_line)
        if len(sites) != 1:
            return ("unknown", "")
    else:
        sites = _ts_assign_sites_scoped(masked_lines, key, use_line)
        if len(sites) == 0:
            return ("unknown", "")
        if len(sites) > 1:
            return ("dynamic", "")
    if len(sites) != 1:
        return ("unknown", "")
    line_no, rhs_start = sites[0]
    if not 1 <= line_no <= len(stripped_lines):
        return ("dynamic", "")
    rhs = _ts_rhs_text(stripped_lines[line_no - 1], rhs_start).strip()
    if not rhs:
        return ("dynamic", "")
    sites_out.append(line_no)
    status, value = _ts_eval_url_expr(
        rhs, stripped_lines, masked_lines, seen | {key}, sites_out, line_no
    )
    if status == "dynamic":
        # A resolvable base whose own value is computed still poisons the
        # use site (never a half-resolved URL)...
        return ("dynamic", "")
    if status == "unknown":
        # ...unless the base itself is just an alias of an unknown config
        # value with no literal content: then it stays an unknown prefix.
        probe = re.sub(r"\{[^}]*\}", "", value)
        if re.sub(r"[^A-Za-z0-9]", "", probe) == "":
            return ("unknown", "")
    return (status, value)


def _ts_eval_url_expr(
    expr: str,
    stripped_lines: list[str],
    masked_lines: list[str],
    seen: frozenset[str],
    sites_out: list[int],
    use_line: int,
) -> tuple[str, str]:
    """Full URL expression: ``+``-chain concatenation of operands.

    ``dynamic`` poisons the whole expression (abstain, no record);
    ``unknown`` operands contribute nothing (unknown host/config prefix);
    zero known literal content means the path cannot be determined
    (abstain, missing base).
    """
    parts = _ts_split_top(expr, "+")
    if parts is None:
        return ("dynamic", "")
    if len(parts) == 1:
        status, value = _ts_eval_operand(
            parts[0], stripped_lines, masked_lines, seen, sites_out, use_line
        )
        if status != "ok":
            return (status, value)
        if re.fullmatch(r"\{[A-Za-z_$][\w$.]*\}", value.strip()):
            # A lone `` `${base}` `` template: resolve the base itself.
            inner = value.strip()[1:-1]
            return _ts_resolve_name(
                inner, stripped_lines, masked_lines, seen, sites_out, use_line
            )
        return ("ok", value)
    combined: list[str] = []
    unknown_prefix = False
    for part in parts:
        status, value = _ts_eval_operand(
            part, stripped_lines, masked_lines, seen, sites_out, use_line
        )
        if status == "dynamic":
            return ("dynamic", "")
        if status == "unknown":
            unknown_prefix = True
            bare = part.strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", bare):
                # Plain parameter inside a composition: a path segment
                # (``'/' + orderId`` -> ``/{orderId}``).
                combined.append("{" + bare + "}")
            continue
        combined.append(value)
    _ = unknown_prefix
    return ("ok", "".join(combined))


def _ts_path_of(combined: str) -> str | None:
    """Linkable path from a resolved URL expression, else None.

    Absolute URLs contribute their path; otherwise the text from the first
    ``/`` (or ``/`` + relative segments); query/fragment stripped. Bare
    placeholders with no literal content abstain (missing base).
    """
    text = (combined or "").strip()
    if not text:
        return None
    http_at = text.find("http://")
    if http_at == -1:
        http_at = text.find("https://")
    try:
        if http_at != -1:
            candidate = urlparse(text[http_at:])
            path = candidate.path or ""
        elif text.startswith("/"):
            path = text
        else:
            # Relative composition (``'owners' + '/' + id``): the whole
            # text is path content, never a host prefix to strip.
            path = "/" + text
        path = path.split("?", 1)[0].split("#", 1)[0]
    except (ValueError, AttributeError):
        return None
    if not path.startswith("/"):
        return None
    probe = re.sub(r"\{[^}]*\}", "", path)
    if re.sub(r"[^A-Za-z0-9]", "", probe) == "":
        return None
    return path


def extract_ts_client_urls(path: Path) -> list[dict[str, Any]]:
    """TS/JS HTTP client URLs (Angular HttpClient, fetch, axios).

    A record needs a supported receiver plus a statically known verb and a
    derivable path: ``this.http``/``http``/``httpClient`` ``.get/post/put/
    delete/patch(...)`` (verb from the method name); ``fetch(url[, {method:
    'POST'}])`` (absent options, or options without ``method:``, defaults
    to GET with a cited default reason; dynamic/unknown ``method:`` values
    abstain); ``axios.get/post/put/delete/patch(url, ...)`` plus the trivial
    ``axios({method: 'post', url: ...})`` config-object form (missing or
    dynamic members abstain).

    The URL expression may be a literal, a template (``${var}`` becomes a
    ``{var}`` token) or a ``+`` composition whose base is single-assigned
    in the innermost scope enclosing the call that holds any assignment
    (widening to the file only when the local scope assigns nothing:
    field/const/``this.`` alias, one site only, cited as ``base_span``);
    an ``environment.X``/config host prefix is an unknown
    prefix (never fabricated: only the known path suffix is kept).
    Reassigned bases, computed URLs (call results, ternaries), sole unknown
    bases, unknown verbs and non-HTTP ``.get()`` (``Map.get``) yield no
    record (abstain), as do ``foohttp.get(...)`` / ``other.http.get(...)``
    (receiver guard) and nested generics (``get<Foo<Bar>>`` abstains
    by design, no crash). Comments and string literals that look like HTTP
    calls never emit.

    Records reuse the ``client_url`` shape (``url`` is the resolved path on
    the documented ``http://ts.local`` dummy host for linker path matching
    -- the host is never claimed -- plus ``ts: True``, ``receiver``,
    ``call_line``/``call_text``, ``url_expr``, ``resolved_path`` and span
    from the base definition through the call site) so the linkers treat
    them as declared-only candidates with carried invocation evidence
    (``destination_verified`` False, ``equivalent`` never True).
    """
    file = Path(path)
    if file.suffix.lower() not in (".ts", ".js"):
        return []
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    stripped = _blank_ts_ignorable(text, blank_strings=False)
    masked = _blank_ts_ignorable(text, blank_strings=True)
    stripped_lines = stripped.splitlines()
    masked_lines = masked.splitlines()
    line_starts = _line_starts(text)
    candidates: list[tuple[int, str, str | None]] = []
    for match in _TS_HTTPCLIENT_CALL.finditer(masked):
        candidates.append(
            (match.start(), "httpclient", str(match.group("verb")).lower())
        )
    for match in _TS_FETCH_CALL.finditer(masked):
        candidates.append((match.start(), "fetch", None))
    for match in _TS_AXIOS_CALL.finditer(masked):
        candidates.append(
            (match.start(), "axios", str(match.group("verb")).lower())
        )
    for match in _TS_AXIOS_CONFIG.finditer(masked):
        candidates.append((match.start(), "axios-config", None))
    candidates.sort()
    records: list[dict[str, Any]] = []
    seen_calls: set[tuple[int, str]] = set()
    for start, kind, verb in candidates:
        call_line = _line_of(line_starts, start)
        open_idx = masked.find("(", start)
        if open_idx == -1:
            continue
        args = _ts_balanced_args(stripped, open_idx)
        if args is None:
            continue
        receiver = "axios" if kind.startswith("axios") else (
            "fetch" if kind == "fetch" else ""
        )
        url_expr: str | None = None
        method: str | None = verb
        method_reason = "explicit"
        default_method = False
        url_kind = "composed"
        if kind == "httpclient":
            recv_match = _TS_HTTPCLIENT_CALL.match(masked, start)
            if recv_match is None:
                continue
            receiver = re.sub(
                r"\s+", "", recv_match.group("recv")
            )
            split = _ts_split_top(args, ",")
            if not split or not split[0].strip():
                continue
            url_expr = split[0].strip()
        elif kind == "fetch":
            receiver = "fetch"
            split = _ts_split_top(args, ",")
            if not split or not split[0].strip():
                continue
            url_expr = split[0].strip()
            if len(split) > 1:
                options = split[1]
                method_match = _TS_METHOD_KV.search(options)
                if method_match is not None:
                    candidate_verb = method_match.group(1).strip().lower()
                    if candidate_verb not in _TS_HTTP_VERBS:
                        continue
                    method = candidate_verb
                # ``\bmethod\s*:`` (not a substring): a mere ``method``
                # substring such as an ``"X-method"`` header is not an
                # explicit method, so it falls through to default GET below;
                # only a real ``method:`` key with a dynamic/unknown value
                # abstains.
                elif re.search(r"\bmethod\s*:", options):
                    continue
                else:
                    method = "get"
                    default_method = True
                    method_reason = (
                        "default: fetch without an explicit method "
                        "defaults to GET"
                    )
            else:
                method = "get"
                default_method = True
                method_reason = (
                    "default: fetch without options defaults to GET"
                )
        elif kind == "axios":
            split = _ts_split_top(args, ",")
            if not split or not split[0].strip():
                continue
            url_expr = split[0].strip()
        else:
            inner = args.strip()
            if (
                len(inner) >= 2
                and inner.startswith("{")
                and inner.endswith("}")
            ):
                inner = inner[1:-1]
            else:
                continue
            config_parts = _ts_split_top(inner, ",")
            if config_parts is None:
                continue
            method_part: str | None = None
            url_part: str | None = None
            for part in config_parts:
                if _TS_METHOD_KV.search(part) is not None and method_part is None:
                    method_part = part
                if _TS_URL_KV.search(part) is not None and url_part is None:
                    url_part = part
            if method_part is None or url_part is None:
                continue
            method_match = _TS_METHOD_KV.search(method_part)
            if method_match is None:
                continue
            candidate_verb = method_match.group(1).strip().lower()
            if candidate_verb not in _TS_HTTP_VERBS:
                continue
            method = candidate_verb
            url_match = re.match(
                r"\s*url\s*:\s*(.+)\s*$", url_part, re.DOTALL
            )
            if url_match is None:
                continue
            url_expr = url_match.group(1).strip()
            if not url_expr:
                continue
        if method is None or method not in _TS_HTTP_VERBS:
            continue
        if url_expr is None:
            continue
        sites: list[int] = []
        status, combined = _ts_eval_url_expr(
            url_expr, stripped_lines, masked_lines, frozenset(), sites,
            call_line,
        )
        if status != "ok":
            continue
        resolved = _ts_path_of(combined)
        if resolved is None:
            continue
        key = (call_line, resolved + "\x00" + method)
        if key in seen_calls:
            continue
        seen_calls.add(key)
        url_expr_text = url_expr.strip()
        if (
            len(url_expr_text) >= 2
            and url_expr_text[0] in ("'", '"')
            and url_expr_text[-1] == url_expr_text[0]
        ):
            url_kind = "literal"
        elif (
            len(url_expr_text) >= 2
            and url_expr_text[0] == "`"
            and url_expr_text[-1] == "`"
        ):
            url_kind = "template"
        if kind == "axios-config":
            url_kind = "config"
        try:
            call_text = stripped_lines[call_line - 1].strip()
        except IndexError:
            continue
        base_span: dict[str, int] | None = None
        if sites:
            first = min(sites)
            base_span = {"start_line": first, "end_line": first}
        record = dict(_base(file, call_line))
        record.update({
            "kind": "client_url",
            "url": _TS_DUMMY_HOST + resolved,
            "ts": True,
            "method": method,
            "client_method": method,
            "method_reason": method_reason,
            "default_method": default_method,
            "receiver": receiver,
            "receiver_line": call_line,
            "call_line": call_line,
            "call_text": call_text,
            "url_expr": url_expr_text,
            "resolved_path": resolved,
            "url_kind": url_kind,
            "base_span": base_span,
            "span": {
                "start_line": min(sites) if sites else call_line,
                "end_line": call_line,
            },
        })
        records.append(record)
    records.sort(key=lambda r: (int(r["line"]), str(r["url"])))
    return records


# ---------------------------------------------------------------- Java guards and transitions

# Single-line declarations only; wrapped (multi-line) signatures abstain
# (no half-parse): the `[^)]*` param group requires the full declaration
# on one line, so a signature split across lines yields no match by design.
_METHOD_WITH_BODY = re.compile(
    r"^\s*(?:public|private|protected|static\s+)*(?:[\w<>\[\]\s,\?\.]*)\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)\s*(?:throws\s+[\w\.\[\]\s,]+)?\s*\{?\s*$"
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


def _is_window_compare(left: str, op: str, right: str) -> bool:
    """Window-shaped comparison: ``<=`` with a window/days token.

    Only a ``<=`` comparison naming a window/days value (e.g. ``days <=
    windowDays``) counts as a window predicate; any other comparison
    (``==``, ``<``, ``>``, ...) or any ``<=`` without such a token
    abstains (not a window predicate).
    """
    if op != "<=":
        return False
    blob = f"{left} {right}".lower()
    return ("window" in blob or "day" in blob)


def _param_count(params_text: str) -> int:
    """Top-level comma count for a method-declaration parameter list."""
    text = (params_text or "").strip()
    if not text:
        return 0
    depth_angle = depth_paren = depth_brack = 0
    in_str: str | None = None
    count = 1
    for ch in text:
        if in_str is not None:
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
        elif ch == "<":
            depth_angle += 1
        elif ch == ">":
            if depth_angle > 0:
                depth_angle -= 1
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            if depth_paren > 0:
                depth_paren -= 1
        elif ch == "[":
            depth_brack += 1
        elif ch == "]":
            if depth_brack > 0:
                depth_brack -= 1
        elif ch == "," and depth_angle == 0 and depth_paren == 0 and depth_brack == 0:
            count += 1
    return count


def _call_arg_count(condition_text: str, name: str) -> int | None:
    """Top-level argument count for the first ``name(...)`` call."""
    match = re.search(r"\b" + re.escape(name) + r"\s*\(", condition_text)
    if not match:
        return None
    pos = match.end() - 1
    depth = 0
    commas = 0
    seen_content = False
    in_str: str | None = None
    for idx in range(pos, len(condition_text)):
        ch = condition_text[idx]
        if in_str is not None:
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
            if depth == 1:
                seen_content = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return (commas + 1) if seen_content else 0
            if depth < 0:
                return None
        elif ch == "," and depth == 1:
            commas += 1
        elif ch.strip() and depth == 1:
            seen_content = True
    return None
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
    bodies and abstract declarations are skipped. Wrapped (multi-line)
    declarations abstain by design (single-line only, no half-parse):
    ``_METHOD_WITH_BODY`` requires the full signature on one line.
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
            "decl": index + 1,
            "param_count": _param_count(match.group(2) if match.lastindex is not None and match.lastindex >= 2 else ""),
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

    Evidence extras (all additive): an enforced rule cites the method's true
    comparison (``return days <= windowDays``) in ``enforcement_lines``/span
    even when the failure-branch scan stops earlier; a caller-side guard
    naming a helper resolves the helper def (``helper_def_line``) and cites
    the callee's own comparison separately (``callee_condition`` /
    ``callee_predicate_line``), abstaining (None) when unresolvable; every
    transition carries ``prerequisites`` (dominating prior rejections: same
    method, guard_if < write, guard parent an ancestor-prefix of the write
    parent, enforced rejection) and a ``coverage`` label
    (``contained[-with-prerequisites]`` / ``prior-partial`` / ``none``).
    """
    enforced: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    bodies = _method_bodies(path)
    # Helper-method predicate index: (name, param-count) -> (decl line,
    # window comparison, predicate line). Lets a caller-side guard citing a
    # helper (``if (!isEligible(...))``) prove the link: helper def line
    # plus the callee's own comparison, cited separately from the call
    # line. Keyed by (name, arity) so overloads do not collide
    # (first-wins by bare name would misattribute); only a single
    # window-shaped (``<=`` + window/days token) ``return`` comparison
    # counts -- zero or multiple candidates abstain (None): no name-only
    # predicate attribution, and an unrelated early ``return a==b`` can
    # never be cited as a window predicate.
    helper_predicates: dict[tuple[str, int], dict[str, Any]] = {}
    for _body in bodies:
        _hlines = _body["lines"]
        _hbase = _body["base"]
        _hcands: list[tuple[str, int]] = []
        for _hoff, _hline in enumerate(_hlines):
            _hmatch = _RETURN_STMT.match(_hline)
            if not _hmatch:
                continue
            _hcompare = _COMPARE.search(_hmatch.group(1))
            if _hcompare and _is_window_compare(
                _hcompare.group(1), _hcompare.group(2), _hcompare.group(3)
            ):
                _hcands.append(
                    (
                        f"{_hcompare.group(1)} "
                        f"{_hcompare.group(2)} {_hcompare.group(3)}",
                        _hbase + _hoff,
                    )
                )
        if len(_hcands) == 1:
            _hcond, _hcond_line = _hcands[0]
        else:
            _hcond, _hcond_line = None, None
        _hkey = (_body["name"], int(_body.get("param_count", 0)))
        if _hkey not in helper_predicates:
            helper_predicates[_hkey] = {
                "decl": _body.get("decl"),
                "condition": _hcond,
                "condition_line": _hcond_line,
            }
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
        # Per-body window candidates: only single window-shaped
        # (``<=`` + window/days) ``return`` comparisons count; zero or
        # multiple abstain. Per-guard scoping below cites the candidate
        # only when its line is after the guard_if (never an unrelated
        # early ``return a==b``, which also fails the window shape).
        _window_cands: list[tuple[str, int]] = []
        for _w_off, _w_line in enumerate(lines):
            _w_match = _RETURN_STMT.match(_w_line)
            if not _w_match:
                continue
            _w_compare = _COMPARE.search(_w_match.group(1))
            if _w_compare and _is_window_compare(
                _w_compare.group(1), _w_compare.group(2), _w_compare.group(3)
            ):
                _window_cands.append(
                    (
                        f"{_w_compare.group(1)} "
                        f"{_w_compare.group(2)} {_w_compare.group(3)}",
                        base + _w_off,
                    )
                )
        if len(_window_cands) == 1:
            _body_window_cond, _body_window_line = _window_cands[0]
        else:
            _body_window_cond, _body_window_line = None, None
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
            # Per-guard window predicate: cite the body's single
            # window-shaped comparison only when it lies after this
            # guard_if. An unrelated early ``return a==b`` (before the
            # guard, wrong shape, or ambiguous multiples) abstains.
            per_guard_condition: str | None = None
            per_guard_condition_line: int | None = None
            if (
                _body_window_cond is not None
                and _body_window_line is not None
                and _body_window_line > guard_line
            ):
                per_guard_condition = _body_window_cond
                per_guard_condition_line = _body_window_line
            enforcement = [guard_line]
            if failure_line is not None and failure_line not in enforcement:
                enforcement.append(failure_line)
            if per_guard_condition_line is not None and per_guard_condition_line not in enforcement:
                # The method's true comparison (e.g. ``return days <=
                # windowDays``) is enforcement evidence even when the scan
                # for the failure branch stops earlier (e.g. at an early
                # ``return false`` null-check): the span must cover guard
                # through predicate instead of omitting it.
                enforcement.append(per_guard_condition_line)
            call = _GUARD_CALL.search(condition_text)
            if call:
                guard_name = call.group(1)
            else:
                guard_name = (per_guard_condition or condition_text.strip())
                if not guard_name.strip():
                    continue
            helper_def_line: int | None = None
            callee_condition: str | None = None
            callee_predicate_line: int | None = None
            if call:
                _cname = call.group(1)
                _argc = _call_arg_count(condition_text, _cname)
                if _argc is not None:
                    helper_info = helper_predicates.get((_cname, _argc))
                    if helper_info is not None and helper_info.get("condition_line") is not None:
                        helper_def_line = helper_info.get("decl")
                        callee_condition = helper_info.get("condition")
                        callee_predicate_line = helper_info.get("condition_line")
            enforcement = sorted(set(enforcement))
            record = dict(_base(path, guard_line))
            record.update({
                "kind": "enforced_rule",
                "rule": "window_guard",
                "condition": per_guard_condition or (condition_text or "").strip(),
                "failure_outcome": failure,
                "guard": guard_name,
                "guard_if_line": guard_line,
                "enforcement_lines": enforcement,
                "span": {"start_line": enforcement[0], "end_line": enforcement[-1]},
                "helper_def_line": helper_def_line,
                "callee_condition": callee_condition,
                "callee_predicate_line": callee_predicate_line,
            })
            enforced.append(record)
            body_guards.append({
                "guard": guard_name,
                "guard_if_line": guard_line,
                "enforcement_lines": enforcement,
                "guard_depth": guard_depth,
                "guard_parent": guard_parent,
                "failure": failure,
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
                prerequisites = [
                    {
                        "guard": g["guard"],
                        "guard_if_line": g["guard_if_line"],
                        "enforcement_lines": list(g["enforcement_lines"]),
                    }
                    for g in body_guards
                    if g["guard_if_line"] < status_line
                    and g.get("failure") == "rejection"
                    and g["guard_if_line"] != contained["guard_if_line"]
                    and len(g.get("guard_parent") or ()) <= len(write_parent)
                    and tuple(write_parent[:len(g.get("guard_parent") or ())])
                    == tuple(g.get("guard_parent") or ())
                ]
                transitions.append({
                    **_base(path, status_line),
                    "kind": "transition",
                    "target_status": target,
                    "guard": contained["condition"],
                    "guard_if_line": contained["guard_if_line"],
                    "enforcement_lines": [contained["guard_if_line"]],
                    "span": {"start_line": status_line, "end_line": status_line},
                    "prerequisites": prerequisites,
                    "coverage": (
                        "contained-with-prerequisites"
                        if prerequisites
                        else "contained"
                    ),
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
            other_prerequisites = [
                {
                    "guard": g["guard"],
                    "guard_if_line": g["guard_if_line"],
                    "enforcement_lines": list(g["enforcement_lines"]),
                }
                for g in body_guards
                if g["guard_if_line"] < status_line
                and g.get("failure") == "rejection"
                and (best is None or g["guard_if_line"] != best["guard_if_line"])
                and len(g.get("guard_parent") or ()) <= len(write_parent)
                and tuple(write_parent[:len(g.get("guard_parent") or ())])
                == tuple(g.get("guard_parent") or ())
            ]
            transitions.append({
                **_base(path, status_line),
                "kind": "transition",
                "target_status": target,
                "guard": best["guard"] if best is not None else None,
                "guard_if_line": best["guard_if_line"] if best is not None else None,
                "enforcement_lines": list(best["enforcement_lines"]) if best is not None else [],
                "span": {"start_line": status_line, "end_line": status_line},
                "prerequisites": other_prerequisites if best is not None else [],
                "coverage": (
                    "prior-partial"
                    if best is not None
                    else "none"
                ),
            })
    return enforced, transitions


def link_client_url(url_record: dict[str, Any], openapi_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contract links between a client URL and OpenAPI paths.

    Compatible only when the client URL path matches an OpenAPI path *and*
    the client method matches the OpenAPI operation method *and* invocation
    evidence exists (a supported ``RestTemplate`` call line in the same
    method; the client method rides on the URL record, or is inferred
    trivially from ``postForObject``/``getForObject``/``exchange`` call
    sites via a supported ``RestTemplate`` receiver; unknown
    method abstains via ``candidate-needs-method``). Explicit-method-only
    records without a call line yield a candidate (compatible False) with
    a ``no-invocation`` reason -- compatible True honestly requires
    invocation evidence. An unused URL whose
    path matches nothing yields no links. Duplicate destinations
    (same path+method in >1 operation) never yield compatible True
    (ambiguous destination: abstain/candidate). Equivalent is never true (client
    URL carries scheme+host, OpenAPI path is path-only). A compatible link is
    an operation match only (path+method): delivery/schema/equivalence are
    never verified, so every link carries ``destination_verified`` False with
    ``destination_evidence`` holding the client hostname, and the reason
    states the operation-only scope.
    """
    links: list[dict[str, Any]] = []
    client_path = urlparse(url_record["url"]).path
    try:
        client_host = urlparse(url_record["url"]).hostname
    except ValueError:
        client_host = None
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
    # Intended (not a fix): OpenAPI links are exact-match only, while
    # annotation links allow ``{var}`` template segments -- no new OpenAPI
    # template logic; a template client path simply yields no OpenAPI link.
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
        "destination_verified": False,
        "destination_evidence": client_host,
        "client_call_line": call_line,
        "client_call": call_text,
        "span": {
            "start_line": url_record["line"],
            "end_line": url_record["line"],
        },
        "method_reason": url_record.get("method_reason"),
        "default_method": url_record.get("default_method", False),
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
        link["compatible"] = bool(path_match and method_match and call_line is not None)
        link["equivalent"] = False
        never_equiv = "equivalent never true (client URL carries scheme+host, OpenAPI path is path-only)"
        op_only = (
            "operation-only match (path+method), not delivery/schema/equivalence; "
            f"destination host {client_host!r} unverified (destination_verified false)"
        )
        if dup_count > 1 and path_match and method_match:
            link["compatible"] = False
            link["reason"] = (
                f"ambiguous destination: {dup_count} operations share path {client_path} "
                f"and method {client_method}; abstain, no verified compatible link "
                f"(candidate, duplicate destination); {never_equiv}; {op_only}"
            )
        elif link["compatible"]:
            op_path_line = op.get("span", {}).get("start_line", op["line"]) if isinstance(op.get("span"), dict) else op["line"]
            binding_line = url_record["line"]
            link["reason"] = (
                f"compatible contract: client path {client_path} == openapi path {op['openapi_path']} "
                f"and method {client_method} == {op_method_norm}; "
                f"binding L{binding_line} + call L{call_line} + openapi path L{op_path_line} + method L{op['line']}; {never_equiv}; {op_only}"
            )
        elif path_match:
            if method_match:
                link["reason"] = (
                    f"candidate-no-invocation: path {client_path} matches and method {client_method} == {op_method_norm} "
                    f"but no invocation evidence (no-invocation, compatible requires call line); "
                    f"(candidate, explicit-only without call); {never_equiv}; {op_only}"
                )
            elif client_method is None or op_method_norm is None:
                link["reason"] = (
                    f"candidate-needs-method: path {client_path} matches but method is unknown "
                    f"(client {client_method!r} vs openapi {op_method_norm!r}); {never_equiv}; {op_only}"
                )
            else:
                link["reason"] = (
                    f"incompatible contract: method mismatch client {client_method} != openapi "
                    f"{op_method_norm} for path {client_path}; {never_equiv}; {op_only}"
                )
        else:
            link["reason"] = (
                f"incompatible contract: client path {client_path} != openapi path {op['openapi_path']}; "
                f"{never_equiv}; {op_only}"
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
    mapping_routes: list[dict[str, Any]] = []
    policies: list[dict[str, Any]] = []
    persistence: list[dict[str, Any]] = []
    openapi: list[dict[str, Any]] = []
    client_urls: list[dict[str, Any]] = []
    enforced_rules: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    record_constraints: list[dict[str, Any]] = []

    for repo in repos:
        for root, dirs, names in os.walk(repo):
            dirs.sort()
            for name in sorted(names):
                file = Path(root) / name
                if name.endswith(".java"):
                    annotations.extend(extract_annotations(file))
                    mapping_routes.extend(extract_mapping_routes(file))
                    client_urls.extend(extract_client_urls(file))
                    client_urls.extend(extract_fluent_client_urls(file))
                    rules, trans = extract_guards(file)
                    enforced_rules.extend(rules)
                    transitions.extend(trans)
                    record_constraints.extend(extract_record_constraints(file))
                elif _is_openapi(name):
                    openapi.extend(extract_openapi(file))
                elif name in ("application.yml", "application.yaml") or name.endswith(".yml") or name.endswith(".yaml"):
                    policies.extend(extract_yaml_policy(file))
                elif name.endswith(".sql"):
                    persistence.extend(extract_sql_persistence(file))

    contract_links: list[dict[str, Any]] = []
    deduped: dict[tuple[str, int, str], dict[str, Any]] = {}
    for record in client_urls:
        key = (str(record.get("file")), int(record.get("line", -1)),
               str(record.get("url")))
        prior = deduped.get(key)
        if prior is None or (
            record.get("fluent") is True and prior.get("fluent") is not True
        ):
            deduped[key] = record
    client_urls = list(deduped.values())
    for url_record in client_urls:
        contract_links.extend(link_client_url(url_record, openapi))
    annotation_operations = emit_annotation_operations(mapping_routes)
    for url_record in client_urls:
        contract_links.extend(link_client_to_annotation(url_record, annotation_operations))

    return {
        "origin": ORIGIN,
        "extractor_version": EXTRACTOR_VERSION,
        "repos": [str(p) for p in repos],
        "records": {
            "annotations": annotations,
            "mapping_routes": mapping_routes,
            "annotation_operations": annotation_operations,
            "policies": policies,
            "persistence": persistence,
            "openapi": openapi,
            "client_urls": client_urls,
            "contract_links": contract_links,
            "enforced_rules": enforced_rules,
            "transitions": transitions,
            "record_constraints": record_constraints,
        },
        "counts": {
            "annotations": len(annotations),
            "mapping_routes": len(mapping_routes),
            "annotation_operations": len(annotation_operations),
            "policies": len(policies),
            "persistence": len(persistence),
            "openapi": len(openapi),
            "client_urls": len(client_urls),
            "contract_links": len(contract_links),
            "enforced_rules": len(enforced_rules),
            "transitions": len(transitions),
            "record_constraints": len(record_constraints),
        },
        "limitation": YAML_LIMITATION,
    }
