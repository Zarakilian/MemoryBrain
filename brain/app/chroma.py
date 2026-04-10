from pathlib import Path
from typing import Optional
import chromadb

CHROMA_PATH = Path("/app/data/chroma")
COLLECTION_NAME = "memories"


def get_client(path: Path = CHROMA_PATH) -> chromadb.ClientAPI:
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def _get_collection(client: Optional[chromadb.ClientAPI] = None) -> chromadb.Collection:
    if client is None:
        client = get_client()
    return client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def chroma_add(
    memory_id: str,
    embedding: list[float],
    metadata: dict,
    client: Optional[chromadb.ClientAPI] = None,
):
    col = _get_collection(client)
    col.upsert(ids=[memory_id], embeddings=[embedding], metadatas=[metadata])


def chroma_search(
    embedding: list[float],
    n_results: int = 20,
    where: Optional[dict] = None,
    client: Optional[chromadb.ClientAPI] = None,
) -> list[dict]:
    col = _get_collection(client)
    count = col.count()
    if count == 0:
        return []
    kwargs: dict = {
        "query_embeddings": [embedding],
        "n_results": min(n_results, count),
    }
    if where:
        kwargs["where"] = where
    results = col.query(**kwargs, include=["metadatas", "distances"])
    if not results["ids"] or not results["ids"][0]:
        return []
    return [
        {"id": id_, "metadata": meta, "distance": dist}
        for id_, meta, dist in zip(
            results["ids"][0], results["metadatas"][0], results["distances"][0]
        )
    ]


def chroma_delete(memory_id: str, client: Optional[chromadb.ClientAPI] = None):
    col = _get_collection(client)
    col.delete(ids=[memory_id])
