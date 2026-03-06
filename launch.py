#!/usr/bin/env python3
"""Constellation V3 — Entry point.

Usage:
    python3 launch.py                              # Full: embed (if needed) + serve + open browser
    python3 launch.py --headless                   # REST API only, no browser
    python3 launch.py --reembed                    # Force re-embedding
    python3 launch.py --port 9000                  # Custom port
    python3 launch.py --source /path/to/conv.json  # Skip auto-scan
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


def run_pipeline(source_path: str, config: dict):
    """Run the full embedding pipeline."""
    from core.parser import parse_claude_export
    from core.embedder import Embedder
    from core.indexer import (
        embed_conversations,
        build_clusters,
        build_edges,
        build_graph_data,
        save_pipeline_output,
    )

    ensure_data_dir()

    # Parse
    conversations = parse_claude_export(source_path)
    if not conversations:
        print("No conversations found in export.")
        sys.exit(1)

    msg_count = sum(len(c['messages']) for c in conversations)
    print(f"{len(conversations)} conversations \u00b7 {msg_count} messages\n")

    # Embed
    embedder = Embedder(config.get('embedding', {}).get('model', 'all-MiniLM-L6-v2'))
    embeddings = embed_conversations(conversations, embedder)

    # Cluster
    cluster_info = build_clusters(embeddings)

    # Edges
    edges = build_edges(embeddings, conversations)

    # Build graph data
    graph_data = build_graph_data(conversations, embeddings, cluster_info, edges)

    # Save
    save_pipeline_output(conversations, embeddings, graph_data, DATA_DIR)

    # Update config with source path
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
    args = parser.parse_args()

    print(BANNER)

    config = ensure_config()

    if args.port:
        config['server']['port'] = args.port

    # Check for cached data
    embeddings_path = os.path.join(DATA_DIR, 'embeddings.npy')
    graph_data_path = os.path.join(DATA_DIR, 'graph_data.json')

    need_pipeline = args.reembed or not os.path.exists(embeddings_path) \
        or not os.path.exists(graph_data_path)

    if need_pipeline:
        source_path = find_source(config, args.source)
        run_pipeline(source_path, config)
    else:
        print("Using cached embeddings and graph data.")

    start_server(config, args.headless)


if __name__ == '__main__':
    main()
