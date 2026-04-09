import json
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.mcp_discovery import read_mcp_tools
from app.main import app

client = TestClient(app)


def test_reads_mcp_servers_sorted(tmp_path):
    config = {
        "mcpServers": {
            "pagerduty": {},
            "clickhouse-iom": {},
            "memorybrain": {},
            "confluence-mcp": {},
        }
    }
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert result["tools"] == ["clickhouse-iom", "confluence-mcp", "memorybrain", "pagerduty"]
    assert result["source"] == str(p)
    assert "error" not in result


def test_returns_empty_when_file_missing(tmp_path):
    result = read_mcp_tools(str(tmp_path / "nonexistent.json"))
    assert result["tools"] == []
    assert result["source"] is None
    assert "error" in result


def test_returns_empty_when_malformed_json(tmp_path):
    p = tmp_path / "claude.json"
    p.write_text("not valid json {{{")
    result = read_mcp_tools(str(p))
    assert result["tools"] == []
    assert "error" in result


def test_memorybrain_appears_in_list(tmp_path):
    config = {"mcpServers": {"memorybrain": {}, "other-tool": {}}}
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert "memorybrain" in result["tools"]


def test_empty_when_no_mcp_servers_key(tmp_path):
    config = {"someOtherKey": "value"}
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert result["tools"] == []
    assert "error" not in result


def test_mcp_tools_endpoint_returns_200():
    fake_result = {"tools": ["clickhouse-iom", "memorybrain"], "source": "~/.claude.json"}
    with patch("app.main.read_mcp_tools", return_value=fake_result):
        resp = client.get("/mcp-tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tools"] == ["clickhouse-iom", "memorybrain"]
    assert "source" in data


def test_returns_empty_when_mcp_servers_not_a_dict(tmp_path):
    config = {"mcpServers": ["not", "a", "dict"]}
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert result["tools"] == []
    assert "error" in result


def test_returns_empty_on_permission_error(tmp_path):
    p = tmp_path / "claude.json"
    p.write_text('{"mcpServers": {"tool": {}}}')
    p.chmod(0o000)
    result = read_mcp_tools(str(p))
    assert result["tools"] == []
    assert "error" in result
    p.chmod(0o644)  # restore so tmp_path cleanup works


def test_returns_empty_list_when_mcp_servers_is_empty(tmp_path):
    config = {"mcpServers": {}}
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert result["tools"] == []
    assert "error" not in result
