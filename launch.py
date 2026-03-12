#!/usr/bin/env python3
"""Constellation V3 — Entry point.

Usage:
    python3 launch.py                              # Full: embed (if needed) + serve + open browser
    python3 launch.py --headless                   # REST API only, no browser
    python3 launch.py --reembed                    # Force re-embedding
    python3 launch.py --port 9000                  # Custom port
    python3 launch.py --source /path/to/conv.json  # Skip auto-scan
    python3 launch.py --chatgpt-source /path/to/chatgpt.json  # Add ChatGPT data
    python3 launch.py --add-source chatgpt /path   # Generic multi-provider
"""

import argparse
import json
import os
import sys
import webbrowser
import zipfile

from core.config import CONFIG_PATH, DATA_DIR, ensure_config, ensure_data_dir, save_config


BANNER = """
  \u2726 Constellation V3
"""


def find_source(config: dict, source_arg: str = None) -> str:
    """Locate the Claude export conversations.json file."""
    # 1. Command-line argument
    if source_arg:
        if os.path.isfile(source_arg):
            return source_arg
        print(f"Error: Source file not found: {source_arg}")
        sys.exit(1)

    # 2. Config file
    configured = config.get('source', {}).get('path', '')
    if configured and os.path.isfile(configured):
        return configured

    # 3. Auto-scan Downloads
    from core.parser import find_claude_export
    print("Scanning for Claude export...")
    found = find_claude_export()

    if not found:
        # Also check current directory
        if os.path.isfile('conversations.json'):
            found = [('json', os.path.abspath('conversations.json'))]

    if not found:
        print("\nNo Claude export found in ~/Downloads/")
        print("Please provide the path to your conversations.json:")
        path = input("> ").strip().strip('"').strip("'")
        if os.path.isfile(path):
            return path
        print(f"Error: File not found: {path}")
        sys.exit(1)

    def resolve_source(file_type, path):
        """Extract conversations.json from zip if needed, or return path directly."""
        if file_type == 'zip':
            extract_dir = os.path.join(DATA_DIR, 'extracted')
            os.makedirs(extract_dir, exist_ok=True)
            print(f"Extracting conversations.json from {os.path.basename(path)}...")
            with zipfile.ZipFile(path) as zf:
                zf.extract('conversations.json', extract_dir)
            return os.path.join(extract_dir, 'conversations.json')
        return path

    if len(found) == 1:
        file_type, path = found[0]
        print(f"Found: {path}")
        try:
            confirm = input("Is this the export you want to use? [Y/n] ").strip().lower()
        except EOFError:
            confirm = 'y'
        if confirm in ('', 'y', 'yes'):
            return resolve_source(file_type, path)
        sys.exit(0)

    # Multiple found
    print(f"\nFound {len(found)} Claude exports:")
    for i, (ft, path) in enumerate(found):
        label = f" (zip)" if ft == 'zip' else ""
        print(f"  [{i + 1}] {path}{label}")
    try:
        choice = input(f"Select [1-{len(found)}]: ").strip()
    except EOFError:
        choice = '1'
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(found):
            file_type, path = found[idx]
            return resolve_source(file_type, path)
    except ValueError:
        pass
    print("Invalid selection.")
    sys.exit(1)


def run_pipeline_parse(source_path: str) -> list:
    """Parse the Claude export and return conversation dicts."""
    from core.parser import parse_claude_export
    conversations = parse_claude_export(source_path)
    if not conversations:
        print("No conversations found in export.")
        sys.exit(1)
    return conversations


