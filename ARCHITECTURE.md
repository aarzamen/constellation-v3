# Constellation V3 — Architecture

## Overview

Constellation is a single-process application with two independent server interfaces:

1. **HTTP Server** — Serves the 3D visualization frontend and REST API
2. **MCP Server** — Standalone subprocess for LLM memory queries via stdio

Both read from the same `data/` directory on disk. They are independent — killing the web server doesn't affect MCP queries.

## Data Pipeline

```
Claude Export (conversations.json)
        │
        ▼
    Parser (core/parser.py)
    Extract conversations, separate user messages
        │
        ▼
    Embedder (core/embedder.py)
    all-MiniLM-L6-v2, local, 384 dimensions
    Embed each user message individually
        │
        ▼
    Mean Pooling
    Average message vectors per conversation
    L2-normalize the result
        │
        ▼
    Indexer (core/indexer.py)
    ├── Auto-cluster (spherical k-means, silhouette sweep K=5..20)
    ├── Build edges (cosine similarity, percentile threshold)
    ├── PCA 3D projection (numpy SVD)
    └── Generate graph_data.json for frontend
        │
        ▼
    data/
    ├── embeddings.npy        # Numpy binary, ~1.5MB for 1K conversations
    ├── conversations.json    # Parsed conversation text + metadata
    ├── clusters.json         # Cluster assignments and labels
    └── graph_data.json       # Frontend-ready graph structure
```

## Key Design Decisions

### No sklearn
KMeans, PCA, cosine similarity, and silhouette scoring are implemented in pure numpy (~80 lines in `core/math_utils.py`). At 1,000 conversations × 384 dimensions, the entire index fits in under 10MB of RAM.

### Embed User Messages Only
Assistant messages contain too much shared boilerplate. User messages carry the topical signal. Each conversation is represented by the mean of its user message embeddings.

### Percentile-Based Edges
Edges connect conversations whose cosine similarity falls in the top percentile (default 95th). This auto-scales regardless of corpus size.

### Independent Servers
The MCP server loads its own copy of embeddings from disk (~1.5MB, instant). It doesn't depend on the HTTP server. Startup cost is ~1-2 seconds.

## Directory Structure

```
constellation/
├── launch.py              # Entry point
├── core/
│   ├── parser.py          # Claude export → unified format
│   ├── embedder.py        # sentence-transformers wrapper
│   ├── indexer.py         # Clustering, edges, graph output
│   ├── math_utils.py      # Pure numpy: kmeans, pca, cosine
│   └── config.py          # Settings (YAML)
├── server/
│   ├── http_server.py     # Static files + REST API
│   ├── mcp_server.py      # MCP endpoint (standalone)
│   └── api.py             # Shared search logic
├── frontend/
│   ├── index.html
│   ├── css/constellation.css
│   └── js/
│       ├── app.js         # Orchestrator
│       ├── graph.js       # 3d-force-graph wrapper
│       ├── starfield.js   # Background renderer
│       ├── timeline.js    # Brushable timeline
│       ├── inspector.js   # Conversation viewer
│       └── search.js      # Search interface
└── data/                  # Generated at runtime (gitignored)
```

## Dependencies

**Python**: numpy, sentence-transformers, pyyaml, fastmcp
**Frontend**: 3d-force-graph (CDN), Google Fonts (Poppins, Lora)
