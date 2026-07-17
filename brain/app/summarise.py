import os
from abc import ABC, abstractmethod
from typing import Optional
from urllib.parse import urlparse


SHORT_CONTENT_THRESHOLD = 400


class SummariseProvider(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    async def summarise(self, content: str, max_sentences: int = 3) -> str: ...

    @abstractmethod
    async def score_importance(self, content: str) -> int: ...

    def _verbatim_if_short(self, content: str) -> Optional[str]:
        return content if len(content) <= SHORT_CONTENT_THRESHOLD else None


class OllamaProvider(SummariseProvider):
    def __init__(self):
        import ollama as _ollama
        url = self._validate_url(os.getenv("OLLAMA_URL", "http://ollama:11434"))
        self._client = _ollama.AsyncClient(host=url)
        self._embed_model = os.getenv("OLLAMA_EMBED_MODEL", "embeddinggemma")
        self._summarise_model = os.getenv("OLLAMA_SUMMARISE_MODEL", "llama3.2:3b")

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"OLLAMA_URL scheme must be http or https, got '{parsed.scheme}'")
        return url

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings(model=self._embed_model, prompt=text)
        return response["embedding"]

    async def summarise(self, content: str, max_sentences: int = 3) -> str:
        verbatim = self._verbatim_if_short(content)
        if verbatim is not None:
            return verbatim
        prompt = (
            f"Summarise the following in {max_sentences} sentences. "
            f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
        )
        response = await self._client.generate(model=self._summarise_model, prompt=prompt)
        return response["response"].strip()

    async def score_importance(self, content: str) -> int:
        prompt = (
            "Rate the importance of this note for future reference from 1 to 5. "
            "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
            f"Reply with ONLY the digit:\n\n{content[:500]}"
        )
        response = await self._client.generate(model=self._summarise_model, prompt=prompt)
        try:
            return int(response["response"].strip()[0])
        except (ValueError, IndexError):
            return 3


class GeminiProvider(SummariseProvider):
    def __init__(self):
        from google import genai
        self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        self._embed_model = os.getenv("GEMINI_EMBED_MODEL", "models/text-embedding-004")
        self._summarise_model = os.getenv("GEMINI_SUMMARISE_MODEL", "gemini-2.0-flash")

    async def embed(self, text: str) -> list[float]:
        import asyncio
        result = await asyncio.to_thread(
            self._client.models.embed_content, model=self._embed_model, contents=text
        )
        return result.embedding

    async def summarise(self, content: str, max_sentences: int = 3) -> str:
        verbatim = self._verbatim_if_short(content)
        if verbatim is not None:
            return verbatim
        import asyncio
        prompt = (
            f"Summarise the following in {max_sentences} sentences. "
            f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
        )
        response = await asyncio.to_thread(
            self._client.models.generate_content, model=self._summarise_model, contents=prompt
        )
        return response.text.strip()

    async def score_importance(self, content: str) -> int:
        import asyncio
        prompt = (
            "Rate the importance of this note from 1 to 5. "
            "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
            f"Reply with ONLY the digit:\n\n{content[:500]}"
        )
        response = await asyncio.to_thread(
            self._client.models.generate_content, model=self._summarise_model, contents=prompt
        )
        try:
            return int(response.text.strip()[0])
        except (ValueError, IndexError):
            return 3


class OpenAIProvider(SummariseProvider):
    def __init__(self):
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        self._embed_model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        self._summarise_model = os.getenv("OPENAI_SUMMARISE_MODEL", "gpt-4o-mini")

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(model=self._embed_model, input=text)
        return response.data[0].embedding

    async def summarise(self, content: str, max_sentences: int = 3) -> str:
        verbatim = self._verbatim_if_short(content)
        if verbatim is not None:
            return verbatim
        response = await self._client.chat.completions.create(
            model=self._summarise_model,
            messages=[{
                "role": "user",
                "content": (
                    f"Summarise the following in {max_sentences} sentences. "
                    f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
                ),
            }],
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()

    async def score_importance(self, content: str) -> int:
        response = await self._client.chat.completions.create(
            model=self._summarise_model,
            messages=[{
                "role": "user",
                "content": (
                    "Rate the importance of this note from 1 to 5. "
                    "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
                    f"Reply with ONLY the digit:\n\n{content[:500]}"
                ),
            }],
            max_tokens=1,
        )
        try:
            return int(response.choices[0].message.content.strip()[0])
        except (ValueError, IndexError):
            return 3


# Backward-compatible module-level alias kept for test_url_validation.py
def validate_ollama_url(url: str) -> str:
    """Validate OLLAMA_URL: must be http or https scheme. Raises ValueError otherwise."""
    if not url or not url.strip():
        raise ValueError("OLLAMA_URL must not be empty")
    return OllamaProvider._validate_url(url)


def get_provider() -> SummariseProvider:
    """Auto-select: Gemini if GOOGLE_API_KEY set, OpenAI if OPENAI_API_KEY set, else Ollama."""
    if os.getenv("GOOGLE_API_KEY"):
        return GeminiProvider()
    if os.getenv("OPENAI_API_KEY"):
        return OpenAIProvider()
    return OllamaProvider()


_provider: Optional[SummariseProvider] = None


def _get_provider() -> SummariseProvider:
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


# Public interface — unchanged so nothing else needs to update
async def embed(text: str) -> list[float]:
    return await _get_provider().embed(text)


async def summarise(content: str, max_sentences: int = 3) -> str:
    return await _get_provider().summarise(content, max_sentences)


async def score_importance(content: str) -> int:
    return await _get_provider().score_importance(content)


# Backward-compatible aliases used by main.py's /readiness endpoint.
# These proxy through to the active OllamaProvider when applicable;
# if a non-Ollama provider is active they return None / empty string,
# and the readiness check degrades gracefully.
def _get_ollama_client():
    """Return the underlying Ollama AsyncClient, or None if not using Ollama."""
    p = _get_provider()
    return p._client if isinstance(p, OllamaProvider) else None


def _get_embed_model() -> str:
    p = _get_provider()
    return p._embed_model if hasattr(p, "_embed_model") else ""


def _get_summarise_model() -> str:
    p = _get_provider()
    return p._summarise_model if hasattr(p, "_summarise_model") else ""
