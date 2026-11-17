#!/usr/bin/env python3
"""M1 verification harness: manifest stability, tests, smoke, guards, ledger."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from bo_store.manifest import load_manifest, verify_manifest  # noqa: E402
from bo_store.provenance import init_db, record_artifact  # noqa: E402

LEDGER_PATH = ROOT / "docs" / "verification-m1-ledger.json"
MANIFEST_PATH = ROOT / "manifests" / "m1-manifest.json"
REQUIREMENTS_PATH = ROOT / "requirements.txt"

FORBIDDEN_NAMES = {"socket", "requests", "urllib", "subprocess", "importlib", "exec"}
# Import allowlist for the egress guard: these stdlib modules are approved for
# the API layer (read-only localhost server) and are not treated as egress.
ALLOWED_IMPORTS = {"urllib.parse", "http.server"}
GUARD_DIRS = [ROOT / "src" / "bo_extract", ROOT / "src" / "bo_store", ROOT / "src" / "bo_api"]


def _run(cmd, cwd=None):
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        text=True,
        capture_output=True,
    )
    return {
        "cmd": cmd,
        "exit": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def check_manifest_hash_stable():
    m = load_manifest(MANIFEST_PATH)
    results = []
    hashes = []
    for _ in range(2):
        ok, errors = verify_manifest(m, workspace_root=str(ROOT))
        payload = {"ok": ok, "errors": errors}
        h = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        results.append(payload)
        hashes.append(h)
    passed = results[0]["ok"] and results[1]["ok"] and hashes[0] == hashes[1]
    return {
        "name": "manifest_verify_hash_stable_2x",
        "pass": passed,
        "detail": {"runs": results, "hashes": hashes},
    }


def check_pytest():
    r = _run([sys.executable, "-m", "pytest", "tests/", "-v"])
    return {
        "name": "pytest",
        "pass": r["exit"] == 0,
        "detail": r,
    }


def check_smoke():
    r = _run([sys.executable, "scripts/smoke_m1.py"])
    return {
        "name": "smoke",
        "pass": r["exit"] == 0 and "smoke_m1: PASS" in r["stdout"],
        "detail": r,
    }


def check_redaction_grep():
    fd, db_path = tempfile.mkstemp(prefix="bo_verify_", suffix=".db")
    os.close(fd)
    try:
        init_db(db_path)
        rid = record_artifact(
            db_path,
            repo="repo-a",
            commit_sha="a" * 40,
            file_path="fixtures/repo-a/app.py",
            line_start=1,
            line_end=1,
            content_hash="d" * 64,
            extractor="deterministic",
            version="1.0",
            run_id="verify",
            timestamp="2026-01-01T00:00:00Z",
            origin="deterministic",
            status="proposed",
            confidence=1.0,
            basis="redaction-check",
            excerpt="token sk-test-SECRET123 should not persist",
        )
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT excerpt FROM provenance WHERE id=?", (rid,)).fetchone()
        conn.close()
        excerpt = row[0] if row else None
        passed = bool(row) and isinstance(excerpt, str) and "sk-test" not in excerpt and "[REDACTED]" in excerpt
        return {
            "name": "redaction_grep",
            "pass": passed,
            "detail": {"id": rid, "excerpt": excerpt},
        }
    finally:
        try:
            os.remove(db_path)
        except OSError:
            pass


def _scan_file(path):
    issues = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except Exception as exc:
        return [f"{path}: parse/read error: {exc}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            # urllib.parse is an approved stdlib submodule (ALLOWED_IMPORTS);
            # a bare `import urllib` is caught by the import scanner below.
            if node.id == "urllib":
                continue
            issues.append(f"{path}:{node.lineno} name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            issues.append(f"{path}:{node.lineno} attr {node.attr}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                issues.append(f"{path}:{node.lineno} call {func.id}")
            elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_NAMES:
                issues.append(f"{path}:{node.lineno} call attr {func.attr}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
                full_names = [a.name for a in node.names]
            else:
                names = []
                full_names = []
                if node.module:
                    names.append(node.module.split(".")[0])
                    full_names.append(node.module)
                names.extend(a.name.split(".")[0] for a in node.names)
                full_names.extend(a.name for a in node.names)
            for n, full in zip(names, full_names):
                # Allow approved stdlib submodules (e.g. urllib.parse, http.server)
                # even though their top-level name is in FORBIDDEN_NAMES.
                if n in FORBIDDEN_NAMES and full not in ALLOWED_IMPORTS:
                    issues.append(f"{path}:{node.lineno} import {n}")
    return issues


def check_egress_guard():
    issues = []
    for d in GUARD_DIRS:
        for p in sorted(d.rglob("*.py")):
            issues.extend(_scan_file(p))
    return {
        "name": "egress_guard",
        "pass": len(issues) == 0,
        "detail": {"issues": issues, "dirs": [str(d) for d in GUARD_DIRS], "forbidden": sorted(FORBIDDEN_NAMES)},
    }


def check_cgr_pin():
    text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    has_pin = "code-graph-rag==0.7.1" in text
    has_hash = "a4132ce7de7222f8de88d41a41f2ab80c87dfe8e" in text
    passed = has_pin and has_hash
    return {
        "name": "cgr_pin",
        "pass": passed,
        "detail": {"has_pin": has_pin, "has_hash": has_hash, "requirements": text.strip()},
    }


def main():
    checks = [
        check_manifest_hash_stable(),
        check_pytest(),
        check_smoke(),
        check_redaction_grep(),
        check_egress_guard(),
        check_cgr_pin(),
    ]
    overall = all(c["pass"] for c in checks)
    ledger = {
        "overall": "PASS" if overall else "FAIL",
        "checks": [
            {"name": c["name"], "status": "PASS" if c["pass"] else "FAIL"}
            for c in checks
        ],
    }
    # include concise details for failures and key info
    ledger["details"] = {}
    for c in checks:
        d = c["detail"]
        if c["name"] in ("pytest", "smoke"):
            ledger["details"][c["name"]] = {
                "exit": d.get("exit"),
                "stdout_tail": (d.get("stdout") or "")[-2000:],
                "stderr_tail": (d.get("stderr") or "")[-1000:],
            }
        else:
            ledger["details"][c["name"]] = d

    LEDGER_PATH.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for c in checks:
        print(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['name']}")
        if c["name"] == "manifest_verify_hash_stable_2x":
            print(f"  hashes: {c['detail']['hashes']}")
        elif c["name"] == "cgr_pin":
            print(f"  pin={c['detail']['has_pin']} hash={c['detail']['has_hash']}")
        elif c["name"] == "egress_guard":
            print(f"  issues={len(c['detail']['issues'])}")
        elif c["name"] == "redaction_grep":
            print(f"  excerpt={c['detail']['excerpt']}")
        elif c["name"] in ("pytest", "smoke"):
            print(f"  exit={c['detail']['exit']}")
            if c["name"] == "smoke":
                for line in (c["detail"].get("stdout") or "").strip().splitlines():
                    print(f"  {line}")

    print(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    print(f"LEDGER: {LEDGER_PATH}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
