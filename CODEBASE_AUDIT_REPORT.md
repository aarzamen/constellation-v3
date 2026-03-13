# Constellation V3 — Codebase Audit & Startup Guide

**Date**: 2026-03-13
**Branch**: `claude/codebase-audit-startup-sj0SW`
**Latest Commit**: `e28272b v4.4: tests and documentation updates`
**Auditor**: Claude Opus 4.6 (automated codebase inspection)

---

## Executive Summary

76 items audited across 8 categories. No files were modified. All findings based on direct file reads and grep searches.

| Metric | Count |
|--------|-------|
| Total Items | 76 |
| Present | 49 |
| Partial | 7 |
| Absent | 20 |

**Strongest areas**: Frontend visualization (11/11), Structured logging (6/6), Timestamps (2/2), Tests (4/4).

**Biggest gaps**: Annotation Protocol (7 items, all absent), DEPLOY.md (2 items, absent), Security hardening (3 real gaps), Helper app UX (2 absent, 2 partial).

---

## PART A: Codebase Audit (76 Items)

### SECURITY & HARDENING (Items 1–10)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | Origin header validation on MCP HTTP transport | ❌ ABSENT | `server/mcp_server.py` is stdio-only. No HTTP transport exists, so origin validation is not applicable. |
| 2 | Request body size limit on HTTP POST | ❌ ABSENT | `server/http_server.py:146-147` reads `Content-Length` with no upper bound check. Known issue #8 in CLAUDE.md. |
| 3 | XSS fix — no inline onclick with unescaped IDs | ❌ ABSENT | `frontend/js/search.js:86` uses `onclick='focusNode(\'${n.id}\', ...)'` with unescaped string interpolation. Known issue #9. |
| 4 | Notes file locking with fcntl.flock | ❌ ABSENT | `core/notes.py` uses `os.replace()` atomic writes instead. No `fcntl` import. Mitigated by POSIX atomicity. |
| 5 | CORS do_OPTIONS handler | ❌ ABSENT | `server/http_server.py` has `do_GET`, `do_POST`, `do_DELETE` but no `do_OPTIONS`. Known issue #4. |
| 6 | All print() use file=sys.stderr | ⚠️ PARTIAL | Violations in `core/parser.py:125`, `core/indexer.py:91,126,174,192,374`, `core/config.py:40`, `server/http_server.py:272,275,279`. Safe in launch.py context but fragile if ever called from MCP. |
| 7 | Recluster writes BOTH graph_data.json AND clusters.json | ⚠️ PARTIAL | `server/http_server.py` handle_recluster writes only `graph_data.json` (lines 232-234). No separate `clusters.json` written. |
| 8 | CDN versions pinned in index.html | ⚠️ PARTIAL | `index.html:176` pins `three@0.155.0` (exact). `index.html:177` uses `3d-force-graph@1` (major-only, not exact). |
| 9 | starlette listed in requirements.txt | ❌ ABSENT | Not a dependency. Project uses stdlib `http.server` by design. |
| 10 | --mcp-port flag in launch.py | ❌ ABSENT | `launch.py:268-280` has `--headless`, `--reembed`, `--port`, `--source`, `--chatgpt-source`, `--add-source`. MCP uses stdio, not a port. By design. |

**Real security gaps**: Items 2, 3, and 5 are genuine vulnerabilities. Items 1, 4, 9, 10 are by-design decisions.

---

