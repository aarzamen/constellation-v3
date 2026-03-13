"""REST search endpoints for Constellation.

Shared logic between MCP server and HTTP API.
"""

import json
import os
import sys
import time

import numpy as np

from core.config import DATA_DIR
from core.logger import get_logger
from core.math_utils import cosine_similarity_query
from core.notes import load_notes, append_note, delete_note as _delete_note, get_notes_for_conversation

logger = get_logger(__name__)


class SearchEngine:
    """Manages embeddings and conversation index for search."""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or DATA_DIR
        self.embeddings = None
        self.chunk_embeddings = None
        self.chunk_to_conv = None
        self.chunk_to_local_idx = []
        self.conversations = None
        self.conversation_index = {}
        self.embedder = None
        self._loaded = False

    def load(self):
        """Load embeddings and conversation data from disk."""
        if self._loaded:
            return

        emb_path = os.path.join(self.data_dir, 'embeddings.npy')
        conv_path = os.path.join(self.data_dir, 'conversations.json')

        if not os.path.exists(emb_path) or not os.path.exists(conv_path):
            raise FileNotFoundError(
                f"Data files not found in {self.data_dir}. "
                "Run the embedding pipeline first."
            )

        self.embeddings = np.load(emb_path)
        with open(conv_path, 'r') as f:
            self.conversations = json.load(f)

        self.conversation_index = {c['id']: c for c in self.conversations}
        
        # Build lexical index
        from core.lexical import BM25Index
        self.bm25 = BM25Index()
        docs = []
        for c in self.conversations:
            doc_text = c.get('name', '') + ' ' + ' '.join(m.get('text', '') for m in c.get('messages', []))
            docs.append(doc_text)
        self.bm25.build(docs)

        # Load chunk-level data if present (V4 enhancement)
        # Note: 'chunks' here refers to individual user messages parsed by indexer.py
        chunk_emb_path = os.path.join(self.data_dir, 'chunk_embeddings.npy')
        chunk_map_path = os.path.join(self.data_dir, 'chunk_to_conv.json')
        
        if os.path.exists(chunk_emb_path) and os.path.exists(chunk_map_path):
            self.chunk_embeddings = np.load(chunk_emb_path)
            with open(chunk_map_path, 'r') as f:
                self.chunk_to_conv = json.load(f)
                
            # Precompute an array linking each chunk vector to its local index inside the parent's `user_messages`
            import collections
            counts = collections.defaultdict(int)
            for c_idx in self.chunk_to_conv:
                self.chunk_to_local_idx.append(counts[c_idx])
                counts[c_idx] += 1

        # Load sidecar notes
        self.notes = load_notes(self.data_dir)

        # Load cluster metadata
        self.cluster_meta = {}
        clusters_path = os.path.join(self.data_dir, 'clusters.json')
        if os.path.exists(clusters_path):
            try:
                with open(clusters_path, 'r') as f:
                    clusters_data = json.load(f)
                for cl in clusters_data.get('clusters', []):
                    self.cluster_meta[cl['id']] = cl
            except (json.JSONDecodeError, KeyError):
                pass

        # Build conversation-to-cluster mapping from graph_data
        self.conv_cluster = {}
        graph_path = os.path.join(self.data_dir, 'graph_data.json')
        if os.path.exists(graph_path):
            try:
                with open(graph_path, 'r') as f:
                    graph_data = json.load(f)
                for node in graph_data.get('nodes', []):
                    self.conv_cluster[node['id']] = {
                        'cluster_id': node.get('cluster', 0),
                        'cluster_label': node.get('clusterLabel', ''),
                    }
            except (json.JSONDecodeError, KeyError):
                pass

        self._loaded = True
        logger.info("Index loaded", extra={
            'conversations': len(self.conversations),
            'messages': sum(len(c.get('messages', [])) for c in self.conversations),
        })

    def _ensure_embedder(self):
        """Lazy-load the embedding model for query embedding."""
        if self.embedder is None:
            from core.embedder import Embedder
            self.embedder = Embedder()

    def search(self, query: str, top_k: int = 5, provider: str = None) -> list:
        """Hybrid exact + semantic search over conversation history.

        Args:
            query: Search query text.
            top_k: Number of results to return.
            provider: Optional filter — 'claude', 'chatgpt', or None for all.
        """
        if not query or not query.strip():
            return []

        try:
            top_k = int(top_k)
            if top_k < 1: top_k = 5
        except (ValueError, TypeError):
            top_k = 5

        self.load()
        self._ensure_embedder()

        t0 = time.time()
        query_embedding = self.embedder.embed_query(query)
        semantic_scores = cosine_similarity_query(query_embedding, self.embeddings)
        lexical_scores = self.bm25.get_scores(query)

        # Compute chunk-level (message-level) similarities
        has_chunks = self.chunk_embeddings is not None
        conv_best_chunk_score = np.zeros(len(self.conversations))
        conv_best_chunk_idx = np.zeros(len(self.conversations), dtype=int) - 1
        
        if has_chunks:
            chunk_scores = cosine_similarity_query(query_embedding, self.chunk_embeddings)
            for i, (score, c_idx) in enumerate(zip(chunk_scores, self.chunk_to_conv)):
                if conv_best_chunk_idx[c_idx] == -1 or score > conv_best_chunk_score[c_idx]:
                    conv_best_chunk_score[c_idx] = score
                    conv_best_chunk_idx[c_idx] = i

        # Reciprocal Rank Fusion (RRF) combining conversation semantic, chunk semantic, and lexical
        sem_ranks = np.argsort(semantic_scores)[::-1]
        lex_ranks = np.argsort(lexical_scores)[::-1]
        
        rrf_scores = np.zeros(len(self.conversations))
        k_rrf = 60

        for rank, idx in enumerate(sem_ranks):
            rrf_scores[idx] += 1.0 / (k_rrf + rank + 1)

        for rank, idx in enumerate(lex_ranks):
            if lexical_scores[idx] > 0:
                rrf_scores[idx] += 1.0 / (k_rrf + rank + 1)

        if has_chunks:
            chunk_ranks = np.argsort(conv_best_chunk_score)[::-1]
            for rank, idx in enumerate(chunk_ranks):
                if conv_best_chunk_score[idx] > 0:
                    rrf_scores[idx] += 1.0 / (k_rrf + rank + 1)

        # Fetch more results if filtering by provider to compensate
        fetch_k = top_k * 3 if provider else top_k
        top_indices = np.argsort(rrf_scores)[::-1][:fetch_k]

        results = []
        for idx in top_indices:
            conv = self.conversations[idx]
            
            # Smart excerpt generation - pull the precise matching chunk/message text
            excerpt = ""
            if has_chunks and conv_best_chunk_idx[idx] != -1:
                chunk_id = conv_best_chunk_idx[idx]
                local_msg_idx = self.chunk_to_local_idx[chunk_id]
                
                # Reconstruct user_messages sequence on the fly
                user_msgs = [m['text'] for m in conv.get('messages', []) if m.get('role') == 'user' and m.get('text', '').strip()]
                if not user_msgs:
                    user_msgs = [conv.get('name', '')]
                    
                if local_msg_idx < len(user_msgs):
                    excerpt = user_msgs[local_msg_idx][:800]
                    
            # Fallback if chunks are missing
            if not excerpt and conv.get('messages'):
                excerpt = conv['messages'][0]['text'][:800]

            cluster_info = self.conv_cluster.get(conv['id'], {})
            conv_notes = self.notes.get(conv['id'], [])
            results.append({
                'id': conv['id'],
                'title': conv['name'],
                'date': conv.get('created_at', ''),
                'provider': conv.get('provider', 'claude'),
                'score': float(rrf_scores[idx]),
                'conversation_score': float(semantic_scores[idx]),
                'chunk_score': float(conv_best_chunk_score[idx]) if has_chunks else 0.0,
                'lexical_score': float(lexical_scores[idx]),
                'message_count': len(conv.get('messages', [])),
                'excerpt': excerpt + ('...' if len(excerpt) == 800 else ''),
                'cluster_id': cluster_info.get('cluster_id', 0),
                'cluster_label': cluster_info.get('cluster_label', 'Unknown'),
                'notes_count': len(conv_notes),
            })

        if provider:
            results = [r for r in results if r.get('provider') == provider]

        results = results[:top_k]
        duration_ms = (time.time() - t0) * 1000
        logger.info("Search completed", extra={
            'query': query[:100], 'top_k': top_k, 'results': len(results),
            'duration_ms': round(duration_ms, 1), 'provider': provider,
        })
        return results

    def get_conversation(self, conversation_id: str) -> dict:
        """Retrieve full conversation by ID."""
        if not conversation_id or not str(conversation_id).strip():
            return {'error': 'Invalid conversation ID'}
            
        self.load()
        conv = self.conversation_index.get(conversation_id)
        if not conv:
            return {'error': 'Conversation not found'}
        cluster_info = self.conv_cluster.get(conversation_id, {})
        return {
            'id': conv['id'],
            'title': conv['name'],
            'date': conv.get('created_at', ''),
            'provider': conv.get('provider', 'claude'),
            'messages': [
                {
                    'role': m['role'],
                    'text': m['text'],
                    'timestamp': m.get('created_at', ''),
                }
                for m in conv.get('messages', [])
            ],
            'notes': get_notes_for_conversation(self.data_dir, conversation_id),
            'cluster_id': cluster_info.get('cluster_id', 0),
            'cluster_label': cluster_info.get('cluster_label', 'Unknown'),
        }

    def get_stats(self) -> dict:
        """Return index statistics."""
        self.load()
        total_messages = sum(len(c.get('messages', [])) for c in self.conversations)
        dates = []
        for c in self.conversations:
            ca = c.get('created_at', '')
            if ca:
                dates.append(ca[:10])

        provider_counts = {}
        for c in self.conversations:
            p = c.get('provider', 'claude')
            provider_counts[p] = provider_counts.get(p, 0) + 1

        return {
            'totalConversations': len(self.conversations),
            'totalMessages': total_messages,
            'dateRange': [min(dates), max(dates)] if dates else ['', ''],
            'embeddingModel': 'all-MiniLM-L6-v2',
            'embeddingDim': self.embeddings.shape[1] if self.embeddings is not None else 0,
            'providers': provider_counts,
        }

    def add_note(self, conversation_id: str, note_text: str) -> dict:
        """Append a note to a conversation via sidecar persistence."""
        if not conversation_id or not str(conversation_id).strip():
            return {'error': 'Invalid conversation ID'}
        if not note_text or not str(note_text).strip():
            return {'error': 'Note text cannot be empty'}

        self.load()
        conv = self.conversation_index.get(conversation_id)
        if not conv:
            return {'error': f"Conversation {conversation_id} not found."}

        note = append_note(self.data_dir, conversation_id, note_text)
        # Refresh in-memory cache
        self.notes = load_notes(self.data_dir)

        logger.info("Note added", extra={'conversation_id': conversation_id})
        return {
            'status': 'success',
            'conversation_id': conversation_id,
            'note': note,
            'notes': get_notes_for_conversation(self.data_dir, conversation_id),
        }

    def delete_note(self, conversation_id: str, note_id: str) -> dict:
        """Delete a note by note_id."""
        if not conversation_id or not str(conversation_id).strip():
            return {'error': 'Invalid conversation ID'}
        if not note_id or not str(note_id).strip():
            return {'error': 'Invalid note ID'}

        self.load()
        success = _delete_note(self.data_dir, conversation_id, note_id)
        # Refresh in-memory cache
        self.notes = load_notes(self.data_dir)

        if success:
            logger.info("Note deleted", extra={'conversation_id': conversation_id, 'note_id': note_id})
            return {
                'status': 'success',
                'conversation_id': conversation_id,
                'notes': get_notes_for_conversation(self.data_dir, conversation_id),
            }
        return {'error': f"Note {note_id} not found in conversation {conversation_id}"}

    def list_conversations(self, offset=0, limit=20, sort_by="date", provider=None):
        """List conversations with pagination and optional provider filtering."""
        self.load()

        limit = min(max(1, int(limit)), 50)
        offset = max(0, int(offset))

        convs = list(self.conversations)

        if provider:
            convs = [c for c in convs if c.get('provider', 'claude') == provider]

        if sort_by == "title":
            convs.sort(key=lambda c: c.get('name', '').lower())
        elif sort_by == "message_count":
            convs.sort(key=lambda c: len(c.get('messages', [])), reverse=True)
        else:  # date (newest first)
            convs.sort(key=lambda c: c.get('created_at', ''), reverse=True)

        total = len(convs)
        page = convs[offset:offset + limit]

        items = []
        for c in page:
            cluster_info = self.conv_cluster.get(c['id'], {})
            items.append({
                'id': c['id'],
                'title': c.get('name', ''),
                'date': c.get('created_at', ''),
                'provider': c.get('provider', 'claude'),
                'message_count': len(c.get('messages', [])),
                'has_notes': len(self.notes.get(c['id'], [])) > 0,
                'cluster_id': cluster_info.get('cluster_id', 0),
            })

        return {
            'conversations': items,
            'total': total,
            'offset': offset,
            'limit': limit,
        }

    def list_recent_conversations(self, n=10, before=None, after=None, provider=None):
        """List the most recent conversations, sorted by date (newest first).

        Useful for browsing recent activity without a search query.
        Supports optional date filtering with ISO date strings.

        Args:
            n: Number of conversations to return (default 10, max 50).
            before: Only include conversations before this ISO date (e.g. "2025-06-01").
            after: Only include conversations after this ISO date (e.g. "2025-01-01").
            provider: Optional provider filter.

        Returns:
            List of recent conversations with id, title, date, message_count,
            and cluster_label.
        """
        self.load()

        n = min(max(1, int(n)), 50)
        convs = list(self.conversations)

        if provider:
            convs = [c for c in convs if c.get('provider', 'claude') == provider]

        if before:
            convs = [c for c in convs if c.get('created_at', '') < before]

        if after:
            convs = [c for c in convs if c.get('created_at', '') > after]

        convs.sort(key=lambda c: c.get('created_at', ''), reverse=True)
        page = convs[:n]

        items = []
        for c in page:
            cluster_info = self.conv_cluster.get(c['id'], {})
            items.append({
                'id': c['id'],
                'title': c.get('name', ''),
                'date': c.get('created_at', ''),
                'provider': c.get('provider', 'claude'),
                'message_count': len(c.get('messages', [])),
                'cluster_label': cluster_info.get('cluster_label', ''),
            })

        return items
