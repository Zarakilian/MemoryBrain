-- v3.0.0: provenance (writer, trust), validity windows for facts and
-- decisions, embedding state, chunk vectors, an audit trail and entities.
-- Adds only. Existing rows get safe defaults.
ALTER TABLE memories ADD COLUMN embedded INTEGER NOT NULL DEFAULT 1;
ALTER TABLE memories ADD COLUMN writer TEXT NOT NULL DEFAULT '';
ALTER TABLE memories ADD COLUMN trust TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE memories ADD COLUMN valid_from TEXT;
ALTER TABLE memories ADD COLUMN valid_to TEXT;
ALTER TABLE memories ADD COLUMN invalidated_by TEXT;
ALTER TABLE memories ADD COLUMN content_updated_at TEXT;
UPDATE memories SET trust = 'derived' WHERE type = 'belief' OR source = 'consolidation';
UPDATE memories SET valid_from = timestamp WHERE type IN ('fact', 'decision') AND valid_from IS NULL;
UPDATE memories SET content_updated_at = timestamp WHERE content_updated_at IS NULL;
ALTER TABLE vec_memories ADD COLUMN model TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_vec_memories_model ON vec_memories(model);
CREATE TABLE IF NOT EXISTS vec_chunks (
  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  chunk_ix INTEGER NOT NULL, start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
  model TEXT NOT NULL DEFAULT '', dim INTEGER NOT NULL, embedding BLOB NOT NULL,
  PRIMARY KEY (memory_id, chunk_ix));
CREATE INDEX IF NOT EXISTS idx_vec_chunks_memory ON vec_chunks(memory_id);
CREATE TABLE IF NOT EXISTS memory_audit (
  id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, action TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_audit_memory ON memory_audit(memory_id, at);
CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, norm TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_norm ON entities(kind, norm);
CREATE TABLE IF NOT EXISTS entity_mentions (
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  count INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (entity_id, memory_id));
CREATE INDEX IF NOT EXISTS idx_mentions_memory ON entity_mentions(memory_id);
CREATE INDEX IF NOT EXISTS idx_memories_embedded ON memories(embedded) WHERE embedded = 0;
CREATE INDEX IF NOT EXISTS idx_memories_valid ON memories(project, type, valid_to);
