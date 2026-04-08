"""Tests for URL validation (H2 OLLAMA_URL, H3 BRAIN_URL)."""
import pytest
from app.summarise import validate_ollama_url


def test_localhost_http_accepted():
    assert validate_ollama_url("http://localhost:11434") == "http://localhost:11434"


def test_docker_service_name_accepted():
    assert validate_ollama_url("http://ollama:11434") == "http://ollama:11434"


def test_127_0_0_1_accepted():
    assert validate_ollama_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_https_accepted():
    assert validate_ollama_url("https://ollama.internal:11434") == "https://ollama.internal:11434"


def test_ftp_scheme_rejected():
    with pytest.raises(ValueError, match="scheme"):
        validate_ollama_url("ftp://evil.com/payload")


def test_no_scheme_rejected():
    with pytest.raises(ValueError, match="scheme"):
        validate_ollama_url("ollama:11434")


def test_empty_string_rejected():
    with pytest.raises(ValueError):
        validate_ollama_url("")


def test_file_scheme_rejected():
    with pytest.raises(ValueError, match="scheme"):
        validate_ollama_url("file:///etc/passwd")
