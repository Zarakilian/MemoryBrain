-- v2.3.0: ops metadata for auto-sleep scheduler and retrieval feedback.

CREATE TABLE IF NOT EXISTS brain_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_chosen
    ON retrieval_events(chosen_id, created_at)
    WHERE chosen_id IS NOT NULL;
