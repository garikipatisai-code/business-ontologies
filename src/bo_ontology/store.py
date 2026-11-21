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
from .migration import MIGRATIONS_DIR, apply_migrations

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

_ABSENT = object()
"""Sentinel distinguishing 'new_value not supplied' from explicit None (storable)."""


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
        self._ensure_dependencies()

    def __enter__(self) -> "OntologyStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owns:
            self.conn.close()

    # ------------------------------------------------------------ internals

    def _ensure_dependencies(self) -> None:
        """Apply the dependencies-edge table (same mechanism as migrations)."""
        path = MIGRATIONS_DIR / "0002_dependencies.sql"
        if path.is_file():
            self.conn.executescript(path.read_text(encoding="utf-8"))
            self.conn.commit()

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

    def invalidate(
        self,
        kind: str,
        record_id: Any,
        reason: str | None = None,
        new_value: Any = _ABSENT,
    ) -> dict:
        """Mark a record stale/superseded through the audit-log store path.

        The pure ``invalidate_artifact`` helper touches no DB; this method
        owns persistence/audit: flips status to stale (via ``update_status``),
        optionally stores ``new_value`` (explicit ``None`` is a storable
        value, omission preserves the current value), appends one
        ``invalidate`` audit entry preserving ``original_*`` (no silent
        carry-over), and leaves unrelated rows untouched. Returns the
        invalidated record with ``value``/``original_*`` aliases.
        Raises ``ValueError`` if ``new_value`` is supplied for a kind with
        no value column (concepts/operations/states/events/evidence):
        there is nowhere to persist it, so claiming it in-memory would
        be lost on the next read.
        """
        table = self._table_for(kind)
        row = self.get(kind, record_id)
        if row is None:
            raise KeyError(f"unknown {table} record: {record_id!r}")
        orig_status = row.get("status")
        if "object" in _COLUMNS[table]:
            value_key: str | None = "object"
        elif "value" in _COLUMNS[table]:
            value_key = "value"
        else:
            value_key = None
        orig_value = row.get(value_key) if value_key else row.get("value", row.get("object"))
        if new_value is not _ABSENT and value_key is None:
            raise ValueError(
                f"kind {kind!r} has no value column; new_value cannot be stored"
            )
        # Reuse the validated status path (audits a "status" entry) where the
        # table carries a status column (params/evidence have none: keep the
        # invalidate audit + value update, skip the status flip).
        if "status" in _COLUMNS[table]:
            self.update_status(kind, record_id, "stale")
        if new_value is not _ABSENT:
            self.conn.execute(
                f"UPDATE {table} SET {value_key} = ? WHERE id = ?",
                (_encode(new_value), record_id),
            )
        detail: dict[str, Any] = {
            "reason": reason or "Artifact changed out-of-band (invalidated)",
            "original_status": orig_status,
            "original_value": orig_value,
        }
        if new_value is not _ABSENT:
            detail["new_value"] = new_value
        self._audit(table, record_id, "invalidate", detail)
        self.conn.commit()
        updated = self.get(kind, record_id)
        assert updated is not None
        result = dict(updated)
        # Status-less tables (params/evidence) carry no status column: do not
        # fabricate in-memory ``status``/``superseded`` keys that a fresh
        # read could never return (idempotence for these rows is decided via
        # audit history instead).
        if "status" in _COLUMNS[table]:
            result["status"] = schema.Status.STALE.value
            result["superseded"] = True
        result["invalidation_reason"] = (
            reason or "Artifact changed out-of-band (invalidated)"
        )
        result["original_status"] = orig_status
        result["original_value"] = orig_value
        if value_key == "object":
            result["value"] = result.get("object")
        return result

    @staticmethod
    def _coerce_id(value: Any) -> Any:
        """Restore integer primary keys stored in the TEXT edge columns."""
        if isinstance(value, str) and value.lstrip("-").isdigit():
            try:
                return int(value)
            except ValueError:
                pass
        return value

    def add_dependency(
        self,
        src_kind: str,
        src_id: Any,
        dst_kind: str,
        dst_id: Any,
        reason: str | None = None,
    ) -> Any:
        """Link two existing records; no name-similarity guessing.

        Both endpoints must already exist (``KeyError`` otherwise) and the
        edge must be new (duplicate edges raise ``ValueError``). Unknown
        kinds raise ``ValueError`` via ``_table_for``.
        """
        src_table = self._table_for(src_kind)
        dst_table = self._table_for(dst_kind)
        if src_table == "reviews" or dst_table == "reviews":
            raise ValueError(
                "reviews rows are audit history and cannot take part in "
                "dependency edges"
            )
        reason = redact_value(reason)
        if self.get(src_table, src_id) is None:
            raise KeyError(f"unknown {src_table} record: {src_id!r}")
        if self.get(dst_table, dst_id) is None:
            raise KeyError(f"unknown {dst_table} record: {dst_id!r}")
        exists = self.conn.execute(
            "SELECT id FROM dependencies WHERE src_kind = ? AND src_id = ?"
            " AND dst_kind = ? AND dst_id = ?",
            (src_table, str(src_id), dst_table, str(dst_id)),
        ).fetchone()
        if exists is not None:
            raise ValueError(
                f"duplicate dependency: {src_table}:{src_id} -> "
                f"{dst_table}:{dst_id}"
            )
        try:
            cursor = self.conn.execute(
                "INSERT INTO dependencies (src_kind, src_id, dst_kind, dst_id,"
                " reason) VALUES (?, ?, ?, ?, ?)",
                (src_table, str(src_id), dst_table, str(dst_id), reason),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"duplicate dependency: {src_table}:{src_id} -> "
                f"{dst_table}:{dst_id}"
            ) from exc
        edge_id = cursor.lastrowid
        self._audit(
            "dependencies",
            edge_id,
            "link",
            {
                "src_kind": src_table,
                "src_id": str(src_id),
                "dst_kind": dst_table,
                "dst_id": str(dst_id),
                "reason": reason,
            },
        )
        self.conn.commit()
        return edge_id

    def direct_dependents(self, kind: str, record_id: Any) -> list[dict]:
        """Return explicit ``(dst_kind, dst_id, reason)`` edges, by edge id."""
        table = self._table_for(kind)
        rows = self.conn.execute(
            "SELECT dst_kind, dst_id, reason FROM dependencies"
            " WHERE src_kind = ? AND src_id = ? ORDER BY id",
            (table, str(record_id)),
        ).fetchall()
        return [
            {
                "dst_kind": row["dst_kind"],
                "dst_id": self._coerce_id(row["dst_id"]),
                "reason": row["reason"],
            }
            for row in rows
        ]

    def _already_invalidated(self, table: str, record_id: Any) -> bool:
        """True when an ``invalidate`` audit entry already covers a record.

        Status-less tables (params/evidence) have no status column, so the
        ``status == "stale"`` check can never fire for them; history is the
        idempotence signal there.
        """
        return any(
            entry.get("action") == "invalidate"
            for entry in self.history(table, record_id)
        )

    def _is_stale_row(
        self, table: str, row: dict | None, record_id: Any
    ) -> bool:
        """True when a cascade should traverse but not re-audit a record."""
        if row is None:
            return True
        if row.get("status") == "stale":
            return True
        return "status" not in _COLUMNS.get(
            table, ()
        ) and self._already_invalidated(table, record_id)

    def invalidate_cascade(
        self,
        kind: str,
        record_id: Any,
        reason: str | None = None,
        new_value: Any = _ABSENT,
    ) -> list[tuple]:
        """Invalidate a record and its explicit dependents, breadth-first.

        The root goes through ``self.invalidate`` (with ``new_value`` when
        supplied); each dependent goes through ``self.invalidate`` with a
        trigger reason chaining root -> parent plus the original reason, and
        never receives ``new_value``. Already-stale records — the root
        included — are traversed but not re-audited (status-less rows carry
        no status flag, so a prior ``invalidate`` history entry is the
        idempotence signal there), so diamonds/cycles/self-loops and repeat
        cascades yield one ``invalidate`` entry per record. Returns
        invalidated ``(kind, id)`` pairs in BFS order. The ``reviews`` table
        is never touched.
        """
        root_table = self._table_for(kind)
        root_id = self._coerce_id(record_id)
        root_row = self.get(root_table, root_id)
        if root_row is None:
            raise KeyError(f"unknown {root_table} record: {record_id!r}")
        visited = {(root_table, str(record_id))}
        invalidated: list[tuple] = []
        queue: list[tuple] = [(root_table, root_id)]
        if not self._is_stale_row(root_table, root_row, root_id):
            self.invalidate(kind, record_id, reason, new_value)
            invalidated.append((root_table, root_id))
        while queue:
            cur_kind, cur_id = queue.pop(0)
            trigger = f"cascade from {root_table}:{record_id} via {cur_kind}:{cur_id}"
            if reason:
                trigger += f": {reason}"
            for edge in self.direct_dependents(cur_kind, cur_id):
                dst_kind, dst_id = edge["dst_kind"], edge["dst_id"]
                key = (dst_kind, str(dst_id))
                if key in visited:
                    continue
                visited.add(key)
                queue.append((dst_kind, dst_id))
                row = self.get(dst_kind, dst_id)
                if self._is_stale_row(dst_kind, row, dst_id):
                    continue
                self.invalidate(dst_kind, dst_id, trigger)
                invalidated.append((dst_kind, dst_id))
        return invalidated

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
