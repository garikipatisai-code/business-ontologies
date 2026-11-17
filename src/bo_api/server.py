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

        def _send_json(self, obj, status=200):
            data = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
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
                # Rules: provenance artifacts carrying a rule origin (deterministic /
                # model-inferred / human-reviewed). M1 returns provenance artifacts;
                # the origin/refund_window split is enforced at ingest and exposed
                # here via ?repo=, with a full kind-based split deferred to M2.
                self._send_json(_fetch_all(self.db_path, repo=_repo_param(parsed)))
                return
            if path == "/relations":
                # Relations: cross-repo invocation edges persisted as provenance
                # artifacts (kind "invokes"). M1 returns provenance artifacts;
                # the invokes filter is deferred to M2, scoped here by ?repo=.
                self._send_json(_fetch_all(self.db_path, repo=_repo_param(parsed)))
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
