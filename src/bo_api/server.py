"""Minimal read-only HTTP API/UI for business-ontologies (stdlib only).

M1 is single-user localhost-only (requirements Sec 13): the server binds
127.0.0.1 only. There is no authN/authZ in M1; retrieval is deny-by-default
for a future multi-user model (no full auth implemented here).
"""

from __future__ import annotations

import html as _html
import http.server
import json
import os
import sqlite3
import urllib.parse

try:  # optional dependency already in repo
    from bo_store.provenance import init_db as _init_db, redact_value as _redact
except Exception:  # pragma: no cover
    _init_db = None

    def _redact(value):  # fallback if provenance unavailable
        return value


def _open_db(db_path):
    if hasattr(db_path, "execute"):
        conn = db_path
        try:
            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        return conn, False
    if _init_db is not None:
        try:
            conn = _init_db(db_path)
            try:
                conn.row_factory = sqlite3.Row
            except Exception:
                pass
            return conn, True
        except Exception:
            pass
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn, True


def _close(conn, owned):
    if owned:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_all(db_path, repo=None):
    conn, owned = _open_db(db_path)
    try:
        try:
            conn.execute("SELECT 1 FROM provenance LIMIT 1")
        except Exception:
            if _init_db is not None:
                try:
                    _init_db(conn)
                except Exception:
                    pass
        if repo is None:
            cur = conn.execute("SELECT * FROM provenance ORDER BY id")
        else:
            # Parameterized WHERE: per-repo scoping, never string interpolation.
            cur = conn.execute(
                "SELECT * FROM provenance WHERE repo = ? ORDER BY id", (repo,)
            )
        cols = [d[0] for d in cur.description] if cur.description else []
        out = []
        for row in cur.fetchall():
            item = {}
            for i, c in enumerate(cols):
                item[c] = row[i]
            out.append(_redact(item))
        return out
    finally:
        _close(conn, owned)


def _fetch_one(db_path, row_id):
    conn, owned = _open_db(db_path)
    try:
        try:
            conn.execute("SELECT 1 FROM provenance LIMIT 1")
        except Exception:
            if _init_db is not None:
                try:
                    _init_db(conn)
                except Exception:
                    pass
        try:
            cur = conn.execute("SELECT * FROM provenance WHERE id = ?", (row_id,))
        except Exception:
            return None
        cols = [d[0] for d in cur.description] if cur.description else []
        row = cur.fetchone()
        if row is None:
            return None
        item = {}
        for i, c in enumerate(cols):
            item[c] = row[i]
        return _redact(item)
    finally:
        _close(conn, owned)


def _is_traversal(value):
    if not isinstance(value, str):
        return False
    if os.path.isabs(value):
        return True
    parts = value.replace("\\", "/").split("/")
    return ".." in parts


def _repo_param(parsed):
    """Return the ?repo= value from a parsed URL, else None (no scoping)."""
    values = urllib.parse.parse_qs(parsed.query).get("repo")
    if not values:
        return None
    repo = values[0]
    return repo if repo else None


def _status_param(parsed):
    """Return the ?status= value from a parsed URL, else None (no scoping)."""
    values = urllib.parse.parse_qs(parsed.query).get("status")
    if not values:
        return None
    status = values[0]
    return status if status else None


def _subject_param(parsed):
    """Return the ?subject= value from a parsed URL, else None (no scoping)."""
    values = urllib.parse.parse_qs(parsed.query).get("subject")
    if not values:
        return None
    subject = values[0]
    return subject if subject else None


def _demo_opted_in(parsed):
    """Explicit demo opt-in for /m3/* fixture views (?demo=1 or env)."""
    try:
        values = urllib.parse.parse_qs(parsed.query).get("demo")
        if values and values[0].strip().lower() in ("1", "true", "yes"):
            return True
    except Exception:
        pass
    for env_key in ("BO_M3_DEMO", "BO_DEMO", "BO_API_DEMO"):
        try:
            if os.environ.get(env_key, "").strip().lower() in ("1", "true", "yes"):
                return True
        except Exception:
            continue
    return False


def _labeled_unfiltered(rows, filt, reason):
    """Legacy/unfiltered envelope: never a silent all-rows list."""
    return {
        "data": rows,
        "filter": filt,
        "legacy": True,
        "unfiltered": True,
        "reason": reason,
    }


def _apply_column_filter(rows, column, value):
    """In-Python equality filter on an existing provenance column.

    Returns (True, filtered) when *column* exists; (False, rows) when the
    column is unknown so the caller can label instead of silently dumping
    all rows.
    """
    if not rows:
        return True, []
    if column not in rows[0].keys():
        return False, rows
    return True, [r for r in rows if str(r.get(column)) == str(value)]


def _typed_filter_params(parsed):
    """Collect explicit typed-filter query params (excluding ?repo=)."""
    qs = urllib.parse.parse_qs(parsed.query)
    out = {}
    for key in ("origin", "status", "kind", "subject"):
        try:
            vals = qs.get(key)
        except Exception:
            vals = None
        if vals and vals[0]:
            out[key] = vals[0]
    return out