def run_pipeline_embed(conversations: list, source_path: str, config: dict,
                       force_reembed: bool = False):
    """Run the embedding pipeline on a merged list of conversations."""
    from core.embedder import Embedder
    from core.indexer import (
        embed_conversations, build_clusters, build_edges, build_graph_data, save_pipeline_output
    )
    import numpy as np
    import json as _json

    ensure_data_dir()

    conv_path = os.path.join(DATA_DIR, 'conversations.json')
    emb_path = os.path.join(DATA_DIR, 'embeddings.npy')
    chunk_emb_path = os.path.join(DATA_DIR, 'chunk_embeddings.npy')
    chunk_map_path = os.path.join(DATA_DIR, 'chunk_to_conv.json')

    existing_convs = []
    existing_emb = None
    existing_chunk_emb = None
    existing_chunk_map = []

    use_cache = not force_reembed and os.path.exists(conv_path) and os.path.exists(emb_path)

    if use_cache:
        print("Loading existing index for incremental processing...")
        with open(conv_path, 'r') as f:
            existing_convs = _json.load(f)
        existing_emb = np.load(emb_path)
        if os.path.exists(chunk_emb_path) and os.path.exists(chunk_map_path):
            existing_chunk_emb = np.load(chunk_emb_path)
            with open(chunk_map_path, 'r') as f:
                existing_chunk_map = _json.load(f)

    old_id_to_idx = {c['id']: i for i, c in enumerate(existing_convs)}

    to_embed = []
    for conv in conversations:
        if not use_cache or conv['id'] not in old_id_to_idx:
            to_embed.append(conv)

    if not to_embed and use_cache:
        print("No new conversations found. Core graph remains unchanged.")
        graph_data_path = os.path.join(DATA_DIR, 'graph_data.json')
        if os.path.exists(graph_data_path):
            with open(graph_data_path, 'r') as f:
                return _json.load(f)

    new_emb = None
    new_chunk_emb = None
    new_chunk_map = []
    if to_embed:
        msg_count = sum(len(c['messages']) for c in to_embed)
        print(f"Indexing {len(to_embed)} NEW conversations \u00b7 {msg_count} messages\n")
        embedder = Embedder(config.get('embedding', {}).get('model', 'all-MiniLM-L6-v2'))
        new_emb, new_chunk_emb, new_chunk_map = embed_conversations(to_embed, embedder)

    final_emb = []
    final_chunk_emb = []
    final_chunk_map = []

    new_embed_idx = 0

    old_idx_to_chunks = {}
    if existing_chunk_emb is not None:
        for i, conv_idx in enumerate(existing_chunk_map):
            old_idx_to_chunks.setdefault(conv_idx, []).append(existing_chunk_emb[i])

    new_idx_to_chunks = {}
    if new_chunk_emb is not None:
        for i, conv_idx in enumerate(new_chunk_map):
            new_idx_to_chunks.setdefault(conv_idx, []).append(new_chunk_emb[i])

    for i, conv in enumerate(conversations):
        cid = conv['id']
        if use_cache and cid in old_id_to_idx:
            old_idx = old_id_to_idx[cid]
            final_emb.append(existing_emb[old_idx])

            if existing_chunk_emb is not None and old_idx in old_idx_to_chunks:
                for chunk_vec in old_idx_to_chunks[old_idx]:
                    final_chunk_emb.append(chunk_vec)
                    final_chunk_map.append(i)
            elif existing_chunk_emb is None:
                final_chunk_emb.append(existing_emb[old_idx])
                final_chunk_map.append(i)
        else:
            final_emb.append(new_emb[new_embed_idx])

            if new_chunk_emb is not None and new_embed_idx in new_idx_to_chunks:
                for chunk_vec in new_idx_to_chunks[new_embed_idx]:
                    final_chunk_emb.append(chunk_vec)
                    final_chunk_map.append(i)
            elif new_chunk_emb is None:
                final_chunk_emb.append(new_emb[new_embed_idx])
                final_chunk_map.append(i)

            new_embed_idx += 1

    embeddings = np.array(final_emb)
    chunk_embeddings = np.array(final_chunk_emb) if final_chunk_emb else None

    print("Re-clustering and caching final graph...")
    cluster_info = build_clusters(embeddings)
    edges = build_edges(embeddings, conversations)
    graph_data = build_graph_data(conversations, embeddings, cluster_info, edges)

    save_pipeline_output(conversations, embeddings, chunk_embeddings, final_chunk_map, graph_data, DATA_DIR)
    config['source']['path'] = os.path.abspath(source_path)
    save_config(config)

    print(f"\n\u2726 Pipeline complete")
    print(f"  {graph_data['stats']['clusterCount']} clusters")
    print(f"  {graph_data['stats']['edgeCount']} edges")

    return graph_data


