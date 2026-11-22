#!/usr/bin/env python3
"""M3 verification gate (C2).

Exits NON-ZERO if any gate is BLOCKED or FAIL, printing:
- Gate table with results
- Plan hash (82b386f9fd964717e418e1e451d98e16080ddf061c60339508a11ccdd807eedb)
- 2 known extractor-gap failures (BLOCKED)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

# --- configuration
PLAN_HASH = "82b386f9fd964717e418e1e451d98e16080ddf061c60339508a11ccdd807eedb"
PLAN_PATH = Path(__file__).resolve().parents[1] / "docs" / "plans" / "m3-consolidated.md"
ALLOWED = {
    "src/bo_ontology/alignment.py",
    "src/bo_api/views_m3.py",
    "src/bo_api/server.py",
    "scripts/verify_m3.py",
    "docs/m3-capability.md",
    "docs/verification-m3.md",
    "docs/traceability.md",
}

# 2 known sealed-gap failures (extractor deterministic, not in scope)
KNOWN_BLOCKED = {
    "repo-e StoreCreditAccount decision rules",
    "credit_limit / MAX_CREDIT_BALANCE link",
}

# Exact failing test node ids that map to the 2 known sealed extractor gaps.
KNOWN_BLOCKED_TESTS = {
    "tests/test_m3_heldout_sealed.py::test_store_credit_account_decisions_found_with_evidence",
    "tests/test_m3_heldout_sealed.py::test_different_name_pair_credit_limit_max_credit_balance_linked_with_evidence",
}


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bo_extract.heuristic_concepts import discover  # noqa: E402
from bo_ontology.alignment import align, build_workflow  # noqa: E402
from bo_api import views_m3  # noqa: E402

DEV_FIXTURE_REPOS = ["fixtures/repo-d", "fixtures/repo-b"]


# --- helpers

def _dev_heuristic_counts() -> dict:
    """Deterministic heuristic discovery counts over the dev fixture repos."""
    dev = discover(DEV_FIXTURE_REPOS)
    return {
        "concepts": len(dev.get("concepts") or []),
        "rules": len(dev.get("rules") or []),
        "links": len(dev.get("links") or []),
        "abstain": bool(dev.get("abstain")),
    }


def _heuristic_vs_dev_table() -> dict:
    """Heuristic-vs-dev-ground-truth counts (deterministic) + eval arm status."""
    counts = _dev_heuristic_counts()
    status = "OK" if not counts["abstain"] else "FAIL"
    return {
        "status": status,
        "reason": (
            f"heuristic-vs-dev ground truth: concepts={counts['concepts']} "
            f"rules={counts['rules']} links={counts['links']} abstain={counts['abstain']}; "
            "mock arm non-evaluative; model arm BLOCKED (live eval awaits M3B auth record)"
        ),
    }


def _demo_transcript() -> dict:
    """C2 demo transcript: deterministic mock-only walkthrough of the M3 views."""
    dev = discover(DEV_FIXTURE_REPOS)
    concepts = dev.get("concepts") or []
    mappings = align(concepts, links=dev.get("links") or [])
    steps = build_workflow(concepts)
    checks = [
        ("/m3/concepts", views_m3.concepts_json, concepts),
        ("/m3/alignment", views_m3.alignment_json, mappings),
        ("/m3/workflow", views_m3.workflow_json, steps),
    ]
    outcomes = []
    ok = True
    for endpoint, fn, payload in checks:
        try:
            parsed = json.loads(fn(payload))
            valid = parsed is not None
        except Exception:
            valid = False
        ok = ok and valid
        outcomes.append({"endpoint": endpoint, "status": "OK" if valid else "FAIL"})
    return {
        "status": "OK" if ok else "FAIL",
        "reason": "C2 demo transcript (mock-only, dev fixtures): "
        + ", ".join(f"{o['endpoint']}={o['status']}" for o in outcomes),
    }


def _read_seal() -> dict[str, str]:
    repo_root = Path(__file__).resolve().parent.parent
    seal = repo_root / "docs" / "m3_heldout_seal.json"
    if not seal.exists():
        return {}
    return dict(json.loads(seal.read_text(encoding="utf-8"))["files"])


def _recompute_seal_hashes(roots={"fixtures/repo-e"}) -> dict[str, str]:
    repo_root = Path(__file__).resolve().parent.parent.resolve()
    seals = {}
    for root in roots:
        for path in Path(root).rglob("*"):
            if path.is_file():
                seals[str(path.resolve().relative_to(repo_root))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return seals


def _run_pytest(args: list[str]) -> tuple[int, str, str]:
    """Run pytest; return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        ["python3", "-m", "pytest"] + args,
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", errors="ignore"),
        result.stderr.decode("utf-8", errors="ignore"),
    )


