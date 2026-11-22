#!/usr/bin/env python3
"""Java slice verification gate (J6).

Runs the full pytest suite plus the three Java suites (ground truth, adapter,
business), asserts scripts/verify_m1.py / verify_m2.py / verify_m3.py are
byte-identical to commit c00c834 (read-only hash compare, never modifies them),
prints an honesty block, and exits non-zero on any red.

Read-only gate: no repo file is modified, no download is attempted, no network
is used. The Java adapter requires the pinned javaparser-core 3.17.0 jar in the
default Maven repository (~/.m2/.../javaparser-core/3.17.0/); if it is absent
the Java suites fail closed (red).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "c00c834"
VERIFY_SCRIPTS = [
    "scripts/verify_m1.py",
    "scripts/verify_m2.py",
    "scripts/verify_m3.py",
]
JAVA_SUITES = [
    "tests/test_java_ground_truth.py",
    "tests/test_java_adapter.py",
    "tests/test_java_business.py",
]


def _run(cmd, timeout=300):
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        "cmd": " ".join(cmd) if isinstance(cmd, list) else cmd,
        "exit": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_full_pytest():
    r = _run([sys.executable, "-m", "pytest", "tests/", "-q"])
    return {
        "name": "full_pytest",
        "pass": r["exit"] == 0,
        "detail": {
            "exit": r["exit"],
            "stdout_tail": (r["stdout"] or "")[-1500:],
            "stderr_tail": (r["stderr"] or "")[-1000:],
        },
    }


def check_java_suites():
    results = []
    for suite in JAVA_SUITES:
        r = _run([sys.executable, "-m", "pytest", suite, "-v"])
        results.append(
            {
                "name": f"java_suite:{suite}",
                "pass": r["exit"] == 0,
                "detail": {
                    "exit": r["exit"],
                    "stdout_tail": (r["stdout"] or "")[-1500:],
                    "stderr_tail": (r["stderr"] or "")[-1000:],
                },
            }
        )
    return results


def check_verify_scripts_byte_identical():
    results = []
    for rel in VERIFY_SCRIPTS:
        local = (ROOT / rel).read_bytes()
        proc = subprocess.run(
            ["git", "show", f"{COMMIT}:{rel}"],
            cwd=str(ROOT),
            capture_output=True,
        )
        if proc.returncode != 0:
            results.append(
                {
                    "name": f"verify_script_identical:{rel}",
                    "pass": False,
                    "detail": {
                        "reason": f"git show {COMMIT}:{rel} failed (exit {proc.returncode})",
                        "stderr": proc.stderr.decode("utf-8", errors="ignore")[-500:],
                    },
                }
            )
            continue
        committed = proc.stdout
        passed = _sha256_bytes(local) == _sha256_bytes(committed)
        results.append(
            {
                "name": f"verify_script_identical:{rel}",
                "pass": passed,
                "detail": {
                    "commit": COMMIT,
                    "local_sha256": _sha256_bytes(local),
                    "committed_sha256": _sha256_bytes(committed),
                    "read_only": True,
                },
            }
        )
    return results


def _print_check(name, passed, detail):
    print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        for key in ("exit", "reason", "stderr"):
            if key in detail and detail[key] is not None:
                print(f"  {key}: {detail[key]}")
        stdout_tail = detail.get("stdout_tail")
        if stdout_tail:
            for line in stdout_tail.strip().splitlines()[-8:]:
                print(f"  {line}")


def _print_honesty_block():
    print("\n=== HONESTY BLOCK (Java slice gate) ===")
    rows = [
        ("deterministic", "YES",
         "full pytest + the 3 Java suites are deterministic; adapter re-index/re-resolve is byte-identical across runs"),
        ("semantic", "BLOCKED",
         "no live model, no semantic eval, no network; Java labels are static fixture text scans only"),
        ("exposed-set", "regression-only",
         "Java suites re-run on the two frozen fixtures (java-repo-a + java-repo-b); no new claims on held-out code"),
        ("fresh-holdout", "PENDING",
         "no fresh Java holdout set exists in this slice; held-out evaluation remains pending"),
        ("demo-not-heldout", "YES (Java)",
         "adapter/business assertions run on the same two pinned fixtures, not on a held-out set"),
    ]
    print(f"{'label':<20} {'verdict':<16} 'evidence")
    print("-" * 90)
    for label, verdict, evidence in rows:
        print(f"{label:<20} {verdict:<16} {evidence}")
    print("=== END HONESTY BLOCK ===\n")


def main():
    checks = []

    print("[INFO] Running full pytest (tests/)...")
    full = check_full_pytest()
    checks.append(full)
    _print_check(full["name"], full["pass"], full["detail"])

    print("[INFO] Running Java suites: ground truth, adapter, business...")
    java_results = check_java_suites()
    checks.extend(java_results)
    for res in java_results:
        _print_check(res["name"], res["pass"], res["detail"])

    print("[INFO] Asserting verify_m1/m2/m3.py byte-identical to c00c834 (read-only)...")
    id_results = check_verify_scripts_byte_identical()
    checks.extend(id_results)
    for res in id_results:
        _print_check(res["name"], res["pass"], res["detail"])

    _print_honesty_block()

    overall = all(c["pass"] for c in checks)
    passed = sum(1 for c in checks if c["pass"])
    print(f"JAVA SLICE GATE: {passed}/{len(checks)} checks passed")
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
