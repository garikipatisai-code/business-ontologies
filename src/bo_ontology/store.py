"""Typed business-ontology store (SQLite).

Security controls for the write path:

* every record is redacted centrally with ``bo_store.provenance.redact_value``
  before INSERT (and object payloads are encoded only after redaction);
* evidence paths are confined to the workspace root (no traversal/symlink
  escape) when a root is configured;
* all values are bound parameters; table/column names come from internal
  constants only;
* every mutation appends to ``audit_log`` (append-only history).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from bo_store.provenance import redact_value

from . import schema
from .migration import apply_migrations

_TABLE = {
    "BusinessConcept": "concepts",
    "Operation": "operations",
    "PolicyParameter": "params",
    "Rule": "rules",
    "State": "states",
    "Transition": "transitions",
    "Event": "events",
    "Evidence": "evidence",
    "Assertion": "assertions",
    "ReviewDecision": "reviews",
    "Review": "reviews",
}

_KIND_BY_TABLE = {table: kind for kind, table in _TABLE.items()}

# Whitelisted columns, in schema order. SQL identifiers never come from input.
_COLUMNS = {
    "concepts": ("name", "description", "origin", "status", "confidence", "evidence"),
    "operations": ("name", "description", "origin", "status", "confidence", "evidence"),
    "params": ("name", "value", "type", "description"),
    "rules": (
        "name", "value", "statement", "preconditions", "predicate", "params",
        "exceptions", "outcomes", "location", "supporting", "conflicting",
        "limitations", "origin", "status", "confidence", "evidence",
    ),
    "states": ("name", "description", "origin", "status", "evidence"),
    "transitions": (
        "source", "target", "guard", "trigger", "unknown_source", "origin",
        "status", "evidence",
    ),
    "events": ("name", "phase", "origin", "status", "evidence"),
    "evidence": (
        "repo", "commit_sha", "path", "line_start", "line_end", "extractor",
        "version",
    ),
    "assertions": (
        "id", "subject", "predicate", "object", "occurrence", "origin",
        "status", "confidence", "evidence",
    ),
    "reviews": (
        "subject", "status", "reviewer", "reason", "version",
        "original_status", "decided_at",
    ),
}

_JSON_COLUMNS = frozenset({
    "evidence", "preconditions", "params", "exceptions", "outcomes",
    "location", "supporting", "conflicting", "limitations",
})


def confine_path(path: Any, workspace_root: Any = None) -> str:
    """Return a confined repository-relative path or raise ``ValueError``.

    Without a workspace root, absolute paths are allowed but ``..`` segments
    are always rejected. With a root, the resolved path (symlinks included)
    must stay inside that root.
    """
    text = str(path)
    if "\x00" in text:
        raise ValueError("path contains NUL byte")
    raw = Path(text)
    if ".." in raw.parts:
        raise ValueError(f"path escapes workspace: {text!r}")
    if workspace_root is None:
        return raw.as_posix()

    root = Path(str(workspace_root)).resolve()
    target = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        raise ValueError(f"path escapes workspace: {text!r}") from None


def _confine_value(value: Any, workspace_root: Any) -> Any:
    """Recursively confine ``path`` fields inside nested evidence payloads."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "path" and item is not None:
                item = confine_path(item, workspace_root)
            out[key] = _confine_value(item, workspace_root)
        return out
    if isinstance(value, (list, tuple)):
        return [_confine_value(item, workspace_root) for item in value]
    return value