def _LEGACY_BULK_HEADERS():
    """Header labels for /rules + /relations raw-list legacy bulk exports."""
    return {
        "X-Legacy-Bulk-Export": "true",
        "X-Filter-Status": "unfiltered-legacy",
    }


def _send_typed(handler, rows, filt, default_unfiltered_label, extra_headers=None):
    """Send filtered rows, or a labeled legacy/unfiltered envelope.

    *filt* maps column -> wanted value. Unknown columns (e.g. ``kind`` /
    ``subject``, which are not provenance columns) yield a labeled
    envelope, never a silent all-rows list. Empty *filt* returns the
    repo-scoped rows unchanged unless *default_unfiltered_label* forces
    an explicit label.

    *extra_headers* (e.g. legacy bulk-export labels) is sent only with a
    raw-list body; labeled envelopes already carry their label in-band.
    """
    if not filt:
        if default_unfiltered_label:
            handler._send_json(
                _labeled_unfiltered(rows, None, default_unfiltered_label)
            )
        else:
            handler._send_json(rows, extra_headers=extra_headers)
        return
    filtered = list(rows)
    for column, value in filt.items():
        ok, filtered = _apply_column_filter(filtered, column, value)
        if not ok:
            handler._send_json(
                _labeled_unfiltered(
                    rows, filt, "unknown-filter-column:%s" % column
                )
            )
            return
    handler._send_json(filtered)


