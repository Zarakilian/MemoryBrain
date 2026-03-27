# tests/test_summarise.py
import pytest
from app.summarise import embed, summarise, score_importance


@pytest.mark.asyncio
async def test_embed_returns_float_list(mock_ollama):
    result = await embed("grafana dashboard for monitoring")
    assert isinstance(result, list)
    assert len(result) == 768
    assert all(isinstance(x, float) for x in result)


@pytest.mark.asyncio
async def test_summarise_returns_non_empty_string(mock_ollama):
    result = await summarise("Very long content about monitoring dashboards " * 50)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_score_importance_returns_int_1_to_5(mock_ollama):
    score = await score_importance("trivial note about nothing")
    assert isinstance(score, int)
    assert 1 <= score <= 5


@pytest.mark.asyncio
async def test_score_importance_defaults_to_3_on_bad_response(mock_ollama):
    mock_ollama.generate.side_effect = None
    mock_ollama.generate.return_value = {"response": "not a number"}
    score = await score_importance("something")
    assert score == 3


@pytest.mark.asyncio
async def test_embed_handles_long_content(mock_ollama):
    """Long content should not raise."""
    result = await embed("word " * 10000)
    assert isinstance(result, list)
    assert len(result) == 768
