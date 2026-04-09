import json
import os
from pathlib import Path


def read_mcp_tools(claude_json_path: str = "~/.claude.json") -> dict:
    path = Path(os.path.expanduser(claude_json_path))
    try:
        with open(path) as f:
            data = json.load(f)
        tools = sorted(data.get("mcpServers", {}).keys())
        return {"tools": tools, "source": str(claude_json_path)}
    except FileNotFoundError:
        return {"tools": [], "source": None, "error": f"{claude_json_path} not found"}
    except json.JSONDecodeError as e:
        return {"tools": [], "source": None, "error": f"Invalid JSON in {claude_json_path}: {e}"}
    except Exception as e:
        return {"tools": [], "source": None, "error": str(e)}