def _failed_test_ids(stdout: str) -> set[str]:
    """Collect FAILED node ids from pytest short summary lines."""
    failed: set[str] = set()
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            failed.add(stripped[len("FAILED "):].split(" - ")[0].strip())
    return failed


def _gate_summary() -> dict[str, dict]:
    gates = {}

    # 1. plan hash
    if PLAN_PATH.exists():
        actual = hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()
        gates["plan-hash"] = {
            "status": "OK" if actual == PLAN_HASH else "FAIL",
            "reason": f"expected {PLAN_HASH}, got {actual}" if actual != PLAN_HASH else None,
        }
    else:
        gates["plan-hash"] = {"status": "FAIL", "reason": "file missing"}

    # 2. allowed files count
    touched = [p for p in ALLOWED if Path(p).exists()]
    gates["files"] = {
        "status": "OK" if len(touched) == len(ALLOWED) else "FAIL",
        "reason": f"{len(touched)}/{len(ALLOWED)} files exist" if len(touched) != len(ALLOWED) else None,
    }

    # 3. M1/M2 regression suites green
    m1m2_exit, m1m2_out, m1m2_err = _run_pytest(
        ["tests/test_m2_extractor.py", "tests/test_m2_combined_index.py", "tests/test_m2_ground_truth.py", "-v"]
    )
    gates["M1-M2"] = {
        "status": "GREEN" if m1m2_exit == 0 else "FAIL",
        "reason": None if m1m2_exit == 0 else f"exit {m1m2_exit}",
    }

    # 4. M3A suites green (contract + eval + held-out sealed w/ known BLOCKED gaps)
    m3a_exit, m3a_out, m3a_err = _run_pytest(
        ["tests/test_m3_contract.py", "tests/test_m3_eval.py", "-v"]
    )
    gates["M3A"] = {
        "status": "GREEN" if m3a_exit == 0 else "FAIL",
        "reason": None if m3a_exit == 0 else f"exit {m3a_exit}",
    }

    # 5. alignment suite green (C2)
    align_exit, align_out, align_err = _run_pytest(["tests/test_m3_alignment.py", "-v"])
    gates["M3C alignment"] = {
        "status": "GREEN" if align_exit == 0 else "FAIL",
        "reason": None if align_exit == 0 else f"exit {align_exit}",
    }

    # 6. seal check (recompute vs seal file)
    expected = _read_seal()
    actual = _recompute_seal_hashes()
    seal_ok = expected == actual
    gates["seal"] = {
        "status": "OK" if seal_ok else "FAIL",
        "reason": None if seal_ok else "hash mismatch" if expected else "no seal",
    }

    # 7. M3B protocol suite green (mock-only, live paths BLOCKED)
    # Note: M3B plumbing loads stored prompts from prompts/m3b/*.md
    m3b_exit, m3b_out, m3b_err = _run_pytest(["tests/test_m3b_protocol.py", "-v"])
    gates["M3B protocol"] = {
        "status": "GREEN" if m3b_exit == 0 else "FAIL",
        "reason": None if m3b_exit == 0 else f"exit {m3b_exit}",
    }

    # 8. held-out sealed suite (M3A): BLOCKED on the 2 known extractor gaps, else FAIL
    m3h_exit, m3h_out, m3h_err = _run_pytest(["tests/test_m3_heldout_sealed.py", "-q", "--tb=no"])
    failed = _failed_test_ids(m3h_out)
    if m3h_exit == 0:
        gates["M3A held-out"] = {
            "status": "GREEN",
            "reason": "exposed repo-e regression-only; fresh-holdout PENDING per docs/verification-m3.md",
        }
    elif failed and failed.issubset(KNOWN_BLOCKED_TESTS):
        gates["M3A held-out"] = {
            "status": "BLOCKED",
            "reason": "known sealed extractor gaps: " + ", ".join(sorted(failed)),
        }
    else:
        gates["M3A held-out"] = {
            "status": "FAIL",
            "reason": f"exit {m3h_exit}" if not failed else "unexpected failures: " + ", ".join(sorted(failed)),
        }

    # 9. threshold report (already in verification-m3.md, just read and validate)
    threshold_path = Path(__file__).resolve().parent.parent / "docs" / "verification-m3.md"
    if threshold_path.exists():
        content = threshold_path.read_text(encoding="utf-8")
        # Read thresholds from harness.py
        harness_path = Path(__file__).resolve().parent.parent / "src" / "bo_eval" / "harness.py"
        if harness_path.exists():
            import importlib.util, sys
            spec = importlib.util.spec_from_file_location("harness", str(harness_path))
            harness = importlib.util.module_from_spec(spec)
            sys.modules["harness"] = harness
            spec.loader.exec_module(harness)
            expected = harness.THRESHOLDS
            # Extract thresholds section from verification-m3.md
            lines = content.splitlines()
            in_thresholds_section = False
            found_values = {}
            for line in lines:
                line_lower = line.lower()
                if line_lower.startswith("## thresholds") or line_lower.startswith("## thresholds (") or "thresholds" in line_lower:
                    in_thresholds_section = True
                    continue
                if in_thresholds_section:
                    if line.strip().startswith("## ") and not line_lower.startswith("## thresholds"):
                        # New section starts
                        break
                    if "|" in line and not line.startswith("##"):
                        # Parse table row: | Threshold | Key | Value | Denominator (raw x/y) |
                        parts = [p.strip() for p in line.split("|")]
                        if len(parts) >= 4 and parts[2] and parts[3]:
                            # Remove backticks from key (e.g., `concept` -> concept)
                            key = parts[2].strip(" `")
                            value_part = parts[3].strip()
                        # Extract numeric value, handling >= and =
                        if ">=" in value_part:
                            value_str = value_part.split(">=")[1]
                        elif "=" in value_part:
                            value_str = value_part.split("=")[1]
                        else:
                            value_str = value_part
                        # Clean up value string
                        value_str = value_str.strip()
                        if key in expected:
                            found_values[key] = value_str
            # Check if all expected thresholds are present with correct values (numeric comparison)
            thresholds_ok = True
            mismatch_details = []
            for key in expected:
                if key not in found_values:
                    thresholds_ok = False
                    mismatch_details.append(f"missing {key}")
                    continue
                try:
                    extracted_float = float(found_values[key])
                    expected_float = float(expected[key])
                    if abs(extracted_float - expected_float) > 0.0001:  # Allow small floating point difference
                        thresholds_ok = False
                        mismatch_details.append(f"mismatch {key}: expected {expected_float}, got {extracted_float}")
                except ValueError:
                    thresholds_ok = False
                    mismatch_details.append(f"cannot convert {key}: {found_values[key]}")
            if thresholds_ok:
                gates["thresholds"] = {
                    "status": "PRESENT",
                    "reason": None,
                }
            else:
                gates["thresholds"] = {
                    "status": "FAIL",
                    "reason": "thresholds section missing or incorrect values: " + ", ".join(mismatch_details),
                }
        else:
            gates["thresholds"] = {"status": "FAIL", "reason": "harness.py file missing"}
    else:
        gates["thresholds"] = {"status": "FAIL", "reason": "file missing"}

    # 10. heuristic-vs-model table (deterministic content: heuristic-vs-dev
    #     ground truth counts; mock arm non-evaluative; model arm BLOCKED)
    gates["heuristic-table"] = _heuristic_vs_dev_table()

    # 11. C2 demo transcript (deterministic mock-only dev-fixture walkthrough)
    gates["demo-transcript"] = _demo_transcript()

    return gates


def _print_table(gates: dict[str, dict]):
    print("\n=== M3 VERIFICATION GATE TABLE ===\n")
    print(f"{'Gate':<20} {'Status':<10} {'Reason':<40}")
    print("-" * 70)
    for gate, info in gates.items():
        status = info["status"]
        reason = info["reason"] or ""
        print(f"{gate:<20} {status:<10} {reason:<40}")

    print("\n=== KNOWN BLOCKED (sealed extractor gaps) ===\n")
    for item in KNOWN_BLOCKED:
        print(f"- {item}")

    print(f"\n=== PLAN HASH ===\n{PLAN_HASH}")


def _exit_for_gates(gates: dict[str, dict]) -> None:
    # Non-zero exit if any gate is BLOCKED or FAIL (alignment is GREEN/GREEN, not BLOCKED)
    any_fail = any(info["status"] not in {"OK", "GREEN", "PRESENT"} for info in gates.values())
    if any_fail:
        sys.exit(1)
    # If alignment is GREEN, proceed (BLOCKED failures already recorded)
    sys.exit(0)


# --- main

def main() -> None:
    gates = _gate_summary()
    _print_table(gates)
    _exit_for_gates(gates)


if __name__ == "__main__":
    main()