### MULTI-PROVIDER INGESTION (Items 11–25)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 11 | `core/chatgpt_parser.py` with `parse_chatgpt_export` | ✅ PRESENT | `core/chatgpt_parser.py:110` |
| 12 | `core/gemini_parser.py` with `parse_gemini_export` | ✅ PRESENT | `core/gemini_parser.py:95` |
| 13 | `core/grok_parser.py` with `parse_grok_export` | ✅ PRESENT | `core/grok_parser.py:49` |
| 14 | `core/provider_registry.py` with `register_parser` | ✅ PRESENT | `core/provider_registry.py:12` |
| 15 | Claude parser sets `provider='claude'` | ✅ PRESENT | `core/parser.py:169` |
| 16 | ChatGPT parser sets `provider='chatgpt'` | ✅ PRESENT | `core/chatgpt_parser.py:161` |
| 17 | `--chatgpt-source` flag in launch.py | ✅ PRESENT | `launch.py:276-277` |
| 18 | `--add-source` generic flag in launch.py | ✅ PRESENT | `launch.py:278-280` with `nargs=2` (PROVIDER, PATH) |
| 19 | config.yaml supports multiple source paths | ⚠️ PARTIAL | `launch.py:306-313` reads `config.get('sources', {})` and saves to it. But `config.yaml.example` does not show the `sources:` key — undocumented. |
| 20 | Graph data nodes include `provider` field | ✅ PRESENT | `core/indexer.py:268` — `'provider': conv.get('provider', 'claude')` |
| 21 | `get_stats` returns providers breakdown | ✅ PRESENT | `server/api.py:273-285` returns `'providers': provider_counts` dict |
| 22 | Search results include `provider` field | ✅ PRESENT | `server/api.py:212` |
| 23 | Provider filter on `search_conversations` MCP tool | ✅ PRESENT | `server/mcp_server.py:31` — `provider: str = None` parameter |
| 24 | Provider filter on `list_conversations` MCP tool | ✅ PRESENT | `server/mcp_server.py:128` — `provider: str = None` parameter |
| 25 | Provider filter on `list_recent_conversations` MCP tool | ❌ ABSENT | No `list_recent_conversations` tool exists. Only `list_conversations`. |

**Provider pipeline is solid**. All four parsers implemented, provider field flows through the full stack. Only gap is a missing `list_recent_conversations` tool.

---

### FRONTEND VISUALIZATION (Items 26–36)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 26 | Node shape differs by provider | ✅ PRESENT | `graph.js:52-90` — OctahedronGeometry (ChatGPT), DodecahedronGeometry (Gemini), IcosahedronGeometry (Grok), SphereGeometry (Claude) |
| 27 | Provider section in sidebar | ✅ PRESENT | `index.html:36-39` — `<div id="provider-section">` with `<div id="provider-list">` |
| 28 | Provider filter toggle logic | ✅ PRESENT | `graph.js:10` — `filteredProviders` Set. `graph.js:154-157` filter check. `app.js:111-119` toggleProvider function. |
| 29 | Node tooltip includes provider | ✅ PRESENT | `graph.js:25-31` — nodeLabel callback maps provider to display label |
| 30 | `--accent-chatgpt` CSS variable | ✅ PRESENT | `constellation.css:32-33` — `--accent-chatgpt: #10a37f` and `--accent-chatgpt-dim: #0d8c6d` |
| 31 | Center View button and function | ✅ PRESENT | `graph.js:374-394` — `centerView()` computes bounding box and flies camera |
| 32 | Cluster layout scales radius by node count | ✅ PRESENT | `graph.js:305-307` — `clusterRadius = Math.max(120, Math.sqrt(nodeCount) * 5)` |
| 33 | 2D flat uses stronger charge repulsion | ✅ PRESENT | `graph.js:290-299` — charge strength `-150` vs 3D's `-80` |
| 34 | Temporal helix has scaling applied | ✅ PRESENT | `graph.js:343-349` — `txScale = 2.0`, `tyScale = 1.5` |
| 35 | Layout switching clears fx/fy/fz BEFORE switch | ✅ PRESENT | `graph.js:257-262` — clears before `switch` at line 275 |
| 36 | Auto-centerView after layout switch | ✅ PRESENT | `graph.js:368-371` — `setTimeout(centerView, 500)` after switch |

**Perfect score. All 11 frontend items present.**

---

