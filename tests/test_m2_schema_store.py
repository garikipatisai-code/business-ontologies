"""M2 T3 persistent schema/store tests.

Covers docs/architecture-m0.md Sec 7 (typed ontology store) and Sec 8
(provenance origin/status enums, audit trail) per docs/plans/m2-deep-ontology.md
T3 + narrow amendment (this file).

Tests only: every test uses a fresh tmp_path SQLite database. The frozen
extractor grader (tests/test_m2_extractor.py) is never imported or executed.
"""

import hashlib
import sqlite3

import pytest

from bo_ontology.migration import apply_migrations, current_version
from bo_ontology.schema import (
    ORIGINS,
    STATUSES,
    Assertion,
    BusinessConcept,
    Evidence,
    Event,
    Operation,
    Origin,
    PolicyParameter,
    ReviewDecision,
    Rule,
    State,
    Status,
    Transition,
    validate_origin,
    validate_status,
)
from bo_ontology.store import OntologyStore, confine_path


# ----------------------------------------------------------------- enums (Sec 8)

def test_valid_origin_values_accepted():
    """All three defined origins (deterministic/model-inferred/human-reviewed)
    are accepted by the validator and by a typed record."""
    assert set(ORIGINS) == {"deterministic", "model-inferred", "human-reviewed"}
    for member in Origin:
        assert validate_origin(member.value) == member.value
        assert validate_origin(member) == member.value
        assert BusinessConcept(name="c", origin=member.value).origin == member.value


def test_valid_status_values_accepted():
    """Every defined status (proposed/accepted/rejected/disputed/stale) is
    accepted by the validator and by a typed record."""
    assert set(STATUSES) == {"proposed", "accepted", "rejected", "disputed", "stale"}
    for member in Status:
        assert validate_status(member.value) == member.value
        assert validate_status(member) == member.value
        assert BusinessConcept(name="c", status=member.value).status == member.value


def test_invalid_origin_raises_value_error():
    """Unknown origins fail closed with ValueError (no silent coercion)."""
    for bad in ("guessed", "model_inferred", "", None, 7):
        with pytest.raises(ValueError):
            validate_origin(bad)
        with pytest.raises(ValueError):
            BusinessConcept(name="c", origin=bad)


def test_invalid_status_raises_value_error():
    """Unknown statuses fail closed with ValueError. ``superseded`` is not part
    of the Sec 8 status enum (proposed/accepted/rejected/stale/disputed), so it
    is rejected like any other undefined status."""
    for bad in ("superseded", "approved", "draft", "", None):
        with pytest.raises(ValueError):
            validate_status(bad)
        with pytest.raises(ValueError):
            BusinessConcept(name="c", status=bad)


# ----------------------------------------------------------------- migration

def test_migration_applies_twice_idempotent_and_preserves_rows(tmp_path):
    """Re-running the migration is a no-op: existing rows survive and
    PRAGMA user_version stays at 1."""
    db = tmp_path / "migrate.db"
    assert apply_migrations(db) == 1
    with OntologyStore(db) as store:  # constructor applies migrations again
        rid = store.add_concept(BusinessConcept(name="Invoice"))
    assert apply_migrations(db) == 1
    assert current_version(db) == 1
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT name FROM concepts ORDER BY id").fetchall()
        assert [row[0] for row in rows] == ["Invoice"]
        assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 1
        assert rid is not None
    finally:
        conn.close()


# ----------------------------------------------------------------- CRUD round trip

def _insert(store, kind):
    """Create one record of *kind* and return its primary key."""
    if kind == "concepts":
        return store.add_concept(BusinessConcept(name="Invoice", description="billing invoice"))
    if kind == "operations":
        return store.add_operation(Operation(name="issue_refund"))
    if kind == "params":
        return store.add_parameter(PolicyParameter(name="max_refund_days", value=30, type="int"))
    if kind == "rules":
        return store.add_rule(
            Rule(
                name="refund_within_window",
                statement="A refund is allowed within the window.",
                predicate="days <= 30",
            )
        )
    if kind == "states":
        return store.add_state(State(name="pending"))
    if kind == "transitions":
        return store.add_transition(Transition(source="pending", target="approved"))
    if kind == "events":
        return store.add_event(Event(name="order.placed"))
    if kind == "evidence":
        return store.add_evidence(
            Evidence(
                repo="repo-a",
                path="app.py",
                commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                line_start=10,
                line_end=12,
            )
        )
    if kind == "assertions":
        return store.add_assertion(
            Assertion(subject="Invoice", predicate="has_state", object={"state": "pending"})
        )
    raise AssertionError(f"unhandled kind: {kind}")


@pytest.mark.parametrize(
    "kind",
    [
        "concepts",
        "operations",
        "params",
        "rules",
        "states",
        "transitions",
        "events",
        "evidence",
        "assertions",
    ],
)
def test_crud_round_trip(tmp_path, kind):
    """create -> read -> list -> delete for every persistent ontology kind."""
    with OntologyStore(tmp_path / f"{kind}.db") as store:
        rid = _insert(store, kind)
        row = store.get(kind, rid)
        assert row is not None
        assert row["id"] == rid
        assert [item["id"] for item in store.list(kind)] == [rid]
        assert store.delete(kind, rid) is True
        assert store.get(kind, rid) is None
        assert store.list(kind) == []


# ----------------------------------------------------------------- redaction before INSERT

_SECRET_SAMPLES = [
    "key=AKIAIOSFODNN7EXAMPLE",
    "token sk-test-canary-123456",
    "pat ghp_abcdefgh123456",
    "slack xoxb-abcdefgh123456",
    "pem -----BEGIN PRIVATE KEY-----",
]


