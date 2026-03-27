import os
import ollama

EMBED_MODEL = "nomic-embed-text"
SUMMARISE_MODEL = "llama3.2:3b"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

_client = ollama.AsyncClient(host=OLLAMA_URL)


async def embed(text: str) -> list[float]:
    response = await _client.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]


async def summarise(content: str, max_sentences: int = 3) -> str:
    prompt = (
        f"Summarise the following in {max_sentences} sentences. "
        f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
    )
    response = await _client.generate(model=SUMMARISE_MODEL, prompt=prompt)
    return response["response"].strip()


async def score_importance(content: str) -> int:
    prompt = (
        "Rate the importance of this note for future reference from 1 to 5. "
        "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
        f"Reply with ONLY the digit:\n\n{content[:500]}"
    )
    response = await _client.generate(model=SUMMARISE_MODEL, prompt=prompt)
    try:
        return int(response["response"].strip()[0])
    except (ValueError, IndexError):
        return 3
