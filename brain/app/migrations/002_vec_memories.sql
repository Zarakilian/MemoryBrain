-- v2.0.0: vector storage inside brain.db (replaces embedded ChromaDB).
-- Plain table + sqlite-vec's vec_distance_cosine() at query time: exact KNN,
-- fully SQL-filterable via join to memories. dim guards against mixed
-- embedding providers (768 = embeddinggemma, others differ).
CREATE TABLE IF NOT EXISTS vec_memories (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    dim       INTEGER NOT NULL,
    embedding BLOB NOT NULL
);
