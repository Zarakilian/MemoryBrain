-- v2.1.0: the consolidation cycle — the brain that sleeps.
--
-- 1. Reinforcement & decay: every memory carries a strength (multiplied
--    into retrieval ranking; floor 0.2, ceiling 3.0 enforced in code) and
--    a last_recalled timestamp. Retrieval strengthens; consolidation
--    decays what nothing has touched. Forgetting is RANKING, never
--    deletion.
--
-- 2. New edge kinds for the memory graph:
--      derived_from   belief -> the source memories it was distilled from
--      conflicts_with two coexisting memories that look like they disagree
--    SQLite cannot alter a CHECK constraint, so memory_links is rebuilt.

ALTER TABLE memories ADD COLUMN strength REAL NOT NULL DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN last_recalled TEXT;

DROP VIEW IF EXISTS memory_links_all;

CREATE TABLE memory_links_new (
    src_id     TEXT NOT NULL,
    dst_id     TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN
                 ('semantic','tag','reference','session_chain','entity',
                  'derived_from','conflicts_with')),
    weight     REAL NOT NULL CHECK (weight > 0 AND weight <= 1.0),
    directed   INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id, kind)
) WITHOUT ROWID;

INSERT INTO memory_links_new SELECT * FROM memory_links;
DROP TABLE memory_links;
ALTER TABLE memory_links_new RENAME TO memory_links;

CREATE INDEX IF NOT EXISTS idx_links_dst  ON memory_links(dst_id);
CREATE INDEX IF NOT EXISTS idx_links_kind ON memory_links(kind);

CREATE VIEW IF NOT EXISTS memory_links_all AS
    SELECT src_id, dst_id, kind, weight, directed, meta, created_at
    FROM memory_links
    UNION ALL
    SELECT dst_id, src_id, kind, weight, directed, meta, created_at
    FROM memory_links WHERE directed = 0;
