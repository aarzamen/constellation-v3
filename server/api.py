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
        
        # Build lexical index
        from core.lexical import BM25Index
        self.bm25 = BM25Index()
        docs = []
        for c in self.conversations:
            doc_text = c.get('name', '') + ' ' + ' '.join(m.get('text', '') for m in c.get('messages', []))
            docs.append(doc_text)
        self.bm25.build(docs)

        self._loaded = True
        import sys
        print(f"Loaded {len(self.conversations)} conversations, "
              f"embeddings shape {self.embeddings.shape}, "
              f"BM25 index built", file=sys.stderr)

    def _ensure_embedder(self):
        """Lazy-load the embedding model for query embedding."""
        if self.embedder is None:
            from core.embedder import Embedder
            self.embedder = Embedder()

    def search(self, query: str, top_k: int = 5) -> list:
        """Hybrid exact + semantic search over conversation history."""
        self.load()
        self._ensure_embedder()

        query_embedding = self.embedder.embed_query(query)
        semantic_scores = cosine_similarity_query(query_embedding, self.embeddings)
        lexical_scores = self.bm25.get_scores(query)

        # Reciprocal Rank Fusion (RRF)
        sem_ranks = np.argsort(semantic_scores)[::-1]
        lex_ranks = np.argsort(lexical_scores)[::-1]

        rrf_scores = np.zeros(len(self.conversations))
        k_rrf = 60

        for rank, idx in enumerate(sem_ranks):
            rrf_scores[idx] += 1.0 / (k_rrf + rank + 1)

        for rank, idx in enumerate(lex_ranks):
            if lexical_scores[idx] > 0:
                rrf_scores[idx] += 1.0 / (k_rrf + rank + 1)

        top_indices = np.argsort(rrf_scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            conv = self.conversations[idx]
            results.append({
                'id': conv['id'],
                'title': conv['name'],
                'date': conv.get('created_at', ''),
                'score': float(semantic_scores[idx]),
                'rrf_score': float(rrf_scores[idx]),
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

    def add_note(self, conversation_id: str, note_text: str) -> dict:
        """Append a generic note to a conversation's metadata and save it."""
        self.load()
        conv = self.conversation_index.get(conversation_id)
        if not conv:
            return {'error': 'Conversation not found'}
        
        # Initialize notes array if not present
        if 'notes' not in conv:
            conv['notes'] = []
            
        import datetime
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conv['notes'].append({'text': note_text, 'created_at': timestamp})
        
        # Save explicitly back to conversations.json
        conv_path = os.path.join(self.data_dir, 'conversations.json')
        with open(conv_path, 'w') as f:
            json.dump(self.conversations, f)
            
        import sys
        print(f"Added note to conversation {conversation_id}", file=sys.stderr)
        
        return {'status': 'success', 'conversation_id': conversation_id, 'note': note_text}
