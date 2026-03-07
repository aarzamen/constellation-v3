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

        self._loaded = True
        import sys
        print(f"Loaded {len(self.conversations)} conversations, "
              f"embeddings shape {self.embeddings.shape}, "
              f"chunk blocks {self.chunk_embeddings.shape if self.chunk_embeddings is not None else 'None'}", file=sys.stderr)

    def _ensure_embedder(self):
        """Lazy-load the embedding model for query embedding."""
        if self.embedder is None:
            from core.embedder import Embedder
            self.embedder = Embedder()

    def search(self, query: str, top_k: int = 5) -> list:
        """Hybrid exact + semantic search over conversation history."""
        if not query or not query.strip():
            return []
            
        try:
            top_k = int(top_k)
            if top_k < 1: top_k = 5
        except (ValueError, TypeError):
            top_k = 5

        self.load()
        self._ensure_embedder()

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

        top_indices = np.argsort(rrf_scores)[::-1][:top_k]

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

            results.append({
                'id': conv['id'],
                'title': conv['name'],
                'date': conv.get('created_at', ''),
                'score': float(rrf_scores[idx]),
                'conversation_score': float(semantic_scores[idx]),
                'chunk_score': float(conv_best_chunk_score[idx]) if has_chunks else 0.0,
                'lexical_score': float(lexical_scores[idx]),
                'message_count': len(conv.get('messages', [])),
                'excerpt': excerpt + ('...' if len(excerpt) == 800 else ''),
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
        if not conversation_id or not str(conversation_id).strip():
            return {'error': 'Invalid conversation ID'}
        if not note_text or not str(note_text).strip():
            return {'error': 'Note text cannot be empty'}
            
        self.load()
        conv = self.conversation_index.get(conversation_id)
        if not conv:
            return {'error': f"Conversation {conversation_id} not found."}
        
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