def create_app(db_path):
    """Return a read-only request handler class bound to *db_path*."""
    # Ensure schema exists up front.
    try:
        conn, owned = _open_db(db_path)
        _close(conn, owned)
    except Exception:
        pass

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, obj, status=200, extra_headers=None):
            data = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for _hk, _hv in (extra_headers or {}).items():
                self.send_header(_hk, _hv)
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def _send_html(self, body, status=200):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def _abstain(self, status=404):
            self._send_json({"status": "abstain", "reason": "no-evidence"}, status=status)

        def _traversal_guard(self, parsed):
            for seg in parsed.path.split("/"):
                if seg == "..":
                    return True
            for key, values in urllib.parse.parse_qs(parsed.query).items():
                if _is_traversal(key):
                    return True
                for v in values:
                    if _is_traversal(v):
                        return True
            return False

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if self._traversal_guard(parsed):
                self._send_json({"status": "abstain", "reason": "path-traversal"}, status=400)
                return

            path = parsed.path.rstrip("/") or "/"

            if path == "/evidence":
                # Evidence: all provenance artifacts (optionally scoped by ?repo=).
                self._send_json(_fetch_all(self.db_path, repo=_repo_param(parsed)))
                return
            if path == "/rules":
                # Rules: provenance artifacts scoped by ?repo=, with optional
                # in-Python equality filters (?origin=/?status=). Unfiltered
                # raw list is advertised bulk-export behavior (parity with
                # /evidence); unknown filter columns yield a labeled
                # legacy/unfiltered envelope, never a silent all-rows list.
                # Legacy bulk-export: unfiltered raw list bodies are labeled
                # via response headers only (body stays a bare list).
                _send_typed(
                    self,
                    _fetch_all(self.db_path, repo=_repo_param(parsed)),
                    _typed_filter_params(parsed),
                    None,
                    _LEGACY_BULK_HEADERS(),
                )
                return
            if path == "/relations":
                # Relations: cross-repo invocation edges persisted as provenance
                # artifacts. Scoped by ?repo=, with optional in-Python equality
                # filters. Unfiltered raw list is advertised bulk-export
                # behavior (parity with /evidence); unknown columns yield a
                # labeled envelope, never a silent all-rows list.
                # Legacy bulk-export: unfiltered raw list bodies are labeled
                # via response headers only (body stays a bare list).
                _send_typed(
                    self,
                    _fetch_all(self.db_path, repo=_repo_param(parsed)),
                    _typed_filter_params(parsed),
                    None,
                    _LEGACY_BULK_HEADERS(),
                )
                return
            if path == "/status":
                # Review status: ?status= honored via in-Python equality
                # filtering; a missing status/origin filter yields a labeled
                # legacy/unfiltered envelope, never silent all-rows. Extra
                # typed params (e.g. ?kind=/?subject=) are forwarded so they
                # label as unknown-filter-column instead of being dropped.
                filt = _typed_filter_params(parsed)
                if "status" not in filt and "origin" not in filt:
                    _send_typed(
                        self,
                        _fetch_all(self.db_path, repo=_repo_param(parsed)),
                        filt,
                        "missing-status-filter",
                    )
                    return
                _send_typed(
                    self,
                    _fetch_all(self.db_path, repo=_repo_param(parsed)),
                    filt,
                    None,
                )
                return
            if path == "/conflicts":
                # Evidence conflicts: only provenance artifacts with status
                # "disputed" (plus ?repo= scoping). Never silent all-rows:
                # a missing status column yields a labeled envelope.
                rows = _fetch_all(self.db_path, repo=_repo_param(parsed))
                ok, rows = _apply_column_filter(rows, "status", "disputed")
                if not ok:
                    self._send_json(
                        _labeled_unfiltered(
                            rows, {"status": "disputed"}, "unknown-filter-column:status"
                        )
                    )
                    return
                extra = _typed_filter_params(parsed)
                extra.pop("status", None)
                if extra:
                    _send_typed(self, rows, extra, None)
                    return
                self._send_json(rows)
                return
            if path == "/history":
                # Review history: provenance artifacts scoped by ?repo=, with
                # optional in-Python equality filters; unfiltered responses
                # carry a legacy/unfiltered label.
                _send_typed(
                    self,
                    _fetch_all(self.db_path, repo=_repo_param(parsed)),
                    _typed_filter_params(parsed),
                    "missing-review-filter",
                )
                return
            # M3 read-only views (mount-only, no write verbs). Fixture-backed
            # demo data is served only under explicit opt-in (?demo=1 or
            # BO_M3_DEMO/BO_DEMO/BO_API_DEMO env); the default for an
            # arbitrary temp-db app is abstain, never a fixture leak.
            if path == "/m3/concepts":
                # M3 concepts view: from heuristic extraction (C1)
                if not _demo_opted_in(parsed):
                    self._abstain(404)
                    return
                try:
                    from bo_extract.heuristic_concepts import discover
                    concepts = discover(["fixtures/repo-d", "fixtures/repo-b"])
                    self._send_json(
                        {"demo": True, "origin": "fixture-demo", "data": concepts}
                    )
                except Exception as e:
                    self._send_json({"status": "error", "reason": str(e)}, status=500)
                return
            if path == "/m3/alignment":
                # M3 alignment view: typed mappings (C2)
                if not _demo_opted_in(parsed):
                    self._abstain(404)
                    return
                try:
                    from bo_ontology.alignment import align
                    from bo_extract.heuristic_concepts import discover
                    dev = discover(["fixtures/repo-d", "fixtures/repo-b"])
                    concepts = dev["concepts"]
                    links = dev["links"]
                    mappings = align(concepts, links)
                    self._send_json(
                        {"demo": True, "origin": "fixture-demo", "data": mappings}
                    )
                except Exception as e:
                    self._send_json({"status": "error", "reason": str(e)}, status=500)
                return
            if path == "/m3/workflow":
                # M3 workflow view: ordered steps with evidence (C2)
                if not _demo_opted_in(parsed):
                    self._abstain(404)
                    return
                try:
                    from bo_ontology.alignment import build_workflow
                    from bo_extract.heuristic_concepts import discover
                    dev = discover(["fixtures/repo-d", "fixtures/repo-b"])
                    steps = build_workflow(dev["concepts"])
                    self._send_json(
                        {"demo": True, "origin": "fixture-demo", "data": steps}
                    )
                except Exception as e:
                    self._send_json({"status": "error", "reason": str(e)}, status=500)
                return
            if path.startswith("/provenance/"):
                raw = path[len("/provenance/"):]
                if not raw or "/" in raw:
                    self._abstain(404)
                    return
                try:
                    row_id = int(urllib.parse.unquote(raw))
                except ValueError:
                    self._abstain(404)
                    return
                row = _fetch_one(self.db_path, row_id)
                if row is None:
                    self._abstain(404)
                    return
                self._send_json(row)
                return
            if path in ("/", "/index.html", "/ui", "/inspect"):
                rows = _fetch_all(self.db_path)
                repos = sorted({str(r.get("repo")) for r in rows if r.get("repo") is not None})
                items = []
                for r in rows:
                    items.append(
                        "<li>repo=%s file=%s origin=%s status=%s</li>"
                        % (
                            _html.escape(str(r.get("repo", ""))),
                            _html.escape(str(r.get("file_range", ""))),
                            _html.escape(str(r.get("origin", ""))),
                            _html.escape(str(r.get("status", ""))),
                        )
                    )
                body = (
                    "<!doctype html><html><head><title>Business Ontologies Inspect</title></head>"
                    "<body><h1>Department</h1><h2>Context</h2><h2>Repo</h2>"
                    "<h2>Evidence</h2><ul>" + "".join(items) + "</ul></body></html>"
                )
                self._send_html(body)
                return
            self._abstain(404)

        def log_message(self, *args, **kwargs):
            return

    Handler.db_path = db_path
    return Handler


def run(db_path, host="127.0.0.1", port=0):
    """Serve the read-only app on *host*:*port*.

    M1 is localhost-only: default host is 127.0.0.1 so the API/UI is never
    exposed to the network in this milestone.
    """
    from http.server import ThreadingHTTPServer

    handler = create_app(db_path)
    httpd = ThreadingHTTPServer((host, port), handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


__all__ = ["create_app", "run"]