def start_server(config: dict, headless: bool = False):
    """Start the HTTP server."""
    from server.http_server import run_server

    host = config.get('server', {}).get('host', '127.0.0.1')
    port = config.get('server', {}).get('port', 8420)

    url = f"http://{host}:{port}"
    print(f"\n\u2726 Memory server ready")

    if not headless:
        print(f"\u2726 Visualization: {url}")

    # Print MCP config snippet (uses absolute path for reliability)
    project_dir = os.path.dirname(os.path.abspath(__file__))
    mcp_config = {
        "mcpServers": {
            "constellation": {
                "command": sys.executable,
                "args": [os.path.join(project_dir, "server", "mcp_server.py")]
            }
        }
    }
    print(f"\nMCP config for Claude Desktop:")
    print(json.dumps(mcp_config, indent=2))

    if not headless:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    run_server(host, port, headless)


def main():
    parser = argparse.ArgumentParser(description='Constellation V3')
    parser.add_argument('--headless', action='store_true',
                        help='REST API only, no browser')
    parser.add_argument('--reembed', action='store_true',
                        help='Force re-embedding')
    parser.add_argument('--port', type=int, help='Custom port')
    parser.add_argument('--source', type=str,
                        help='Path to conversations.json')
    parser.add_argument('--chatgpt-source', type=str,
                        help='Path to ChatGPT conversations.json export')
    parser.add_argument('--add-source', nargs=2, action='append',
                        metavar=('PROVIDER', 'PATH'),
                        help='Add a data source: --add-source chatgpt /path/to/file.json')
    args = parser.parse_args()

    print(BANNER)

    config = ensure_config()

    if args.port:
        config['server']['port'] = args.port

    source_path = find_source(config, args.source)
    conversations = run_pipeline_parse(source_path)

    # Collect additional sources from --chatgpt-source and --add-source flags
    extra_sources = {}

    # --chatgpt-source is a shortcut for --add-source chatgpt <path>
    if args.chatgpt_source:
        extra_sources['chatgpt'] = args.chatgpt_source

    # --add-source entries
    if args.add_source:
        for provider, path in args.add_source:
            extra_sources[provider] = path

    # Also check config for saved sources
    saved_sources = config.get('sources', {})
    for provider, source_info in saved_sources.items():
        if provider == 'claude':
            continue  # already handled by find_source
        if provider not in extra_sources and isinstance(source_info, dict):
            path = source_info.get('path', '')
            if path and os.path.exists(path):
                extra_sources[provider] = path

    # Parse additional sources
    for provider, path in extra_sources.items():
        if not os.path.exists(path):
            print(f"Warning: {provider} source not found: {path}")
            continue

        if provider == 'chatgpt':
            from core.chatgpt_parser import parse_chatgpt_export
            extra_convs = parse_chatgpt_export(path)
        elif provider == 'gemini':
            try:
                from core.gemini_parser import parse_gemini_export
                extra_convs = parse_gemini_export(path)
            except ImportError:
                print(f"Warning: Gemini parser not available, skipping")
                continue
        elif provider == 'grok':
            try:
                from core.grok_parser import parse_grok_export
                extra_convs = parse_grok_export(path)
            except ImportError:
                print(f"Warning: Grok parser not available, skipping")
                continue
        else:
            print(f"Warning: Unknown provider '{provider}', skipping")
            continue

        print(f"Adding {len(extra_convs)} {provider} conversations to index")
        conversations.extend(extra_convs)

        # Save source path to config
        if 'sources' not in config:
            config['sources'] = {}
        config['sources'][provider] = {'path': os.path.abspath(path)}

    run_pipeline_embed(conversations, source_path, config, force_reembed=args.reembed)

    start_server(config, args.headless)


if __name__ == '__main__':
    main()
