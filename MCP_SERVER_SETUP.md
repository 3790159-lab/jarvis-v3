# MCP Server Setup — Jarvis as Claude Desktop Tool Provider

Jarvis exposes three tools to Claude Desktop via the Model Context Protocol (MCP):
- **jarvis_research** — web search + Perplexity report
- **jarvis_create_table** — Excel/CSV from query
- **jarvis_parse_file** — PDF/DOCX/XLSX analysis

---

## Prerequisites

1. Claude Desktop installed (Mac / Windows)
2. Jarvis backend running: `python -m uvicorn app.main:app --port 8010`
3. Python in PATH

---

## Step-by-step Setup

### 1. Find Claude Desktop config file

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`  
**Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

### 2. Add Jarvis MCP server

Edit (or create) `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "python",
      "args": ["C:/path/to/project/scripts/run_mcp_server.py"],
      "env": {
        "JARVIS_BACKEND": "http://127.0.0.1:8010",
        "PYTHONPATH": "C:/path/to/project",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "N8N_API_KEY": "your-n8n-key"
      }
    }
  }
}
```

Replace `C:/path/to/project` with actual project path.

### 3. Restart Claude Desktop

Close and reopen Claude Desktop. You should see a hammer icon (🔨) in the input area indicating MCP tools are available.

---

## Usage Examples

Once configured, in Claude Desktop you can say:

> "Use jarvis_research to find the latest news on AI regulation"

> "Use jarvis_create_table to create a table of top 10 programming languages with their use cases"

> "Use jarvis_parse_file to analyze /path/to/report.pdf"

---

## Troubleshooting

### Tools not showing in Claude Desktop
- Verify the path in `args` is correct and absolute
- Check that `python scripts/run_mcp_server.py` runs without errors in terminal
- Look at Claude Desktop logs: **Settings → Developer → View Logs**

### Backend connection errors
- Ensure Jarvis backend is running: `curl http://127.0.0.1:8010/health`
- Check `JARVIS_BACKEND` env var matches backend port

### Python not found
- Use full Python path: `"C:/Python312/python.exe"` instead of `"python"`

---

## Architecture

```
Claude Desktop
     │ MCP JSON-RPC (stdio)
     ▼
scripts/run_mcp_server.py
     │ imports
     ▼
app/services/mcp_server.py (JarvisMCPServer)
     │ HTTP POST
     ▼
http://127.0.0.1:8010  (Jarvis FastAPI backend)
     │
     ├── /api/jarvis/tools/internet/research
     ├── /api/jarvis/tools/table/create
     └── /api/jarvis/tools/file/parse
```
