# Constellation Deployment Guide

## Prerequisites

- Python 3.9+ (developed on 3.12)
- A Claude export (conversations.json) from claude.ai Settings > Export
- Optional: ChatGPT export, Gemini AI Studio folder, Grok export

## Installation

```bash
cd constellation-v3
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running

```bash
# Full pipeline: embed + serve + open browser
python3 launch.py

# Headless (API only, no browser)
python3 launch.py --headless

# Force re-embedding
python3 launch.py --reembed

# Custom port
python3 launch.py --port 9000

# Specify source explicitly
python3 launch.py --source /path/to/conversations.json

# Add ChatGPT data
python3 launch.py --chatgpt-source /path/to/chatgpt/conversations.json

# Add any provider
python3 launch.py --add-source gemini /path/to/gemini/folder
```

## MCP Server Setup

The MCP server runs as a standalone subprocess over stdio (JSON-RPC).

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "constellation": {
      "command": "python3",
      "args": ["/absolute/path/to/constellation-v3/server/mcp_server.py"]
    }
  }
}
```

### Claude Code

Add to `.claude/settings.json` (project-level) or `~/.claude/settings.json` (global):

```json
{
  "mcpServers": {
    "constellation": {
      "command": "python3",
      "args": ["/absolute/path/to/constellation-v3/server/mcp_server.py"]
    }
  }
}
```

### claude.ai (Remote via Cloudflare Tunnel)

If you have a Cloudflare Tunnel configured:

1. Start the REST server: `python3 launch.py --headless`
2. Start the tunnel: `cloudflared tunnel run constellation`
3. In claude.ai MCP settings, add the remote URL

### ChatGPT (Custom GPT)

ChatGPT does not natively support MCP. To use Constellation with ChatGPT:

1. Run the REST server: `python3 launch.py --headless`
2. Expose via Cloudflare Tunnel or ngrok
3. Configure as an OpenAPI action in a Custom GPT pointing to the REST endpoints

## Recommended Tool Settings (claude.ai)

| Tool | Setting |
|---|---|
| Constellation | Automatic |
| Gmail | Automatic |
| Google Calendar | Automatic |
| Excalidraw | On demand |
| Mermaid | On demand |
| Hugging Face | On demand |
| Cloudflare tools | On demand |

## Annotation Protocol

See `ANNOTATION_PROTOCOL.md` for the four note patterns (BREADCRUMB, GRAVITY,
TODO, SAFETY) that Claude uses when interacting with Constellation's memory.

## Constellation Helper (macOS)

```bash
python3 constellation_helper.py
```

A macOS control panel for managing Constellation servers. Features:
- Start/stop REST and MCP servers
- View server status, uptime, corpus stats
- Add data sources (Claude, ChatGPT, Gemini, Grok)
- Re-embed with real-time progress dashboard
- Start/stop Cloudflare Tunnel for remote access

## Data Directory

All runtime data is stored in `data/` (gitignored):
- `embeddings.npy` — conversation-level embeddings
- `chunk_embeddings.npy` — message-level embeddings
- `conversations.json` — parsed conversation index
- `graph_data.json` — frontend visualization data
- `notes.json` — persistent conversation notes (sidecar)
- `logs/` — structured JSON logs

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `CONSTELLATION_DATA_DIR` | Override data directory | `<project>/data` |
| `CONSTELLATION_LOG_LEVEL` | Log level | `INFO` |
| `TOKENIZERS_PARALLELISM` | Suppress fork warning | `false` (set by MCP server) |
