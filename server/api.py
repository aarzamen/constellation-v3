"""REST search endpoints for Constellation.

Shared logic between MCP server and HTTP API.

SearchEngine reads through core.storage generations: each public operation
captures one immutable loaded state (snapshot + derived indexes) and uses
only that state for its whole request, so a concurrent reload can never
mix two generations inside one response. Notes are loaded fresh per
operation from the sidecar, outside any index state.
"""

import collections
import threading
import time
from dataclasses import dataclass

import numpy as np

from core.config import DATA_DIR
from core.logger import get_logger
from core.math_utils import cosine_similarity_query
from core.notes import load_notes, append_note, delete_note as _delete_note, get_notes_for_conversation
from core.storage import IndexSnapshot, load_snapshot, read_generation_token

logger = get_logger(__name__)

# Assumed identity for legacy flat indexes that carry no recorded identity.
LEGACY_DEFAULT_MODEL = 'all-MiniLM-L6-v2'


@dataclass(frozen=True)
class _SearchState:
    """One fully built, immutable view of a loaded index generation."""
    snapshot: IndexSnapshot
    conversation_index: dict
    bm25: object
    chunk_to_local_idx: list
    cluster_meta: dict
    conv_cluster: dict


def _build_state(snapshot: IndexSnapshot) -> _SearchState:
    """Build every derived structure locally before it becomes visible."""
    conversations = snapshot.conversations
    conversation_index = {c['id']: c for c in conversations}

    from core.lexical import BM25Index
    bm25 = BM25Index()
    docs = []
    for c in conversations:
        doc_text = c.get('name', '') + ' ' + ' '.join(
            m.get('text', '') for m in c.get('messages', []))
        docs.append(doc_text)
    bm25.build(docs)

    # Link each chunk vector to its local index inside the parent's
    # user-message sequence. Rebuilt from scratch (or left empty) so a
    # generation without chunks carries no stale chunk state.
    chunk_to_local_idx = []
    if snapshot.chunk_to_conv is not None:
        counts = collections.defaultdict(int)
        for c_idx in snapshot.chunk_to_conv:
            chunk_to_local_idx.append(counts[c_idx])
            counts[c_idx] += 1

    cluster_meta = {}
    clusters = snapshot.clusters
    if isinstance(clusters, dict):
        for cl in clusters.get('clusters', []):
            if isinstance(cl, dict) and 'id' in cl:
                cluster_meta[cl['id']] = cl

    conv_cluster = {}
    graph_data = snapshot.graph_data
    if isinstance(graph_data, dict):
        for node in graph_data.get('nodes', []):
            conv_cluster[node['id']] = {
                'cluster_id': node.get('cluster', 0),
                'cluster_label': node.get('clusterLabel', ''),
            }

    return _SearchState(
        snapshot=snapshot,
        conversation_index=conversation_index,
        bm25=bm25,
        chunk_to_local_idx=chunk_to_local_idx,
        cluster_meta=cluster_meta,
        conv_cluster=conv_cluster,
    )