### CONSTELLATION HELPER APP (Items 37–50)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 37 | `constellation_helper.py` exists | ✅ PRESENT | 853 lines at project root |
| 38 | Uses customtkinter | ✅ PRESENT | Line 31: `import customtkinter as ctk` |
| 39 | customtkinter in requirements.txt | ✅ PRESENT | Line 5: `customtkinter>=5.2` |
| 40 | Spawns servers with `start_new_session=True` | ✅ PRESENT | Lines 514 (REST), 530 (MCP) |
| 41 | Closing window does NOT stop servers | ✅ PRESENT | `_on_closing()` calls `self.destroy()` only, does not kill server processes |
| 42 | Background status polling thread | ✅ PRESENT | Lines 175-177 daemon thread, lines 749-763 polls every 3 seconds |
| 43 | MCP connection info with Copy buttons | ✅ PRESENT | Lines 316-398 with Copy JSON, Copy URL buttons |
| 44 | "claude" in provider dropdown | ❌ ABSENT | Lines 678-686: `values=['chatgpt', 'gemini', 'grok']` — "claude" missing |
| 45 | Startup wizard / first-run detection | ❌ ABSENT | No wizard flow. Goes directly to `_reconnect_existing()` then `_build_ui()`. |
| 46 | Start Tunnel button runs cloudflared | ✅ PRESENT | Lines 629-635 spawn `cloudflared tunnel --url http://localhost:8000` |
| 47 | Re-embed with progress feedback | ⚠️ PARTIAL | Button exists (line 436-442) but only shows static "Embedding..." text (line 582). No progress bar, percentage, or ETA. |
| 48 | Button state management | ⚠️ PARTIAL | Start button disabled when running (lines 791-795). Stop button state is never managed — can click Stop when nothing is running. |
| 49 | Uptime and PID display | ✅ PRESENT | Lines 798-809 fetch and format actual values from server_info |
| 50 | Conversation/message count | ✅ PRESENT | Lines 801-802 fetch counts with comma formatting |

**Additional bug found**: The tunnel command uses port 8000 (`constellation_helper.py:630`) but the REST server defaults to port 8420. Port mismatch.

---

### STRUCTURED LOGGING (Items 51–56)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 51 | `core/logger.py` with `get_logger` | ✅ PRESENT | `core/logger.py:94-97` |
| 52 | JSON lines to rotating log file | ✅ PRESENT | `core/logger.py:20-44` JSONFormatter to `data/logs/constellation.log` |
| 53 | RotatingFileHandler configured | ✅ PRESENT | `core/logger.py:80-82` — `maxBytes=5MB`, `backupCount=3` |
| 54 | `/api/logs` endpoint | ✅ PRESENT | `server/http_server.py:133-141` |
| 55 | `/api/server-info` endpoint | ✅ PRESENT | `server/http_server.py:125-131` |
| 56 | Server modules use logger | ✅ PRESENT | `mcp_server.py:21,24` and `api.py:14,18` use `get_logger()` |

**Perfect score. All 6 logging items present.**

---

### TIMESTAMP PRESERVATION (Items 57–58)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 57 | Three-tier timestamp extraction | ✅ PRESENT | `core/parser.py:37-58` — `extract_message_timestamp()`: (a) `created_at`, (b) content block `start_timestamp`, (c) `timestamp` fallback |
| 58 | Timestamp test exists | ✅ PRESENT | `tests/test_constellation.py:183-224` — four unit tests plus integration test |

---

### ANNOTATION PROTOCOL (Items 59–65)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 59 | `ANNOTATION_PROTOCOL.md` exists | ❌ ABSENT | No such file at project root |
| 60 | `search_conversations` docstring: "BREADCRUMB" | ❌ ABSENT | `mcp_server.py:32-47` — not mentioned |
| 61 | `search_conversations` docstring: "GRAVITY" | ❌ ABSENT | Not mentioned |
| 62 | `search_conversations` docstring: "TODO" pattern | ❌ ABSENT | Not mentioned |
| 63 | `search_conversations` docstring: "SAFETY" / "explicit approval" | ❌ ABSENT | Not mentioned |
| 64 | `add_conversation_note` docstring: note format templates | ❌ ABSENT | `mcp_server.py:82-91` — no BREADCRUMB/GRAVITY/TODO templates |
| 65 | `get_conversation` docstring: notes from prior sessions | ❌ ABSENT | `mcp_server.py:60-69` — mentions provider but not notes/prior sessions |

**Entire annotation protocol is unimplemented.** No document, no docstring enrichment, no note templates.

---

