-- v2.2.0: multi-AI context bank — pins, project policy, retrieval telemetry.
--
-- Pins are the working set: goals, env truths, active branch, constraints.
-- They always float to the top of get_project_brief and are excluded from
-- consolidation decay (forgetting is ranking — pins refuse to sink).
--
-- project_policy tunes brief size and whether the system ops lane is injected.
-- retrieval_events is optional telemetry for future ranking calibration.

CREATE TABLE IF NOT EXISTS project_pins (
    project    TEXT NOT NULL,
    memory_id  TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'truth'
               CHECK (kind IN ('goal','truth','branch','constraint','open_loop','custom')),
    label      TEXT NOT NULL DEFAULT '',
    priority   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_pins_project ON project_pins(project);
CREATE INDEX IF NOT EXISTS idx_pins_memory  ON project_pins(memory_id);

CREATE TABLE IF NOT EXISTS project_policy (
    project           TEXT PRIMARY KEY,
    include_system    INTEGER NOT NULL DEFAULT 1,
    max_brief_chars   INTEGER NOT NULL DEFAULT 3500,
    default_tags      TEXT NOT NULL DEFAULT '[]',
    notes             TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_events (
    id          TEXT PRIMARY KEY,
    project     TEXT,
    query       TEXT NOT NULL,
    result_ids  TEXT NOT NULL DEFAULT '[]',
    chosen_id   TEXT,
    source      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_project
    ON retrieval_events(project, created_at);
