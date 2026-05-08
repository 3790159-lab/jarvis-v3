"""Standalone script to run Jarvis as an MCP server for Claude Desktop.

Claude Desktop config (~/.claude/claude_desktop_config.json):
{
  "mcpServers": {
    "jarvis": {
      "command": "python",
      "args": ["C:/path/to/project/scripts/run_mcp_server.py"],
      "env": {
        "JARVIS_BACKEND": "http://127.0.0.1:8010",
        "PYTHONPATH": "C:/path/to/project"
      }
    }
  }
}
"""
import os
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
try:
    from dotenv import load_dotenv
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
except Exception:
    pass

import logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

from app.services.mcp_server import JarvisMCPServer

if __name__ == "__main__":
    server = JarvisMCPServer()
    server.serve_stdio()