### DOCUMENTATION (Items 66–72)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 66 | `DEPLOY.md` exists with MCP install instructions | ❌ ABSENT | No `DEPLOY.md` at project root |
| 67 | `DEPLOY.md` tool settings recommendation table | ❌ ABSENT | No `DEPLOY.md` exists |
| 68 | CLAUDE.md references annotation protocol | ❌ ABSENT | No "annotation" found in CLAUDE.md |
| 69 | CLAUDE.md documents Constellation Helper | ✅ PRESENT | CLAUDE.md lines 110-127: `## Constellation Helper` section |
| 70 | README.md mentions multi-provider support | ❌ ABSENT | No ChatGPT/Gemini/Grok/multi-provider content in README.md |
| 71 | `tools/export_fetcher.py` exists | ✅ PRESENT | 9,748 bytes in `tools/` |
| 72 | `tools/README.md` exists | ✅ PRESENT | 625 bytes in `tools/` |

---

### TESTS (Items 73–76)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 73 | `tests/test_chatgpt_parser.py` | ✅ PRESENT | 282 lines |
| 74 | `tests/test_gemini_parser.py` | ✅ PRESENT | 164 lines |
| 75 | `tests/test_multi_provider.py` | ✅ PRESENT | 142 lines |
| 76 | `tests/test_notes.py` | ✅ PRESENT | 275 lines |

**Full test file listing**: `test_constellation.py`, `test_chatgpt_parser.py`, `test_gemini_parser.py`, `test_logger.py`, `test_multi_provider.py`, `test_notes.py` (33 tests collected).

---

## Remaining Work — All Absent Items

| Priority | # | Item | Category |
|----------|---|------|----------|
| High | 2 | Request body size limit on HTTP POST | Security |
| High | 3 | XSS fix — inline onclick with unescaped IDs | Security |
| High | 5 | CORS do_OPTIONS handler | Security |
| Medium | 25 | `list_recent_conversations` MCP tool | Multi-Provider |
| Medium | 44 | "claude" in helper provider dropdown | Helper App |
| Medium | 45 | Startup wizard / first-run detection | Helper App |
| Medium | 66 | DEPLOY.md with MCP install instructions | Documentation |
| Medium | 67 | DEPLOY.md tool settings table | Documentation |
| Medium | 70 | README.md multi-provider mention | Documentation |
| Low | 59 | ANNOTATION_PROTOCOL.md | Annotation Protocol |
| Low | 60 | search_conversations docstring: BREADCRUMB | Annotation Protocol |
| Low | 61 | search_conversations docstring: GRAVITY | Annotation Protocol |
| Low | 62 | search_conversations docstring: TODO pattern | Annotation Protocol |
| Low | 63 | search_conversations docstring: SAFETY | Annotation Protocol |
| Low | 64 | add_conversation_note docstring: templates | Annotation Protocol |
| Low | 65 | get_conversation docstring: prior session notes | Annotation Protocol |
| Low | 68 | CLAUDE.md references annotation protocol | Documentation |
| N/A | 1 | Origin validation (stdio, not HTTP) | By Design |
| N/A | 9 | starlette in requirements (uses stdlib) | By Design |
| N/A | 10 | --mcp-port flag (MCP uses stdio) | By Design |

---

## PART B: Definitive Startup & Operations Guide

### Section 1: Prerequisites

**Python**: 3.9+ required (CLAUDE.md spec). Current machine has Python 3.11.14. No `.venv` exists on this machine.

**Pip packages** (from `requirements.txt`):

- `numpy>=1.24`
- `sentence-transformers>=2.2`
- `pyyaml>=6.0`
- `fastmcp>=0.1`
- `customtkinter>=5.2`

Install command:

```bash
pip install -r /home/user/constellation-v3/requirements.txt
```

Note: `sentence-transformers` downloads the `all-MiniLM-L6-v2` model (~90MB) on first run if not cached.

**cloudflared**: NOT installed on this machine. Required only for remote/tunnel access. Install on macOS with `brew install cloudflared`.

---

### Section 2: Starting Everything (The Full Stack)

**Step 1 — Activate environment**

```bash
cd /home/user/constellation-v3
source .venv/bin/activate  # If .venv exists; otherwise use system python3
```

**Step 2 — First-time setup: provide data source**

Place your Claude export `conversations.json` somewhere accessible. On first run, the system creates `config.yaml` from `config.yaml.example` (gitignored).

