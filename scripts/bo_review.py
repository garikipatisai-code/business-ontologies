#!/usr/bin/env python3
"""Business ontology review CLI write path (T3).

Human-in-loop publication via status transitions. Construction ≠ publication
(C2). All operations are append-only with full audit history persisted in the
ontology store.

Commands:
* propose: create a new review with proposed status
* accept: transition to accepted status
* reject: transition to rejected status
* dispute: transition to disputed status
* correct: correct a review decision (transition with original_status)
* history: view review history for a subject
* help: show this help message

Usage examples:
  bo_review.py propose --subject "refund_window_days" --reviewer "alice" --reason "initial review"
  bo_review.py accept --subject "refund_window_days" --reviewer "bob" --reason "evidence validated"
  bo_review.py history --subject "refund_window_days"

Options:
  --db:      ontology store database path (default: local ontologies.db)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bo_ontology.review import record_review, transition_review
from bo_ontology.schema import Status, validate_status
from bo_ontology.store import OntologyStore

DEFAULT_DB = "ontologies.db"


def _open_store(args):
    """Open (and migrate) the ontology store for *args.db* (default ontologies.db)."""
    db = getattr(args, "db", None) or DEFAULT_DB
    return OntologyStore(db)


def _base_review(args):
    """Build a validated review mapping from CLI args (proposed by default)."""
    status = validate_status(getattr(args, "status", None) or Status.PROPOSED.value)
    return {
        "subject": args.subject,
        "status": status,
        "reviewer": args.reviewer,
        "reason": args.reason,
        "version": 1,
    }


def _persist(store, review):
    """Persist one review decision; the reviews table is the append-only audit."""
    store.record_review(review)
    return review


def create_review(args):
    """Create a new review with proposed status."""
    store = _open_store(args)
    review = record_review(_base_review(args))
    _persist(store, review)

    print("Review created:")
    print(json.dumps(review, indent=2, sort_keys=True))
    return review


def accept_review(args):
    """Accept a review decision."""
    store = _open_store(args)
    review = record_review(_base_review(args))
    _persist(store, review)
    updated = transition_review(review, "accepted")
    _persist(store, updated)

    print("Review accepted:")
    print(json.dumps(updated, indent=2, sort_keys=True))
    return updated


def reject_review(args):
    """Reject a review decision."""
    store = _open_store(args)
    review = record_review(_base_review(args))
    _persist(store, review)
    updated = transition_review(review, "rejected")
    _persist(store, updated)

    print("Review rejected:")
    print(json.dumps(updated, indent=2, sort_keys=True))
    return updated


def dispute_review(args):
    """Mark a review as disputed."""
    store = _open_store(args)
    review = record_review(_base_review(args))
    _persist(store, review)
    updated = transition_review(review, "disputed")
    _persist(store, updated)

    print("Review disputed:")
    print(json.dumps(updated, indent=2, sort_keys=True))
    return updated


def correct_review(args):
    """Correct a review decision (transition with original_status)."""
    store = _open_store(args)
    review = record_review(_base_review(args))
    _persist(store, review)
    updated = transition_review(review, args.new_status)
    if args.original_status is not None:
        prior = validate_status(args.original_status)
        stored_prior = review.get("status")
        if prior != stored_prior:
            raise ValueError(
                f"original_status {prior!r} does not match stored prior {stored_prior!r}; "
                "original_status must equal the prior status (no silent overwrite)"
            )
        updated["original_status"] = prior
    _persist(store, updated)

    print("Review corrected:")
    print(json.dumps(updated, indent=2, sort_keys=True))
    return updated


def show_history(args):
    """Show review history for a subject (from store.reviews(subject))."""
    store = _open_store(args)
    rows = store.reviews(args.subject)
    print(f"History for subject '{args.subject}': ({len(rows)} review(s))")
    for row in rows:
        print(json.dumps(row, indent=2, sort_keys=True))
    return rows


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Business ontology review CLI")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help="ontology store database path (default: %(default)s)")
    subparsers = parser.add_subparsers(dest="command", help="command to execute")

    # Propose command
    parser_propose = subparsers.add_parser("propose", help="create a new review")
    parser_propose.add_argument("--subject", required=True, help="subject of the review")
    parser_propose.add_argument("--reviewer", required=True, help="who is performing the review")
    parser_propose.add_argument("--reason", required=True, help="reason for the review")

    # Accept command
    parser_accept = subparsers.add_parser("accept", help="accept a review")
    parser_accept.add_argument("--subject", required=True, help="subject of the review")
    parser_accept.add_argument("--reviewer", required=True, help="who is accepting the review")
    parser_accept.add_argument("--reason", required=True, help="reason for acceptance")
    parser_accept.add_argument("--status", help="current status of the review (default: proposed)")

    # Reject command
    parser_reject = subparsers.add_parser("reject", help="reject a review")
    parser_reject.add_argument("--subject", required=True, help="subject of the review")
    parser_reject.add_argument("--reviewer", required=True, help="who is rejecting the review")
    parser_reject.add_argument("--reason", required=True, help="reason for rejection")
    parser_reject.add_argument("--status", help="current status of the review (default: proposed)")

    # Dispute command
    parser_dispute = subparsers.add_parser("dispute", help="mark a review as disputed")
    parser_dispute.add_argument("--subject", required=True, help="subject of the review")
    parser_dispute.add_argument("--reviewer", required=True, help="who is disputing the review")
    parser_dispute.add_argument("--reason", required=True, help="reason for dispute")
    parser_dispute.add_argument("--status", help="current status of the review (default: proposed)")

    # Correct command
    parser_correct = subparsers.add_parser("correct", help="correct a review decision")
    parser_correct.add_argument("--subject", required=True, help="subject of the review")
    parser_correct.add_argument("--reviewer", required=True, help="who is correcting the review")
    parser_correct.add_argument("--reason", required=True, help="reason for correction")
    parser_correct.add_argument("--status", help="current status of the review (default: proposed)")
    parser_correct.add_argument("--original-status", required=True, help="original status being corrected from")
    parser_correct.add_argument("--new-status", required=True, help="new status after correction")

    # History command
    parser_history = subparsers.add_parser("history", help="show review history")
    parser_history.add_argument("--subject", required=True, help="subject to show history for")

    args = parser.parse_args()

    if args.command == "propose":
        create_review(args)
    elif args.command == "accept":
        accept_review(args)
    elif args.command == "reject":
        reject_review(args)
    elif args.command == "dispute":
        dispute_review(args)
    elif args.command == "correct":
        correct_review(args)
    elif args.command == "history":
        show_history(args)
    else:
        parser.print_help()
        sys.exit(1)
if __name__ == "__main__":
    main()
