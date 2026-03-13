"""HTTP server for Constellation.

Serves static frontend files + REST API.
"""

import json
import os
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import numpy as np

from core.config import DATA_DIR, PROJECT_ROOT
from server.api import SearchEngine

FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')
MAX_REQUEST_BODY = 10 * 1024 * 1024  # 10MB

search_engine = SearchEngine()
_server_start_time = time.time()


class ConstellationHandler(SimpleHTTPRequestHandler):
    """Handler for static files and API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def end_headers(self):
        # Prevent aggressive browser caching of all responses (dev server)
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # API endpoints
        if path.startswith('/api/'):
            return self.handle_api_get(path, parsed)

        # Serve graph data from data directory
        if path == '/data/graph_data.json':
            return self.serve_file(
                os.path.join(DATA_DIR, 'graph_data.json'),
                'application/json'
            )

        # Static files
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/search':
            return self.handle_search()

        self.send_error(404)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Accept, Mcp-Session-Id')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # DELETE /api/conversation/<id>/notes/<note_id>
        if '/notes/' in path and path.startswith('/api/conversation/'):
            parts = path.split('/')
            # ['', 'api', 'conversation', '<id>', 'notes', '<note_id>']
            if len(parts) == 6:
                conv_id = parts[3]
                note_id = parts[5]
                try:
                    result = search_engine.delete_note(conv_id, note_id)
                    self.send_json(result)
                except Exception as e:
                    self.send_json({'error': str(e)}, 500)
                return

        self.send_error(404)

    def handle_api_get(self, path, parsed):
        query_params = parse_qs(parsed.query)

        if path.startswith('/api/conversation/'):
            conv_id = path.split('/api/conversation/')[1]
            try:
                result = search_engine.get_conversation(conv_id)
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/api/conversations':
            try:
                offset = int(query_params.get('offset', [0])[0])
                limit = int(query_params.get('limit', [20])[0])
                sort_by = query_params.get('sort_by', ['date'])[0]
                provider = query_params.get('provider', [None])[0]
                result = search_engine.list_conversations(offset=offset, limit=limit,
                                                          sort_by=sort_by, provider=provider)
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/api/stats':
            try:
                result = search_engine.get_stats()
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/api/recluster':
            k = int(query_params.get('k', [11])[0])
            try:
                result = self.handle_recluster(k)
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/api/server-info':
            try:
                result = self.handle_server_info()
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/api/logs':
            try:
                n = int(query_params.get('n', [50])[0])
                level = query_params.get('level', [None])[0]
                result = self.handle_logs(n, level)
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        self.send_error(404)

    def handle_search(self):
        content_length_str = self.headers.get('Content-Length')
        if content_length_str is None:
            self.send_error(411, 'Length Required')
            return
        try:
            content_length = int(content_length_str)
        except (ValueError, TypeError):
            self.send_error(400, 'Invalid Content-Length')
            return
        if content_length > MAX_REQUEST_BODY:
            self.send_error(413, 'Payload Too Large')
            return
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            query = data.get('query', '')
            top_k = data.get('top_k', 5)
            provider = data.get('provider', None)
            results = search_engine.search(query, top_k, provider=provider)
            self.send_json(results)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def handle_server_info(self):
        """Return server status, uptime, PID, ports, data source info."""
        elapsed = time.time() - _server_start_time
        hours = int(elapsed // 3600)
        mins = int((elapsed % 3600) // 60)

        stats = search_engine.get_stats() if search_engine._loaded else {}

        from core.config import ensure_config
        config = ensure_config()
        sources = {}
        claude_path = config.get('source', {}).get('path', '')
        if claude_path:
            sources['claude'] = claude_path
        for provider, info in config.get('sources', {}).items():
            if isinstance(info, dict):
                sources[provider] = info.get('path', '')

        return {
            'pid': os.getpid(),
            'uptime_seconds': elapsed,
            'uptime_formatted': f'{hours}h {mins:02d}m',
            'port': config.get('server', {}).get('port', 8420),
            'total_conversations': stats.get('totalConversations', 0),
            'total_messages': stats.get('totalMessages', 0),
            'providers': stats.get('providers', {}),
            'date_range': stats.get('dateRange', ['', '']),
            'embedding_model': stats.get('embeddingModel', ''),
            'data_sources': sources,
            'log_file': os.path.join(DATA_DIR, 'logs', 'constellation.log'),
        }

    def handle_logs(self, n=50, level=None):
        """Return last N log entries from the JSON log file."""
        log_path = os.path.join(DATA_DIR, 'logs', 'constellation.log')
        if not os.path.exists(log_path):
            return {'entries': [], 'total': 0}

        entries = []
        try:
            with open(log_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if level and entry.get('level', '') != level:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        entries.append({'message': line, 'level': 'RAW'})
        except Exception:
            pass

        # Return last N entries
        return {'entries': entries[-n:], 'total': len(entries)}

    def handle_recluster(self, k):
        """Re-cluster with a new K value and return updated graph data."""
        from core.indexer import (
            build_clusters, build_edges, build_graph_data, save_pipeline_output
        )

        search_engine.load()
        embeddings = search_engine.embeddings
        conversations = search_engine.conversations

        # Re-cluster
        cluster_info = build_clusters(embeddings, k_override=k)
        edges = build_edges(embeddings, conversations)
        graph_data = build_graph_data(conversations, embeddings, cluster_info, edges)

        # Save updated graph data
        graph_path = os.path.join(DATA_DIR, 'graph_data.json')
        with open(graph_path, 'w') as f:
            json.dump(graph_data, f)

        # Save clusters.json for consistency
        clusters_path = os.path.join(DATA_DIR, 'clusters.json')
        with open(clusters_path, 'w') as f:
            json.dump({'k': k, 'clusters': graph_data.get('clusters', [])}, f, indent=2)

        return graph_data

    def send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, filepath, content_type):
        if not os.path.exists(filepath):
            self.send_error(404)
            return
        with open(filepath, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(data))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        # Suppress default logging noise
        pass


def run_server(host='127.0.0.1', port=8420, headless=False):
    """Start the HTTP server."""
    # Pre-load search engine
    try:
        search_engine.load()
    except FileNotFoundError:
        if not headless:
            print("Warning: Data files not found. Run pipeline first.", file=sys.stderr)

    server = HTTPServer((host, port), ConstellationHandler)
    print(f"Server running at http://{host}:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", file=sys.stderr)
        server.shutdown()