```bash
# Claude data
python3 launch.py --source /path/to/conversations.json

# ChatGPT data
python3 launch.py --chatgpt-source /path/to/chatgpt/conversations.json

# Gemini data
python3 launch.py --add-source gemini /path/to/gemini/folder

# Grok data
python3 launch.py --add-source grok /path/to/grok/file.json
```

After first run, source paths are saved in `config.yaml` and reused automatically.

**Step 3 — Start the REST/Visualization Server**

```bash
# Full mode: embeds data, starts HTTP server on port 8420, opens browser
python3 launch.py

# Headless mode: API only, no browser
python3 launch.py --headless

# Custom port
python3 launch.py --port 9000

# Force re-embedding
python3 launch.py --reembed
```

Default port: **8420** (configured in config.yaml under `server.port`). Default host: **127.0.0.1**.

**Step 4 — MCP Server**

The MCP server is a separate stdio subprocess. It is NOT started by launch.py. It's launched automatically by MCP clients (Claude Desktop, Claude Code). For standalone testing:

```bash
python3 /home/user/constellation-v3/server/mcp_server.py
```

This loads data from `data/`, pre-loads the embedding model, then listens on stdin/stdout for JSON-RPC. It does NOT listen on a network port.

**Important**: The `data/` directory must exist with embeddings before the MCP server will work. Run `python3 launch.py` at least once first.

**Step 5 — Cloudflare Tunnel (optional, for remote access)**

```bash
cloudflared tunnel --url http://localhost:8420
```

Creates a temporary `*.trycloudflare.com` URL.

**Warning**: The helper app hardcodes port 8000 for the tunnel (`constellation_helper.py:630`), but the REST server defaults to port 8420. If using the helper's tunnel button, verify the port.

**Step 6 — Verify everything is running**

```bash
# Check REST server
curl http://localhost:8420/api/stats

# Check server info
curl http://localhost:8420/api/server-info

# Check recent logs
curl "http://localhost:8420/api/logs?n=10"

# Search test
curl -X POST http://localhost:8420/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test query", "top_k": 3}'

# Open visualization
open http://localhost:8420       # macOS
xdg-open http://localhost:8420   # Linux
```

---

### Section 3: Using the Helper App

**Start it**:

```bash
cd /home/user/constellation-v3
python3 constellation_helper.py
```

Requires `customtkinter` and a display environment (macOS GUI or X11).

**What it does**:

- Does NOT auto-start servers — you click "Start Servers"
- Spawns REST server (`launch.py --headless`) and MCP server as detached subprocesses
- Servers survive closing the helper window
- Shows status (uptime, PID, conversation/message counts) via polling every 3 seconds
- Displays MCP connection JSON for Claude Desktop with Copy buttons
- "Start Tunnel" button runs cloudflared
- "Re-embed" button re-runs embedding pipeline
- "Add Source" button for ChatGPT/Gemini/Grok sources

**Known bugs**:

1. Provider dropdown missing "claude" — only offers chatgpt, gemini, grok
2. No startup wizard or first-run guidance
3. Re-embed shows static "Embedding..." text — no progress bar or percentage
4. Stop button never disabled when servers are stopped
5. Tunnel command uses port 8000 but REST server defaults to 8420

---

### Section 4: MCP Installation

**Claude Desktop** — Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "constellation": {
      "command": "python3",
      "args": ["/home/user/constellation-v3/server/mcp_server.py"]
    }
  }
}
```

If using a venv, use the venv python path:

```json
{
  "mcpServers": {
    "constellation": {
      "command": "/home/user/constellation-v3/.venv/bin/python3",
      "args": ["/home/user/constellation-v3/server/mcp_server.py"]
    }
  }
}
```

**Claude Code** — Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (global):

```json
{
  "mcpServers": {
    "constellation": {
      "command": "python3",
      "args": ["/home/user/constellation-v3/server/mcp_server.py"]
    }
  }
}
```

**Claude.ai / iPhone (Connectors)**: NOT currently supported. The MCP server is stdio-only. Remote MCP access would require an HTTP/SSE transport layer (not implemented). A Cloudflare tunnel exposes the REST API, not the MCP protocol.

**ChatGPT as MCP client**: NOT supported. ChatGPT is a data source (conversations to ingest), not an MCP client for this project.

---

### Section 5: Restarting After Code Changes

**Stop all running servers**:

```bash
pkill -f "launch.py"
pkill -f "mcp_server.py"
pkill -f "cloudflared"

