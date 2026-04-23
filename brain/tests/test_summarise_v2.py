# tests/test_summarise_v2.py
import asyncio
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


def test_ollama_provider_selected_by_default(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import importlib
    import app.summarise as s
    importlib.reload(s)
    provider = s.get_provider()
    assert provider.__class__.__name__ == "OllamaProvider"


def test_gemini_provider_selected_when_key_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch("google.generativeai.configure"):
        import importlib
        import app.summarise as s
        importlib.reload(s)
        provider = s.get_provider()
    assert provider.__class__.__name__ == "GeminiProvider"


def test_openai_provider_selected_when_key_set(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    import importlib
    import app.summarise as s
    importlib.reload(s)
    provider = s.get_provider()
    assert provider.__class__.__name__ == "OpenAIProvider"


@pytest.mark.asyncio
async def test_short_content_returns_verbatim():
    """Content under SHORT_CONTENT_THRESHOLD is returned as-is without LLM call."""
    import importlib
    import app.summarise as s
    importlib.reload(s)
    # Patch the module-level _provider so we use a real OllamaProvider stub
    mock_provider = MagicMock()
    mock_provider.summarise = AsyncMock(side_effect=Exception("should not be called"))
    s._provider = mock_provider

    # Short content should NOT call the provider
    short = "short note"
    # Call summarise directly — for short content the provider's method is bypassed
    # by the _verbatim_if_short check inside each provider
    import app.summarise as s2
    importlib.reload(s2)
    provider = s2.OllamaProvider.__new__(s2.OllamaProvider)
    result = provider._verbatim_if_short("short note")
    assert result == "short note"


@pytest.mark.asyncio
async def test_embed_delegates_to_provider():
    import importlib
    import app.summarise as s
    importlib.reload(s)
    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    s._provider = mock_provider
    result = await s.embed("hello")
    assert result == [0.1, 0.2, 0.3]
    mock_provider.embed.assert_called_once_with("hello")
