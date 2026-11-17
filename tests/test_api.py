import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from bo_api.server import create_app
from bo_store.provenance import init_db, record_artifact

ROOT = Path(__file__).resolve().parents[1]


def _make_db(tmp_path):
    db = tmp_path / "prov.db"
    init_db(db)
    record_artifact(
        db,
        repo="repo-a",
        commit_sha="a" * 40,
        file_path="app.py",
        line_start=11,
        line_end=11,
        content_hash="c" * 64,
        extractor="deterministic",
        version="1.0",
        run_id="run1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=1.0,
        basis="stdlib ast cross-repo call resolution",
        excerpt="OrderService.createOrder -> RefundPolicy.applyWindow",
    )
    return db


def _server(db):
    handler = create_app(db)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _url(httpd, path):
    return "http://127.0.0.1:%d%s" % (httpd.server_port, path)


def test_get_returns_persisted_rows(tmp_path):
    db = _make_db(tmp_path)
    httpd, thread = _server(db)
    try:
        for path in ("/evidence", "/rules", "/relations"):
            with urllib.request.urlopen(_url(httpd, path)) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["repo"] == "repo-a"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_unknown_provenance_id_returns_404_abstain(tmp_path):
    db = _make_db(tmp_path)
    httpd, thread = _server(db)
    try:
        try:
            urllib.request.urlopen(_url(httpd, "/provenance/999999"))
        except urllib.error.HTTPError as e:
            assert e.code == 404
            body = json.loads(e.read().decode("utf-8"))
            assert body["status"] == "abstain"
            assert body["reason"] == "no-evidence"
        else:
            raise AssertionError("expected 404")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_provenance_known_id_returns_row(tmp_path):
    db = _make_db(tmp_path)
    httpd, thread = _server(db)
    try:
        with urllib.request.urlopen(_url(httpd, "/provenance/1")) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
        assert isinstance(data, dict)
        assert data["id"] == 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_traversal_query_returns_400(tmp_path):
    db = _make_db(tmp_path)
    httpd, thread = _server(db)
    try:
        try:
            urllib.request.urlopen(_url(httpd, "/evidence?path=../../etc/passwd"))
        except urllib.error.HTTPError as e:
            assert e.code == 400
        else:
            raise AssertionError("expected 400")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_no_write_verbs_registered():
    handler = create_app(":memory:")
    for verb in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        assert not hasattr(handler, verb), verb


def test_redaction_holds(tmp_path):
    db = tmp_path / "prov.db"
    init_db(db)
    record_artifact(
        db,
        repo="repo-a",
        commit_sha="a" * 40,
        file_path="app.py",
        line_start=1,
        line_end=1,
        content_hash="b" * 64,
        extractor="struct",
        version="1.0",
        run_id="run1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=0.9,
        basis="ast",
        excerpt="STRIPE_KEY=sk-test-REDACT-ME",
        redacted=True,
    )
    httpd, thread = _server(db)
    try:
        with urllib.request.urlopen(_url(httpd, "/evidence")) as resp:
            body = resp.read().decode("utf-8")
        assert "sk-test" not in body
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_html_endpoint(tmp_path):
    db = _make_db(tmp_path)
    httpd, thread = _server(db)
    try:
        with urllib.request.urlopen(_url(httpd, "/")) as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
        assert "evidence" in body.lower()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _make_two_repo_db(tmp_path):
    db = tmp_path / "prov.db"
    init_db(db)
    record_artifact(
        db,
        repo="repo-a",
        commit_sha="a" * 40,
        file_path="app.py",
        line_start=11,
        line_end=11,
        content_hash="c" * 64,
        extractor="deterministic",
        version="1.0",
        run_id="run1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=1.0,
        basis="stdlib ast cross-repo call resolution",
        excerpt="OrderService.createOrder -> RefundPolicy.applyWindow",
    )
    record_artifact(
        db,
        repo="repo-b",
        commit_sha="b" * 40,
        file_path="rules.py",
        line_start=4,
        line_end=4,
        content_hash="d" * 64,
        extractor="deterministic",
        version="1.0",
        run_id="run1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="accepted",
        confidence=1.0,
        basis="stdlib ast class-attribute constant assignment",
        excerpt="refund_window_days = 30",
    )
    return db


def test_repo_filter_scopes_rows_parameterized(tmp_path):
    """?repo= scoping uses a parameterized WHERE and returns only that repo's rows."""
    db = _make_two_repo_db(tmp_path)
    httpd, thread = _server(db)
    try:
        for path in ("/evidence", "/rules", "/relations"):
            with urllib.request.urlopen(_url(httpd, path + "?repo=repo-b")) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["repo"] == "repo-b"
            assert data[0]["file_range"] == "rules.py:4-4"
        # no filter returns both rows
        with urllib.request.urlopen(_url(httpd, "/evidence")) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert len(data) == 2
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