# Verify
ps aux | grep -E "launch|mcp_server|cloudflared" | grep -v grep
```

**Start again**:

```bash
cd /home/user/constellation-v3
python3 launch.py --headless  # REST server
# MCP server is launched by Claude Desktop/Code automatically
```

**When is re-embed needed?**

- **Re-embed needed**: New conversation data added, embedding model changed, new provider sources added. Run `python3 launch.py --reembed`.
- **Just restart (no re-embed)**: Server code changes (http_server.py, mcp_server.py, api.py), frontend code changes (JS/CSS/HTML), config.yaml setting changes. The `data/` artifacts remain valid.

**What gets regenerated on re-embed?**

- `data/embeddings.npy` — all embeddings (regenerated)
- `data/conversations.json` — parsed conversation data (regenerated)
- `data/graph_data.json` — 3D visualization graph (regenerated)
- `data/clusters.json` — cluster assignments (regenerated)
- `data/notes.json` — sidecar notes file (NOT regenerated, survives re-embed)

---

### Section 6: Current State (as of 2026-03-13)

**Branch**: `claude/codebase-audit-startup-sj0SW` (active). Also has `master` local and `remotes/origin/main`.

**Most recent commit**: `e28272b v4.4: tests and documentation updates`

**Data files**: No `data/` directory exists. The embedding pipeline has never been run on this machine.

**Config**: No `config.yaml` exists. Will be auto-created from `config.yaml.example` on first run. Defaults: Claude source (empty path), `all-MiniLM-L6-v2` model, `127.0.0.1:8420`, MCP enabled.

**Running processes**: None. No launch.py, mcp_server.py, or cloudflared processes detected.

---

## Appendix: File Tree Reference

```
constellation-v3/
├── CLAUDE.md                    # Agent orientation (21KB)
├── ARCHITECTURE.md              # Architecture overview
├── README.md                    # Project README (no multi-provider info)
├── LICENSE                      # MIT
├── requirements.txt             # 5 packages
├── config.yaml.example          # Template config
├── launch.py                    # Main entry point (12KB)
├── constellation_helper.py      # macOS GUI control panel (34KB)
├── session_summary_2026-03-07.md
├── core/
│   ├── parser.py                # Claude export parser
│   ├── chatgpt_parser.py        # ChatGPT export parser
│   ├── gemini_parser.py         # Gemini export parser
│   ├── grok_parser.py           # Grok stub parser
│   ├── provider_registry.py     # Provider-agnostic registry
│   ├── embedder.py              # sentence-transformers wrapper
│   ├── indexer.py               # KMeans clustering, PCA, graph gen
│   ├── math_utils.py            # Pure numpy math
│   ├── lexical.py               # BM25 inverted index
│   ├── notes.py                 # Sidecar note persistence
│   ├── logger.py                # Structured JSON logging
│   └── config.py                # YAML config management
├── server/
│   ├── api.py                   # SearchEngine class
│   ├── http_server.py           # stdlib HTTP server + REST
│   └── mcp_server.py            # FastMCP stdio server
├── frontend/
│   ├── index.html               # Single page app
│   ├── css/constellation.css    # Dark theme (~787 lines)
│   └── js/
│       ├── app.js               # Orchestrator
│       ├── graph.js             # 3d-force-graph rendering
│       ├── search.js            # Search UI
│       ├── inspector.js         # Conversation detail panel
│       ├── timeline.js          # Brushable timeline
│       └── starfield.js         # Background stars
├── tests/
│   ├── test_constellation.py    # Core tests
│   ├── test_chatgpt_parser.py   # ChatGPT parser tests
│   ├── test_gemini_parser.py    # Gemini parser tests
│   ├── test_multi_provider.py   # Multi-provider integration
│   ├── test_notes.py            # Notes system tests
│   └── test_logger.py           # Logger tests
├── tools/
│   ├── export_fetcher.py        # Export automation
│   └── README.md                # Tools docs
└── sample_data/
    └── dummy_conversations.json # Test fixtures
```
