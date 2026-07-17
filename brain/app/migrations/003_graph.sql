-- v2.0.0: automatic memory graph.
-- Edges are DERIVED data (rebuildable via POST /admin/rebuild-graph).
-- Symmetric kinds (semantic, tag) store one row with src_id < dst_id;
-- memory_links_all exposes both directions for single-join queries.

CREATE TABLE IF NOT EXISTS memory_links (
    src_id     TEXT NOT NULL,
    dst_id     TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN
                 ('semantic','tag','reference','session_chain','entity')),
    weight     REAL NOT NULL CHECK (weight > 0 AND weight <= 1.0),
    directed   INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id, kind)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_links_dst  ON memory_links(dst_id);
CREATE INDEX IF NOT EXISTS idx_links_kind ON memory_links(kind);

CREATE VIEW IF NOT EXISTS memory_links_all AS
    SELECT src_id, dst_id, kind, weight, directed, meta, created_at
    FROM memory_links
    UNION ALL
    SELECT dst_id, src_id, kind, weight, directed, meta, created_at
    FROM memory_links WHERE directed = 0;

CREATE TABLE IF NOT EXISTS tag_stats (
    tag TEXT PRIMARY KEY,
    df  INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

ALTER TABLE memories ADD COLUMN link_degree REAL NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN linked_at TEXT;