def _encode(value: Any) -> Any:
    """Encode a value for SQLite: JSON for containers, scalars unchanged."""
    value = redact_value(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def _decode(row: sqlite3.Row, table: str) -> dict[str, Any]:
    """Decode one SQLite row, parsing the JSON columns for *table*."""
    out = dict(row)
    for key in _COLUMNS[table]:
        if key not in out or not isinstance(out[key], str):
            continue
        if key in _JSON_COLUMNS or key in ("value", "object"):
            text = out[key].strip()
            if text.startswith(("[", "{")):
                try:
                    out[key] = json.loads(text)
                except ValueError:
                    pass
    if "unknown_source" in out:
        out["unknown_source"] = bool(out["unknown_source"])
    return out


class OntologyStore:
    """CRUD facade over the migrated ontology database."""

    def __init__(self, db=":memory:", workspace_root=None):
        self.workspace_root = workspace_root
        self._owns = not hasattr(db, "execute")
        self.conn = sqlite3.connect(str(db)) if self._owns else db
        self.conn.row_factory = sqlite3.Row
        apply_migrations(self.conn)

    def __enter__(self) -> "OntologyStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owns:
            self.conn.close()

    # ------------------------------------------------------------ internals

    @staticmethod
    def _table_for(kind: str) -> str:
        if kind in _TABLE:
            return _TABLE[kind]
        if kind in _KIND_BY_TABLE:
            return kind
        raise ValueError(f"unknown ontology kind: {kind!r}")

    def _audit(self, table: str, record_id: Any, action: str, detail: Any) -> None:
        payload = json.dumps(redact_value(detail), sort_keys=True, default=str)
        self.conn.execute(
            "INSERT INTO audit_log (kind, record_id, action, detail) "
            "VALUES (?, ?, ?, ?)",
            (table, None if record_id is None else str(record_id), action, payload),
        )

    # --------------------------------------------------------------- writes

    def insert(self, record: Any) -> Any:
        """Insert a typed record (or mapping) and return its primary key."""
        kind = type(record).__name__
        if kind not in _TABLE and isinstance(record, dict) and "kind" in record:
            kind = record["kind"]
        table = self._table_for(kind)
        data = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        data = redact_value(data)
        data = _confine_value(data, self.workspace_root)

        if table == "evidence" and "commit" in data:
            data["commit_sha"] = data.pop("commit")
        columns = _COLUMNS[table]
        if table != "assertions":
            data.pop("id", None)
        unknown = set(data) - set(columns)
        if unknown:
            raise ValueError(f"unknown fields for {table}: {sorted(unknown)}")
        if table == "assertions" and not data.get("id"):
            raise ValueError("assertion id is required (stable content id)")

        names = [column for column in columns if column in data]
        if not names:
            raise ValueError(f"no persistable fields for {table}")
        placeholders = ", ".join("?" for _ in names)
        sql = (
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})"
        )
        cursor = self.conn.execute(sql, [_encode(data[name]) for name in names])
        record_id = data["id"] if table == "assertions" else cursor.lastrowid
        self._audit(table, record_id, "insert", data)
        self.conn.commit()
        return record_id

    add = insert
    save = insert

    def _add(self, kind: str, cls, record: Any, **overrides) -> Any:
        if isinstance(record, cls):
            if overrides:
                record = replace(record, **overrides)
        else:
            data = dict(record)
            data.update(overrides)
            record = cls(**data)
        return self.insert(record)

    def add_concept(self, record, **overrides):
        return self._add("BusinessConcept", schema.BusinessConcept, record, **overrides)

    def add_operation(self, record, **overrides):
        return self._add("Operation", schema.Operation, record, **overrides)

    def add_parameter(self, record, **overrides):
        return self._add("PolicyParameter", schema.PolicyParameter, record, **overrides)

    def add_rule(self, record, **overrides):
        return self._add("Rule", schema.Rule, record, **overrides)

    def add_state(self, record, **overrides):
        return self._add("State", schema.State, record, **overrides)

    def add_transition(self, record, **overrides):
        return self._add("Transition", schema.Transition, record, **overrides)

    def add_event(self, record, **overrides):
        return self._add("Event", schema.Event, record, **overrides)

    def add_evidence(self, record, **overrides):
        return self._add("Evidence", schema.Evidence, record, **overrides)

    def add_assertion(self, record, **overrides):
        return self._add("Assertion", schema.Assertion, record, **overrides)

    def record_review(self, record, **overrides):
        """Persist a review decision; the reviews table is the audit history."""
        return self._add("ReviewDecision", schema.ReviewDecision, record, **overrides)

    def update_status(self, kind: str, record_id: Any, status: str) -> dict | None:
        """Validate and apply an epistemic status change; audit the mutation."""
        table = self._table_for(kind)
        value = schema.validate_status(status)
        self.conn.execute(
            f"UPDATE {table} SET status = ? WHERE id = ?", (value, record_id)
        )
        self._audit(table, record_id, "status", {"status": value})
        self.conn.commit()
        return self.get(table, record_id)

    def delete(self, kind: str, record_id: Any) -> bool:
        """Delete one record and append the deletion to the audit history."""
        table = self._table_for(kind)
        cursor = self.conn.execute(
            f"DELETE FROM {table} WHERE id = ?", (record_id,)
        )
        self._audit(table, record_id, "delete", {"id": record_id})
        self.conn.commit()
        return cursor.rowcount > 0

    # ---------------------------------------------------------------- reads

    def get(self, kind: str, record_id: Any) -> dict | None:
        table = self._table_for(kind)
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (record_id,)
        ).fetchone()
        return None if row is None else _decode(row, table)

    def find(self, kind: str, limit: int | None = None, **filters) -> list[dict]:
        """Return rows matching equality *filters* (all values parameterized)."""
        table = self._table_for(kind)
        clauses, values = [], []
        for key, value in filters.items():
            if key not in _COLUMNS[table]:
                raise ValueError(f"unknown filter for {table}: {key!r}")
            clauses.append(f"{key} = ?")
            values.append(_encode(value))
        sql = f"SELECT * FROM {table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            values.append(int(limit))
        return [_decode(row, table) for row in self.conn.execute(sql, values)]

    # ``list`` is the conventional CRUD name; kept as an alias of find().
    list = find

    def all(self, kind: str) -> list[dict]:
        return self.find(kind)

    def reviews(self, subject: str | None = None) -> list[dict]:
        if subject is None:
            return self.find("reviews")
        return self.find("reviews", subject=subject)

    def history(self, kind: str | None = None, record_id: Any = None) -> list[dict]:
        """Return append-only audit history, newest last."""
        table = None if kind is None else self._table_for(kind)
        clauses, values = [], []
        if table is not None:
            clauses.append("kind = ?")
            values.append(table)
        if record_id is not None:
            clauses.append("record_id = ?")
            values.append(str(record_id))
        sql = "SELECT * FROM audit_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        return [dict(row) for row in self.conn.execute(sql, values)]


def open_store(db=":memory:", workspace_root=None) -> OntologyStore:
    """Open (and migrate) an ontology store."""
    return OntologyStore(db, workspace_root=workspace_root)


init_store = open_store
