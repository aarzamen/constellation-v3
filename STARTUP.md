# Constellation — Definitive Startup Guide

*Ground truth produced 2026-03-20 from verified running system.*

## Prerequisites

| What | Value |
|------|-------|
| Python | 3.12.13 (`.venv/bin/python`) |
| venv | `/Users/ama/constellation-v3/.venv` |
| FastMCP | 3.1.1 |
| cloudflared | 2026.3.0 |
| Embedding model | `all-MiniLM-L6-v2` (384d, ~80MB, auto-cached) |
| Branch | `main` |
| Last commit | `fb1f038 v4.6: surgical fixes — security, naming cleanup, named tunnel, stderr discipline` |
| Git remote | `origin` → `https://github.com/aarzamen/constellation-v3.git` |
| Tests | 101 passed (pytest) |

## Data Sources

| Provider | Path | Conversations | Messages |
|----------|------|---------------|----------|
| Claude | `/Users/ama/Desktop/constellation work/Claude chats/conversations.json` | 975 | 13,431 |
| ChatGPT | `/Users/ama/Desktop/constellation work/GPT chat/conversations.json` | 723 | 11,765 |
| **Total** | | **1,698** | **25,196** |

Date range: 2023-12-13 to 2026-03-07

## Option A: Helper App (recommended)

The Helper is a macOS GUI that manages all servers and the tunnel.

```bash
cd /Users/ama/constellation-v3
source .venv/bin/activate
python constellation_helper.py
```

From the Helper UI:
1. Click "Start REST Server" → starts `launch.py` on port 8420
2. Click "Start MCP Server" → starts `mcp_server.py --transport streamable-http --port 8000`
3. Click "Start Tunnel" → starts named Cloudflare tunnel
4. Click "Open Visualization" → opens browser to http://127.0.0.1:8420

Closing the Helper does NOT stop the servers.

## Option B: Manual Three-Server Start

Open three terminal tabs. All commands assume:
```bash
cd /Users/ama/constellation-v3
source .venv/bin/activate
```

### Terminal 1 — REST Server + Visualization (port 8420)

```bash
python launch.py
```

This will:
- Parse conversations from both providers (uses config.yaml paths)
- Load cached embeddings from `data/` (no re-embed if unchanged)
- Start REST API on http://127.0.0.1:8420
- Open 3D visualization in browser

### Terminal 2 — MCP HTTP Server (port 8000)

```bash
python server/mcp_server.py --transport streamable-http --port 8000
```

Wait for "Uvicorn running on http://127.0.0.1:8000" before proceeding.

### Terminal 3 — Cloudflare Tunnel

```bash
cloudflared tunnel run constellation
```

Wait for "Registered tunnel connection" messages (should see 3-4 connections).

## Option C: Local Only (no tunnel)

### REST + Visualization only
```bash
cd /Users/ama/constellation-v3
source .venv/bin/activate
python launch.py
```

### REST + stdio MCP only (headless)
```bash
python launch.py --headless
```

The MCP server for Claude Desktop/Claude Code uses stdio transport and is spawned automatically by the client — no manual start needed.

## Ports

| Service | Port | Protocol |
|---------|------|----------|
| REST API + Visualization | 8420 | HTTP |
| MCP HTTP Server | 8000 | Streamable HTTP (MCP) |
| Cloudflare Tunnel | — | Routes `mcp.constellation-memory.com` → localhost:8000 |

## Client Configurations

### Claude Desktop (stdio)

File: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add to `mcpServers`:
```json
"constellation-local": {
    "command": "/Users/ama/constellation-v3/.venv/bin/python",
    "args": [
        "/Users/ama/constellation-v3/server/mcp_server.py"
    ]
}
```

Restart Claude Desktop after editing config.

### Claude Code (stdio)

Already configured. Verify with:
```bash
claude mcp list
```

Should show `constellation-local` pointing to `.venv/bin/python` + `server/mcp_server.py`.

