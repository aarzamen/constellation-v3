# C✦nstellation

**A self-hosted semantic memory server for your AI conversation history.**

Drop in a Claude export, and Constellation gives you a queryable memory bank that any LLM can search — plus a 3D visualization of everything you've discussed.

![Constellation V3](screenshots/hero.png)

## Quick Start

```bash
git clone https://github.com/<user>/constellation.git
cd constellation
python3 launch.py
```

On first run, Constellation will:
1. Scan your `~/Downloads` for a Claude export
2. Embed all conversations locally (~90 seconds, no API key needed)
3. Open an interactive 3D visualization at `http://localhost:8420`

## Features

- **Semantic Memory Server** — MCP-compatible memory bank. Claude Desktop, Claude Code, or any MCP client can query your full conversation history. Includes explicit write-capabilities (`add_conversation_note`).
- **Hybrid Exact + Semantic Search** — Native Reciprocal Rank Fusion (RRF) combines `sentence-transformers` vector search with a pure-Python BM25 lexical index for perfect keyword recall.
- **REST API** — Same hybrid search capability for non-MCP clients.
- **3D Visualization** — Interactive LOD (Level of Detail) force-directed graph where conversations are stars and clusters are constellations. Built for massive scale (>5,000 nodes).
- **Resumable Indexing** — Fast, incremental data processing. Only new exports are tokenized and chunked, caching state across runs.
- **Fully Local** — No API keys, no external vector DBs, no heavy frameworks (LangChain/LlamaIndex). Embedding runs entirely locally via CPU.
- **Headless Mode** — Run as a pure memory server with `--headless`.

## Usage

```bash
python3 launch.py                              # Full mode: embed + visualize
python3 launch.py --headless                   # Memory server only, no browser
python3 launch.py --reembed                    # Force re-embedding
python3 launch.py --port 9000                  # Custom port
python3 launch.py --source /path/to/conv.json  # Specify export path
```

## MCP Integration

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "constellation": {
      "command": "python3",
      "args": ["-m", "server.mcp_server"],
      "cwd": "/path/to/constellation"
    }
  }
}
```

Then ask Claude: *"What was that conversation I had about cardiac emergency protocols?"* or *"Please tag conversation [uuid] with the note 'Requires follow-up'."*

## REST API

```bash
# Hybrid search
curl -X POST http://localhost:8420/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "cardiac emergency protocol", "top_k": 5}'

# Get full conversation
curl http://localhost:8420/api/conversation/<uuid>

# Index stats
curl http://localhost:8420/api/stats
```

## Multi-Provider Support

Constellation indexes conversations from multiple AI providers in a unified
semantic space. Conversations cluster by topic, not by provider.

Supported providers:
- **Claude** (Anthropic) — sphere nodes
- **ChatGPT** (OpenAI) — octahedron nodes
- **Gemini** (Google) — dodecahedron nodes
- **Grok** (xAI) — icosahedron nodes (stub parser)

See [DEPLOY.md](DEPLOY.md) for ingestion commands.

## Requirements

- Python 3.9+
- ~80MB disk for embedding model (downloaded once, cached)
- A Claude or ChatGPT export (see DEPLOY.md for all formats)

```bash
pip install -r requirements.txt
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for technical details.

## License

MIT — see [LICENSE](LICENSE).
