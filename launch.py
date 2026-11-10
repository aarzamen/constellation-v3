#!/usr/bin/env python3
"""Constellation — Entry point.

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
  \u2726 Constellation
"""


def find_source(config: dict, source_arg: str = None, headless: bool = False) -> str:
    """Locate the Claude export conversations.json file.

    When headless=True, never call input() — auto-select or exit with error.
    """
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
        if headless:
            print("Error: No Claude export found and no source configured.")
            print("Run interactively first, or use --source /path/to/conversations.json")
            sys.exit(1)
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
        if headless:
            # Auto-select the only export found
            return resolve_source(file_type, path)
        try:
            confirm = input("Is this the export you want to use? [Y/n] ").strip().lower()
        except EOFError:
            confirm = 'y'
        if confirm in ('', 'y', 'yes'):
            return resolve_source(file_type, path)
        sys.exit(0)

    # Multiple found
    if headless:
        # Auto-select the first (most recent) export
        file_type, path = found[0]
        print(f"Auto-selecting: {path}")
        return resolve_source(file_type, path)

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


def _progress_line(stage, done, total, detail=''):
    """Print a PROGRESS protocol line for subprocess communication.

    Format: PROGRESS:<stage>:<done>:<total>:<detail>
    The helper reads these lines from stdout to update the progress panel.
    """
    print(f"PROGRESS:{stage}:{done}:{total}:{detail}", flush=True)


def run_pipeline_embed(conversations: list, source_path: str, config: dict,
                       force_reembed: bool = False):
    """Reconcile and publish a merged conversation list via the shared ingest.

    Thin wrapper over core.ingest.ingest_conversations, which loads the
    current generation, reconciles the FULL retained corpus with the incoming
    export (existing records are never dropped, even with force_reembed or
    when a source omits them), reuses cached vectors when the embedding
    identity and inputs match, and publishes a new generation with
    compare-and-swap. Legacy note migration and the pre-rebuild notes backup
    happen once inside the shared ingest.
    """
    from core.embedder import Embedder
    from core.ingest import ingest_conversations

    ensure_data_dir()
    _progress_line('init', 0, len(conversations),
                   f'{len(conversations)} conversations')

    embedder = Embedder(config.get('embedding', {}).get('model',
                                                        'all-MiniLM-L6-v2'))

    def on_embed_progress(done, total):
        _progress_line('embedding', done, total)

    result = ingest_conversations(DATA_DIR, conversations, embedder,
                                  force_reembed=force_reembed,
                                  progress_callback=on_embed_progress)

    config['source']['path'] = os.path.abspath(source_path)
    save_config(config)

    graph_data = result.snapshot.graph_data
    stats = graph_data.get('stats', {}) if isinstance(graph_data, dict) else {}
    cluster_count = stats.get('clusterCount', len(graph_data.get('clusters', [])))
    edge_count = stats.get('edgeCount', len(graph_data.get('edges', [])))
    _progress_line('complete', result.after, result.after,
                   f"{cluster_count} clusters, {edge_count} edges")
    print(f"\n\u2726 Pipeline complete")
    print(f"  {result.after} conversations ({result.embedded} embedded)")
    print(f"  {cluster_count} clusters")
    print(f"  {edge_count} edges")

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
    parser = argparse.ArgumentParser(description='Constellation')
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

    source_path = find_source(config, args.source, headless=args.headless)
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

    # Parse additional sources using provider registry
    from core.provider_registry import get_parser, list_providers
    for provider, path in extra_sources.items():
        if not os.path.exists(path):
            print(f"Warning: {provider} source not found: {path}")
            continue

        try:
            parser_fn = get_parser(provider)
        except ValueError:
            print(f"Warning: Unknown provider '{provider}'. "
                  f"Available: {', '.join(list_providers())}")
            continue

        extra_convs = parser_fn(path)
        print(f"Adding {len(extra_convs)} {provider} conversations to index")
        conversations.extend(extra_convs)

        # Save source path to config
        if 'sources' not in config:
            config['sources'] = {}
        source_entry = {'path': os.path.abspath(path)}
        if os.path.isdir(path):
            source_entry['type'] = 'directory'
        config['sources'][provider] = source_entry

    run_pipeline_embed(conversations, source_path, config, force_reembed=args.reembed)

    start_server(config, args.headless)


if __name__ == '__main__':
    main()