def test_named_secret_patterns_redacted_before_insert(tmp_path):
    """AKIA/sk-/ghp_/xoxb-/BEGIN PRIVATE KEY canaries are replaced with
    [REDACTED] in the stored row (central redaction runs before INSERT)."""
    with OntologyStore(tmp_path / "redact_named.db") as store:
        ids = [
            store.add_concept(BusinessConcept(name=f"secret-{i}", description=sample))
            for i, sample in enumerate(_SECRET_SAMPLES)
        ]
        for rid, sample in zip(ids, _SECRET_SAMPLES):
            raw = store.conn.execute(
                "SELECT description FROM concepts WHERE id = ?", (rid,)
            ).fetchone()[0]
            assert raw == "[REDACTED]", sample
            assert sample not in raw


def test_high_entropy_token_redacted_before_insert(tmp_path):
    """A high-entropy token matching no named pattern is redacted too."""
    token = "aB3xK9mQ7pL2wR5tY8uV0nZq4Jh6Tg"  # 30 distinct chars, entropy ~4.9
    with OntologyStore(tmp_path / "redact_entropy.db") as store:
        rid = store.add_concept(BusinessConcept(name="session", description=token))
        raw = store.conn.execute(
            "SELECT description FROM concepts WHERE id = ?", (rid,)
        ).fetchone()[0]
        assert raw == "[REDACTED]"


def test_hex_digest_preserved_by_redaction(tmp_path):
    """Pure hex digests (content hashes, commit SHAs) are never redacted."""
    digest = hashlib.sha256(b"content").hexdigest()
    commit = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    with OntologyStore(tmp_path / "redact_hex.db") as store:
        rid = store.add_concept(BusinessConcept(name="digest", description=digest))
        raw = store.conn.execute(
            "SELECT description FROM concepts WHERE id = ?", (rid,)
        ).fetchone()[0]
        assert raw == digest
        ev_id = store.add_evidence(Evidence(repo="repo-a", path="app.py", commit=commit))
        assert store.get("evidence", ev_id)["commit_sha"] == commit


# ----------------------------------------------------------------- path confinement

def test_path_confinement_rejects_absolute_path(tmp_path):
    """Absolute paths outside the workspace root are rejected."""
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(ValueError):
        confine_path("/etc/passwd", root)
    with pytest.raises(ValueError):
        confine_path(str(tmp_path / "outside.py"), root)


def test_path_confinement_rejects_dotdot_traversal(tmp_path):
    """``..`` traversal is rejected with and without a workspace root."""
    root = tmp_path / "workspace"
    root.mkdir()
    for path in ("../secrets.txt", "a/../../etc/passwd", ".."):
        with pytest.raises(ValueError):
            confine_path(path, root)
        with pytest.raises(ValueError):
            confine_path(path, None)


def test_path_confinement_rejects_symlink_escape(tmp_path):
    """A symlink inside the root pointing outside the root is rejected."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        confine_path("escape/leak.py", root)


def test_path_confinement_allows_relative_path_inside_root(tmp_path):
    """Ordinary repo-relative paths inside the root pass through unchanged."""
    root = tmp_path / "workspace"
    root.mkdir()
    assert confine_path("src/app.py", root) == "src/app.py"


def test_store_insert_rejects_traversing_evidence_path(tmp_path):
    """The store confines evidence paths at write time (traversal and absolute
    escapes raise; an in-root path persists)."""
    root = tmp_path / "workspace"
    root.mkdir()
    with OntologyStore(tmp_path / "confine.db", workspace_root=root) as store:
        with pytest.raises(ValueError):
            store.add_evidence(Evidence(repo="repo-a", path="../outside.py"))
        with pytest.raises(ValueError):
            store.add_evidence(Evidence(repo="repo-a", path=str(tmp_path / "abs.py")))
        ev_id = store.add_evidence(Evidence(repo="repo-a", path="src/app.py"))
        assert store.get("evidence", ev_id)["path"] == "src/app.py"


# ----------------------------------------------------------------- audit history (Sec 8)

def test_review_history_persists_and_preserves_original(tmp_path):
    """Review decisions append: identity/time/reason/version are listed and the
    original decision stays intact after a correcting decision."""
    with OntologyStore(tmp_path / "review.db") as store:
        store.record_review(
            ReviewDecision(
                subject="assert-1",
                status="accepted",
                reviewer="alice",
                reason="evidence ok",
                version=1,
                original_status="proposed",
                decided_at="2026-11-17T09:00:00Z",
            )
        )
        store.record_review(
            ReviewDecision(
                subject="assert-1",
                status="disputed",
                reviewer="bob",
                reason="conflicting rule found",
                version=2,
                original_status="accepted",
                decided_at="2026-11-17T10:00:00Z",
            )
        )
        rows = store.reviews("assert-1")
        assert len(rows) == 2
        first, second = rows
        assert (
            first["reviewer"],
            first["decided_at"],
            first["reason"],
            first["version"],
        ) == ("alice", "2026-11-17T09:00:00Z", "evidence ok", 1)
        # original decision preserved after the correction
        assert first["status"] == "accepted"
        assert first["original_status"] == "proposed"
        assert second["status"] == "disputed"
        assert second["original_status"] == "accepted"
        assert len(store.history("reviews")) == 2


def test_audit_history_persists_across_mutations(tmp_path):
    """Every mutation appends to the append-only audit log with a timestamp."""
    with OntologyStore(tmp_path / "audit.db") as store:
        rid = store.add_concept(BusinessConcept(name="Invoice"))
        entries = store.history("concepts", rid)
        assert [entry["action"] for entry in entries] == ["insert"]
        assert entries[0]["at"]
        assert store.delete("concepts", rid) is True
        entries = store.history("concepts", rid)
        assert [entry["action"] for entry in entries] == ["insert", "delete"]
        assert entries[-1]["record_id"] == str(rid)
