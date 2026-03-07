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
    Extract conversations, separate user messages, recurvsive chunking
        │
        ▼
    Embedder (core/embedder.py)
    all-MiniLM-L6-v2, local, 384 dimensions
    Embed individual text chunks (resumable indexing)
        │
        ▼
    Indexer (core/indexer.py)
    ├── Auto-cluster (spherical k-means, silhouette sweep K=5..20)
    ├── Build edges (cosine similarity, percentile threshold)
    ├── PCA 3D projection (numpy SVD)
    └── Generate graph_data.json
        │
        ▼
    REST API & Hybrid Search (server/api.py)
    ├── Semantic Similarity (Cosine on chunks)
    ├── Lexical BM25 Search (core/lexical.py)
    └── Reciprocal Rank Fusion (RRF)
        │
        ▼
    data/
    ├── embeddings.npy        # Numpy binary of chunk vectors
    ├── chunk_to_conv.json    # Map between chunks and source conversations
    ├── conversations.json    # Parsed conversation text + metadata
    ├── clusters.json         # Cluster assignments and labels
    └── graph_data.json       # Frontend-ready graph structure
```

## Key Design Decisions

### No External Vector DBs or LangChain
KMeans, PCA, cosine similarity, BM25 indices, and text chunking are implemented in pure Python/Numpy. The architecture prioritizes lightweight, self-contained mechanisms over heavy frameworks. 

### Chunk-Level Embeddings (V4)
To ensure reliable recall across massive transcripts, user messages are recursively chunked via paragraph splits. Embeddings are stored per chunk, and search results trace chunks back to their parent conversation identity.

### Hybrid Exact + Semantic Search (V4)
Search requests concurrently map against a native inverted BM25 index (for strict exact-match keywords) and the dense vector arrays. The two lists are merged via Reciprocal Rank Fusion (RRF).

### Resumable Indexing (V4)
The pipeline automatically parses the `conversations.json` against the cached `embeddings.npy`, intelligently skipping previously processed exports and significantly accelerating iterative updates.

### LOD (Level of Detail) & Massive Scale (V4)
To maintain 60 FPS in browsers exceeding 5,000 nodes, `3d-force-graph` is augmented with `THREE.LOD`. Distant elements degrade gracefully to low-poly basic meshes, and visual noise (edges/particles) is dynamically suppressed outside the active focus state.

### Conservative MCP Writes
The MCP server operates natively over `stdout`. Read operations (semantic search, exact fetch) are cleanly isolated. Write operations (`add_conversation_note`) explicitly mutate local flat-file JSON metadata without destructively triggering global vector re-clustering.

## Directory Structure

```
constellation/
├── launch.py              # Entry point
├── core/
│   ├── parser.py          # Claude export → unified format & chunking
│   ├── lexical.py         # Native BM25 inverted index
│   ├── embedder.py        # sentence-transformers wrapper
│   ├── indexer.py         # Clustering, edges, incremental graph output
│   ├── math_utils.py      # Pure numpy: kmeans, pca, cosine
│   └── config.py          # Settings (YAML)
├── server/
│   ├── http_server.py     # Static files + REST API
│   ├── mcp_server.py      # MCP endpoint (standalone)
│   └── api.py             # Shared hybrid search & RRF logic
├── frontend/
│   ├── index.html
│   ├── css/constellation.css
│   └── js/
│       ├── app.js         # Orchestrator & Clipboard APIs
│       ├── graph.js       # 3d-force-graph & THREE.LOD wrapper
│       ├── starfield.js   # Background renderer
│       ├── timeline.js    # Brushable timeline
│       ├── inspector.js   # Conversation viewer (query highlighting)
│       └── search.js      # Hybrid search interface
└── data/                  # Generated at runtime (gitignored)
```

## Dependencies

**Python**: numpy, sentence-transformers, pyyaml, fastmcp
**Frontend**: 3d-force-graph (CDN), Google Fonts (Poppins, Lora)
