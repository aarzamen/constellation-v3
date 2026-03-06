"""REST search endpoints for Constellation V3.

Shared logic between MCP server and HTTP API.
"""

import json
import os

import numpy as np

from core.config import DATA_DIR
from core.math_utils import cosine_similarity_query


class SearchEngine:
    """Manages embeddings and conversation index for search."""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or DATA_DIR
        self.embeddings = None
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
        self._loaded = True
        print(f"Loaded {len(self.conversations)} conversations, "
              f"embeddings shape {self.embeddings.shape}")

    def _ensure_embedder(self):
        """Lazy-load the embedding model for query embedding."""
        if self.embedder is None:
            from core.embedder import Embedder
            self.embedder = Embedder()

    def search(self, query: str, top_k: int = 5) -> list:
        """Semantic search over conversation history."""
        self.load()
        self._ensure_embedder()

        query_embedding = self.embedder.embed_query(query)
        similarities = cosine_similarity_query(query_embedding, self.embeddings)
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            conv = self.conversations[idx]
            results.append({
                'id': conv['id'],
                'title': conv['name'],
                'date': conv.get('created_at', ''),
                'score': float(similarities[idx]),
                'message_count': len(conv.get('messages', [])),
                'excerpt': conv['messages'][0]['text'][:500]
                    if conv.get('messages') else '',
                'messages': [
                    {'role': m['role'], 'text': m['text'][:1000]}
                    for m in conv.get('messages', [])[:10]
                ],
            })
        return results

    def get_conversation(self, conversation_id: str) -> dict:
        """Retrieve full conversation by ID."""
        self.load()
        conv = self.conversation_index.get(conversation_id)
        if not conv:
            return {'error': 'Conversation not found'}
        return {
            'id': conv['id'],
            'title': conv['name'],
            'date': conv.get('created_at', ''),
            'messages': [
                {'role': m['role'], 'text': m['text']}
                for m in conv.get('messages', [])
            ],
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
        return {
            'totalConversations': len(self.conversations),
            'totalMessages': total_messages,
            'dateRange': [min(dates), max(dates)] if dates else ['', ''],
            'embeddingModel': 'all-MiniLM-L6-v2',
            'embeddingDim': self.embeddings.shape[1] if self.embeddings is not None else 0,
        }
