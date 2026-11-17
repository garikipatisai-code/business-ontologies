#!/usr/bin/env python3
"""Smoke test for M1 read-only API/UI inspect path."""

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from bo_store.provenance import init_db, record_artifact
from bo_api.server import create_app


def main():
    db = os.path.join(tempfile.gettempdir(), "bo_smoke.db")
    if os.path.exists(db):
        os.remove(db)
    init_db(db)
    rid = record_artifact(
        db,
        repo="repo-a",
        commit_sha="a" * 40,
        file_path="fixtures/repo-a/app.py",
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
    handler = create_app(db)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % httpd.server_port
    try:
        for path in ("/rules", "/relations", "/provenance/%d" % rid):
            with urllib.request.urlopen(base + path) as resp:
                assert resp.status == 200, (path, resp.status)
                json.loads(resp.read().decode("utf-8"))
                print("OK", path)
        with urllib.request.urlopen(base + "/") as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "evidence" in body.lower()
            print("OK / (html)")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    print("smoke_m1: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
