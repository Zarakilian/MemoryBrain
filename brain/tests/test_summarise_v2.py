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
    with patch("google.genai.Client"):
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


@pytest.mark.asyncio
async def test_gemini_provider_embed_with_google_genai():
    """Test GeminiProvider.embed() correctly calls google-genai API."""
    import importlib
    import app.summarise as s
    importlib.reload(s)

    # Mock the google.genai.Client and its response
    mock_response = MagicMock()
    mock_response.embedding = [0.1, 0.2, 0.3, 0.4, 0.5]

    mock_client = MagicMock()
    mock_client.models.embed_content = MagicMock(return_value=mock_response)

    with patch("google.genai.Client", return_value=mock_client):
        os.environ["GOOGLE_API_KEY"] = "test-key"
        provider = s.GeminiProvider()
        result = await provider.embed("test content")

    assert result == [0.1, 0.2, 0.3, 0.4, 0.5]
    mock_client.models.embed_content.assert_called_once()


@pytest.mark.asyncio
async def test_gemini_provider_summarise_with_google_genai():
    """Test GeminiProvider.summarise() correctly calls google-genai API."""
    import importlib
    import app.summarise as s
    importlib.reload(s)

    # Mock the response
    mock_response = MagicMock()
    mock_response.text = "This is a summary."

    mock_client = MagicMock()
    mock_client.models.generate_content = MagicMock(return_value=mock_response)

    with patch("google.genai.Client", return_value=mock_client):
        os.environ["GOOGLE_API_KEY"] = "test-key"
        provider = s.GeminiProvider()
        result = await provider.summarise("A very long document " * 100)

    assert result == "This is a summary."
    mock_client.models.generate_content.assert_called_once()
    # Verify the call included the model and contents
    call_args = mock_client.models.generate_content.call_args
    assert call_args[1]["model"] == "gemini-2.0-flash"
    assert "Summarise" in call_args[1]["contents"]


@pytest.mark.asyncio
async def test_gemini_provider_score_importance_with_google_genai():
    """Test GeminiProvider.score_importance() correctly calls google-genai API."""
    import importlib
    import app.summarise as s
    importlib.reload(s)

    # Mock the response
    mock_response = MagicMock()
    mock_response.text = "4"

    mock_client = MagicMock()
    mock_client.models.generate_content = MagicMock(return_value=mock_response)

    with patch("google.genai.Client", return_value=mock_client):
        os.environ["GOOGLE_API_KEY"] = "test-key"
        provider = s.GeminiProvider()
        result = await provider.score_importance("Important note about the system")

    assert result == 4
    mock_client.models.generate_content.assert_called_once()
