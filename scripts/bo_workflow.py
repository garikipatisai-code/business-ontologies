#!/usr/bin/env python3
"""Multi-repo workflow CLI (stdlib only: argparse + json).

Samples::

    python3 scripts/bo_workflow.py ingest --manifest m.json --db out.db
    python3 scripts/bo_workflow.py report --db out.db
    python3 scripts/bo_workflow.py review --db out.db --subject NAME \\
        --reviewer qa --reason "looks good"
    python3 scripts/bo_workflow.py invalidate --db out.db --kind params \\
        --id 1 --reason "window 30->45" --new-value 45

The manifest is JSON only (``{"repos": [{"name": ..., "path": ...}]}``);
YAML manifests are rejected. Repo paths must exist locally -- remote
fetches are never attempted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bo_workflow.orchestrator import (  # noqa: E402
    ingest_manifest,
    invalidate_root,
    record_review,
    report,
)


def _fail(message: str) -> int:
    print(f"bo_workflow: error: {message}", file=sys.stderr)
    return 2


def _record_id(text: str):
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


def _new_value(text: str):
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def cmd_ingest(args: argparse.Namespace) -> int:
    try:
        summary = ingest_manifest(args.manifest, args.db)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return _fail(str(exc))
    except Exception as exc:  # never a traceback crash
        return _fail(f"{type(exc).__name__}: {exc}")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    try:
        result = record_review(
            args.db, args.subject, args.reviewer, args.reason,
            status=args.status,
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return _fail(str(exc))
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def cmd_invalidate(args: argparse.Namespace) -> int:
    try:
        if args.new_value is None:
            result = invalidate_root(
                args.db, args.kind, _record_id(args.id), reason=args.reason
            )
        else:
            result = invalidate_root(
                args.db, args.kind, _record_id(args.id),
                reason=args.reason, new_value=_new_value(args.new_value),
            )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return _fail(str(exc))
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    try:
        data = report(args.db)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return _fail(str(exc))
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
    print(json.dumps(data, indent=2, sort_keys=True, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified multi-repo workflow orchestrator "
                    "(JSON manifest, declared-only links)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="ingest manifest repos into a DB")
    ingest.add_argument("--manifest", required=True)
    ingest.add_argument("--db", required=True)
    ingest.set_defaults(func=cmd_ingest)

    review = sub.add_parser("review", help="record a review decision")
    review.add_argument("--db", required=True)
    review.add_argument("--subject", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--status", default="accepted")
    review.set_defaults(func=cmd_review)

    invalidate = sub.add_parser("invalidate", help="cascade-invalidate a root")
    invalidate.add_argument("--db", required=True)
    invalidate.add_argument("--kind", required=True)
    invalidate.add_argument("--id", required=True)
    invalidate.add_argument("--reason", default=None)
    invalidate.add_argument("--new-value", default=None)
    invalidate.set_defaults(func=cmd_invalidate)

    show = sub.add_parser("report", help="inspect active/stale/ledger/reviews")
    show.add_argument("--db", required=True)
    show.set_defaults(func=cmd_report)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
