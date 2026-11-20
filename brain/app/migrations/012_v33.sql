-- v3.3.0: who wrote each summary, when a memory was last checked and found
-- still true, which memory corrects it, and name patterns set per install.
-- Adds only. Existing rows get safe defaults.
ALTER TABLE memories ADD COLUMN summary_source TEXT NOT NULL DEFAULT '';
ALTER TABLE memories ADD COLUMN verified_at TEXT;
ALTER TABLE memories ADD COLUMN verified_by TEXT;
ALTER TABLE memories ADD COLUMN corrected_by TEXT;
UPDATE memories SET summary_source = 'verbatim' WHERE summary = content;
CREATE INDEX IF NOT EXISTS idx_memories_corrected ON memories(corrected_by)
  WHERE corrected_by IS NOT NULL;
CREATE TABLE IF NOT EXISTS entity_patterns (
  kind TEXT NOT NULL, pattern TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (kind, pattern));