To add if missing:
```bash
claude mcp add constellation-local /Users/ama/constellation-v3/.venv/bin/python /Users/ama/constellation-v3/server/mcp_server.py
```

### Claude Cowork (stdio)

Uses the same `claude_desktop_config.json` as Claude Desktop — same entry, same stdio transport. No additional setup.

### claude.ai / iPhone (HTTP via tunnel)

Connector name: **Constellation**
Connector URL: `https://mcp.constellation-memory.com/mcp`

Requires the MCP HTTP server (port 8000) and Cloudflare tunnel to be running.

## MCP Tools (7 total)

| Tool | Description |
|------|-------------|
| `search_conversations` | Hybrid semantic+lexical search with RRF fusion |
| `get_conversation` | Full conversation with messages, notes, cluster info |
| `list_conversations` | Paginated list (offset, limit, sort_by) |
| `list_recent_conversations` | Recent conversations with date filtering |
| `get_stats` | Index statistics (counts, date range, providers) |
| `add_conversation_note` | Append a note to a conversation |
| `delete_conversation_note` | Remove a note by ID |

## Verification Commands

```bash
# REST API health check
curl http://127.0.0.1:8420/api/stats

# MCP HTTP health check
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# Remote tunnel health check
curl -X POST https://mcp.constellation-memory.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# Stdio MCP tool listing
python3 -c "
import subprocess, json
proc = subprocess.Popen(
    ['/Users/ama/constellation-v3/.venv/bin/python',
     '/Users/ama/constellation-v3/server/mcp_server.py'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
init = json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize',
    'params':{'protocolVersion':'2024-11-05','capabilities':{},
    'clientInfo':{'name':'test','version':'1.0'}}}) + '\n'
proc.stdin.write(init.encode()); proc.stdin.flush(); proc.stdout.readline()
notif = json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}) + '\n'
proc.stdin.write(notif.encode()); proc.stdin.flush()
req = json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/list'}) + '\n'
proc.stdin.write(req.encode()); proc.stdin.flush()
resp = json.loads(proc.stdout.readline())
tools = resp.get('result',{}).get('tools',[])
print(f'{len(tools)} tools:')
for t in tools: print(f'  - {t[\"name\"]}')
proc.terminate()
"

# Run tests
python -m pytest tests/ -v
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Port 8420 in use | `lsof -ti:8420 \| xargs kill -9` |
| Port 8000 in use | `lsof -ti:8000 \| xargs kill -9` |
| Tunnel shows 530 error | MCP HTTP server not running on port 8000 |
| Only 5 of 7 tools | Upgrade FastMCP: `pip install --upgrade fastmcp` (need 3.1.1+) |
| `add_conversation_note` missing in Claude Desktop | Restart Claude Desktop after config change |
| Config paths wrong | Edit `config.yaml` — check `source.path` and `sources.chatgpt.path` |
| No `data/` directory | Run `python launch.py` to trigger first embedding pipeline |
| Force re-embed | `python launch.py --reembed` |
| Tunnel remote config overrides local | Edit in Cloudflare dashboard (Zero Trust → Networks → Tunnels → constellation → Public Hostname) |
| `mcp.randobanando.com` not routing | Remote config only has `mcp.constellation-memory.com`; add hostname in Cloudflare dashboard if needed |
| HF Hub rate limit warning | Set `HF_TOKEN` env var (optional, model is cached locally after first download) |

## Cloudflare Tunnel Details

| Field | Value |
|-------|-------|
| Tunnel name | `constellation` |
| Tunnel ID | `dca64438-2081-4be5-8372-7bc51506446b` |
| Credentials | `~/.cloudflared/dca64438-2081-4be5-8372-7bc51506446b.json` |
| Local config | `~/.cloudflared/config.yml` |
| Active hostname | `mcp.constellation-memory.com` → `localhost:8000` |
| Protocol | QUIC |

Note: Cloudflare remote config overrides the local `config.yml` ingress rules. The remote config currently only routes `mcp.constellation-memory.com`. To add `mcp.randobanando.com`, update in the Cloudflare Zero Trust dashboard.