class SearchEngine:
    """Manages embeddings and conversation index for search."""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or DATA_DIR
        self.embedder = None
        self._state = None
        self._reload_lock = threading.RLock()
        self._embedder_cache = {}
        self._embedder_lock = threading.Lock()

    # -- backward-compatible views over the current loaded state ----------

    @property
    def conversations(self):
        state = self._state
        return state.snapshot.conversations if state else None

    @property
    def embeddings(self):
        state = self._state
        return state.snapshot.embeddings if state else None

    @property
    def chunk_embeddings(self):
        state = self._state
        return state.snapshot.chunk_embeddings if state else None

    @property
    def chunk_to_conv(self):
        state = self._state
        return state.snapshot.chunk_to_conv if state else None

    @property
    def chunk_to_local_idx(self):
        state = self._state
        return state.chunk_to_local_idx if state else []

    @property
    def conversation_index(self):
        state = self._state
        return state.conversation_index if state else {}

    @property
    def bm25(self):
        state = self._state
        return state.bm25 if state else None

    @property
    def cluster_meta(self):
        state = self._state
        return state.cluster_meta if state else {}

    @property
    def conv_cluster(self):
        state = self._state
        return state.conv_cluster if state else {}

    @property
    def _loaded(self):
        return self._state is not None

    @property
    def _loaded_mtime(self):
        # Historical name; now the loaded generation token.
        state = self._state
        return state.snapshot.token if state else None

    # -- loading -----------------------------------------------------------

    def load(self) -> _SearchState:
        """Load (or hot-reload) the current index generation.

        Reload keys off the published generation token, not file mtimes.
        The replacement state is built completely before a single reference
        swap; a failed load raises and leaves the previous state intact.
        Returns the state this request should use throughout.
        """
        with self._reload_lock:
            state = self._state
            if state is not None:
                # Cheap change check; corruption surfaces without touching
                # the held state.
                if read_generation_token(self.data_dir) == state.snapshot.token:
                    return state
            snapshot = load_snapshot(self.data_dir)
            if snapshot is None:
                raise FileNotFoundError(
                    f"Data files not found in {self.data_dir}. "
                    "Run the embedding pipeline first."
                )
            # The pointer may have advanced between the cheap check and the
            # load; the snapshot's own token is authoritative.
            new_state = _build_state(snapshot)
            self._state = new_state
            logger.info("Index loaded", extra={
                'generation': snapshot.token,
                'conversations': len(snapshot.conversations),
                'messages': sum(len(c.get('messages', []))
                                for c in snapshot.conversations),
            })
            return new_state

    def get_snapshot(self) -> IndexSnapshot:
        """Return the currently loaded IndexSnapshot, loading if needed."""
        return self.load().snapshot

    # -- embedder selection ------------------------------------------------

    @staticmethod
    def _identity_key(identity):
        if identity is None:
            return None
        return (identity.get('model'), identity.get('revision'),
                identity.get('policy'))

    def _ensure_embedder(self, state: _SearchState = None):
        """Return the query embedder matching the given state's identity.

        Semantic compatibility requires a verifiable identity: a generated
        snapshot missing its identity, or missing an immutable revision,
        cannot be safely queried and raises ValueError (reindex with an
        identifiable model). The legacy MiniLM assumption applies only to
        true flat legacy layouts (token 'legacy-*'). Embedders are cached
        per identity under their own lock; an injected self.embedder is
        honored only while its identity matches the snapshot (or, for
        legacy indexes, when it carries no identity — the explicit testing
        stand-in for the historical assumption). A previously constructed
        different-model embedder is never silently reused.
        """
        if state is None:
            state = self._state
        identity = state.snapshot.embedding_identity if state else None
        injected = self.embedder

        if identity is None:
            token = state.snapshot.token if state else None
            if not (isinstance(token, str) and token.startswith('legacy-')):
                raise ValueError(
                    'index generation records no embedding identity; '
                    'semantic search is unavailable until the index is '
                    'rebuilt with an identifiable model')
            # True flat legacy vectors: explicit historical assumption of
            # the default MiniLM model. An identity-less injected embedder
            # stands in for that assumption (tests only).
            if injected is not None and \
                    getattr(injected, 'embedding_identity', None) is None:
                return injected
            with self._embedder_lock:
                emb = self._embedder_cache.get(None)
                if emb is None:
                    from core.embedder import Embedder
                    emb = Embedder()
                    self._embedder_cache[None] = emb
            return emb

        if identity.get('verified') is False:
            raise ValueError(
                'index embedding identity is explicitly marked unverified '
                f'({identity.get("model")!r}); semantic compatibility '
                'cannot be established — reindex with an identifiable '
                'model')

        if not identity.get('revision'):
            raise ValueError(
                'index embedding identity has no immutable revision '
                f'({identity.get("model")!r}); semantic compatibility '
                'cannot be established — reindex with an identifiable '
                'model')

        if injected is not None and \
                getattr(injected, 'embedding_identity', None) == identity:
            return injected

        key = self._identity_key(identity)
        with self._embedder_lock:
            emb = self._embedder_cache.get(key)
            if emb is None:
                from core.embedder import Embedder
                emb = Embedder(model_name=identity['model'],
                               revision=identity['revision'])
                actual = getattr(emb, 'embedding_identity', None)
                if actual != identity:
                    raise ValueError(
                        f'embedder identity mismatch: index was built with '
                        f'{identity} but the loaded model reports {actual}')
                self._embedder_cache[key] = emb
        return emb

    def _identity_verified(self, identity):
        """True only when a live embedder's reported identity matches a
        fully pinned (revision-carrying) index identity."""
        if identity is None or not identity.get('revision'):
            return False
        if identity.get('verified') is False:
            return False
        for cand in (self.embedder,
                     self._embedder_cache.get(self._identity_key(identity))):
            if cand is not None and \
                    getattr(cand, 'embedding_identity', None) == identity:
                return True
        return False

    # -- public operations -------------------------------------------------

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

        state = self.load()
        snap = state.snapshot
        conversations = snap.conversations
        embedder = self._ensure_embedder(state)
        notes = load_notes(self.data_dir)

        t0 = time.time()
        query_embedding = embedder.embed_query(query)
        semantic_scores = cosine_similarity_query(query_embedding, snap.embeddings)
        lexical_scores = state.bm25.get_scores(query)

        # Compute chunk-level (message-level) similarities
        has_chunks = snap.chunk_embeddings is not None
        conv_best_chunk_score = np.zeros(len(conversations))
        conv_best_chunk_idx = np.zeros(len(conversations), dtype=int) - 1

        if has_chunks:
            chunk_scores = cosine_similarity_query(query_embedding, snap.chunk_embeddings)
            for i, (score, c_idx) in enumerate(zip(chunk_scores, snap.chunk_to_conv)):
                if conv_best_chunk_idx[c_idx] == -1 or score > conv_best_chunk_score[c_idx]:
                    conv_best_chunk_score[c_idx] = score
                    conv_best_chunk_idx[c_idx] = i

        # Reciprocal Rank Fusion (RRF) combining conversation semantic, chunk semantic, and lexical
        sem_ranks = np.argsort(semantic_scores)[::-1]
        lex_ranks = np.argsort(lexical_scores)[::-1]

        rrf_scores = np.zeros(len(conversations))
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
            conv = conversations[idx]

            # Smart excerpt generation - pull the precise matching chunk/message text
            excerpt = ""
            if has_chunks and conv_best_chunk_idx[idx] != -1:
                chunk_id = conv_best_chunk_idx[idx]
                local_msg_idx = state.chunk_to_local_idx[chunk_id]

                # Reconstruct user_messages sequence on the fly
                user_msgs = [m['text'] for m in conv.get('messages', []) if m.get('role') == 'user' and m.get('text', '').strip()]
                if not user_msgs:
                    user_msgs = [conv.get('name', '')]

                if local_msg_idx < len(user_msgs):
                    excerpt = user_msgs[local_msg_idx][:800]

            # Fallback if chunks are missing
            if not excerpt and conv.get('messages'):
                excerpt = conv['messages'][0]['text'][:800]

            cluster_info = state.conv_cluster.get(conv['id'], {})
            conv_notes = notes.get(conv['id'], [])
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

        state = self.load()
        conv = state.conversation_index.get(conversation_id)
        if not conv:
            return {'error': 'Conversation not found'}
        cluster_info = state.conv_cluster.get(conversation_id, {})
        result = {
            'id': conv['id'],
            'title': conv['name'],
            'date': conv.get('created_at', ''),
            'provider': conv.get('provider', 'claude'),
            'messages': [
                {
                    'role': m['role'],
                    'text': m['text'],
                    # Messages store their timestamp under 'timestamp' (set by
                    # every parser since v4.4); 'created_at' kept as fallback
                    # for any pre-v4.4 data still on disk.
                    'timestamp': m.get('timestamp', m.get('created_at', '')),
                }
                for m in conv.get('messages', [])
            ],
            'notes': get_notes_for_conversation(self.data_dir, conversation_id),
            'cluster_id': cluster_info.get('cluster_id', 0),
            'cluster_label': cluster_info.get('cluster_label', 'Unknown'),
        }
        # Optional reconciliation provenance, surfaced only when recorded.
        for key in ('revision_of', 'model', 'inferred_grouping',
                    'system_instruction'):
            if key in conv:
                result[key] = conv[key]
        return result

    def get_stats(self) -> dict:
        """Return index statistics."""
        state = self.load()
        snap = state.snapshot
        conversations = snap.conversations
        total_messages = sum(len(c.get('messages', [])) for c in conversations)
        dates = []
        for c in conversations:
            ca = c.get('created_at', '')
            if ca:
                dates.append(ca[:10])

        provider_counts = {}
        for c in conversations:
            p = c.get('provider', 'claude')
            provider_counts[p] = provider_counts.get(p, 0) + 1

        # Browsing/stats stay usable regardless of identity: report the
        # actual known model name, the explicit legacy assumption for flat
        # legacy layouts only, or 'unknown' — never a silent default.
        identity = snap.embedding_identity
        is_legacy = isinstance(snap.token, str) and \
            snap.token.startswith('legacy-')
        legacy_assumed = identity is None and is_legacy
        if identity and identity.get('model'):
            model = identity['model']
        elif legacy_assumed:
            model = LEGACY_DEFAULT_MODEL
        else:
            model = 'unknown'

        return {
            'totalConversations': len(conversations),
            'totalMessages': total_messages,
            'dateRange': [min(dates), max(dates)] if dates else ['', ''],
            'embeddingModel': model,
            'embeddingDim': snap.embeddings.shape[1] if snap.embeddings is not None else 0,
            'providers': provider_counts,
            'generation': snap.token,
            'embeddingIdentity': identity,
            'identityVerified': self._identity_verified(identity),
            'identityLegacyAssumed': legacy_assumed,
        }

    def add_note(self, conversation_id: str, note_text: str) -> dict:
        """Append a note to a conversation via sidecar persistence."""
        if not conversation_id or not str(conversation_id).strip():
            return {'error': 'Invalid conversation ID'}
        if not note_text or not str(note_text).strip():
            return {'error': 'Note text cannot be empty'}

        state = self.load()
        conv = state.conversation_index.get(conversation_id)
        if not conv:
            return {'error': f"Conversation {conversation_id} not found."}

        note = append_note(self.data_dir, conversation_id, note_text)

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
        state = self.load()
        notes = load_notes(self.data_dir)

        limit = min(max(1, int(limit)), 50)
        offset = max(0, int(offset))

        convs = list(state.snapshot.conversations)

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
            cluster_info = state.conv_cluster.get(c['id'], {})
            items.append({
                'id': c['id'],
                'title': c.get('name', ''),
                'date': c.get('created_at', ''),
                'provider': c.get('provider', 'claude'),
                'message_count': len(c.get('messages', [])),
                'has_notes': len(notes.get(c['id'], [])) > 0,
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
        state = self.load()

        n = min(max(1, int(n)), 50)
        convs = list(state.snapshot.conversations)

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
            cluster_info = state.conv_cluster.get(c['id'], {})
            items.append({
                'id': c['id'],
                'title': c.get('name', ''),
                'date': c.get('created_at', ''),
                'provider': c.get('provider', 'claude'),
                'message_count': len(c.get('messages', [])),
                'cluster_label': cluster_info.get('cluster_label', ''),
            })

        return items
