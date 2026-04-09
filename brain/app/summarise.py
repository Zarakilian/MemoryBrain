import os
from urllib.parse import urlparse
import ollama

EMBED_MODEL = "embeddinggemma"
SUMMARISE_MODEL = "llama3.2:3b"
SHORT_CONTENT_THRESHOLD = 400  # chars — below this, skip Ollama and store verbatim


def validate_ollama_url(url: str) -> str:
    """Validate OLLAMA_URL: must be http or https scheme. Raises ValueError otherwise."""
    if not url or not url.strip():
        raise ValueError("OLLAMA_URL must not be empty")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"OLLAMA_URL scheme must be http or https, got '{parsed.scheme}'")
    return url


OLLAMA_URL = validate_ollama_url(os.getenv("OLLAMA_URL", "http://localhost:11434"))

_client = ollama.AsyncClient(host=OLLAMA_URL)


async def embed(text: str) -> list[float]:
    response = await _client.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]


async def summarise(content: str, max_sentences: int = 3) -> str:
    if len(content) <= SHORT_CONTENT_THRESHOLD:
        return content  # preserve verbatim — short notes are already concise
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
