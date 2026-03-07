"""HTTP server for Constellation V3.

Serves static frontend files + REST API.
"""

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import numpy as np

from core.config import DATA_DIR, PROJECT_ROOT
from server.api import SearchEngine

FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')

search_engine = SearchEngine()


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
                result = search_engine.list_conversations(offset=offset, limit=limit, sort_by=sort_by)
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

        self.send_error(404)

    def handle_search(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            query = data.get('query', '')
            top_k = data.get('top_k', 5)
            results = search_engine.search(query, top_k)
            self.send_json(results)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

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
            print("Warning: Data files not found. Run pipeline first.")

    server = HTTPServer((host, port), ConstellationHandler)
    print(f"Server running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
