-- 0002 dependencies: explicit dependency edges for invalidation cascade.
-- Idempotent: uses IF NOT EXISTS. Applied by OntologyStore init after the
-- versioned migrations (migration runner stays at version 1).

CREATE TABLE IF NOT EXISTS dependencies (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    src_kind TEXT NOT NULL,
    src_id   TEXT NOT NULL,
    dst_kind TEXT NOT NULL,
    dst_id   TEXT NOT NULL,
    reason   TEXT,
    UNIQUE (src_kind, src_id, dst_kind, dst_id)
);

CREATE INDEX IF NOT EXISTS idx_dep_src ON dependencies (src_kind, src_id);
CREATE INDEX IF NOT EXISTS idx_dep_dst ON dependencies (dst_kind, dst_id);
