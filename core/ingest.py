"""Shared ingest pipeline: reconcile, embed with cache reuse, publish.

Constellation — core/ingest.py (Task 2 of
docs/superpowers/plans/2026-09-07-index-preservation.md).

All import paths (launch, nightly, OpenAI, sweep) route through
``ingest_conversations``. Each attempt loads the current snapshot, reconciles
the FULL retained corpus with the incoming export (even under
``force_reembed``), reuses cached vectors only when both the embedding
identity and the exact embedding input match, rebuilds derived artifacts,
and publishes through ``core.indexer.save_pipeline_output`` with
compare-and-swap. A stale publication triggers a fresh load + rebuild, up to
``max_retries`` retries.

Notes are never written here beyond the read-triggered legacy migration plus
one backup before any index work; index locks never enclose note locks.

Public API::

    ingest_conversations(data_dir, incoming, embedder, force_reembed=False,
                         progress_callback=None, max_retries=3)
        -> IngestResult
"""

import copy
from dataclasses import dataclass

import numpy as np

import core.indexer as indexer
from core import notes as notes_store
from core import storage
from core.reconcile import reconcile_conversations
from core.storage import StaleGenerationError


@dataclass
class IngestResult:
    """Committed ingest outcome. Iterates as (before, after) so legacy
    ``before, after = ingest(...)`` callers keep working."""
    before: int
    after: int
    embedded: int
    report: dict
    snapshot: object

    def __iter__(self):
        yield self.before
        yield self.after


def _embedding_identity(embedder):
    """The embedder's identity dict, or None for a raw unknown embedder.

    Provenance (model + policy) is always persisted when known, even
    without a resolved revision; a missing revision only marks the identity
    UNVERIFIED, which blocks cache reuse but never erases the model name.
    """
    identity = getattr(embedder, 'embedding_identity', None)
    if (isinstance(identity, dict)
            and isinstance(identity.get('model'), str) and identity['model']
            and isinstance(identity.get('policy'), str)
            and identity['policy']):
        return copy.deepcopy(identity)
    return None


def _cache_eligible(identity):
    """Stored vectors may be reused only under a fully verified identity:
    non-empty model, policy, AND immutable revision, never marked
    unverified. Dimension or model-name agreement alone is not enough."""
    return (isinstance(identity, dict)
            and isinstance(identity.get('model'), str) and identity['model']
            and isinstance(identity.get('policy'), str) and identity['policy']
            and isinstance(identity.get('revision'), str)
            and identity['revision']
            and identity.get('verified') is not False)


def _embed_input(rec):
    """Embedding input texts: non-whitespace user messages, else the title."""
    texts = [m.get('text', '') for m in rec.get('messages', [])
             if m.get('role') == 'user']
    texts = [t for t in texts if isinstance(t, str) and t.strip()]
    return texts or [rec.get('name') or 'untitled']


def _build_vectors(snap, convs, embedder, identity, force_reembed,
                   progress_callback):
    """Assemble the (N, dim) matrix and optional chunk pair for `convs`.

    A cached vector is reused only when the snapshot identity is verified
    (non-empty revision) and equals the embedder identity, the exact
    embedding input is unchanged, and the snapshot's chunk coverage for
    that record is complete. A record without complete chunk coverage —
    including every record of a chunkless snapshot — is re-embedded so the
    published generation always carries chunks for all conversations.
    """
    reuse = {}
    if (snap is not None and not force_reembed and _cache_eligible(identity)
            and snap.embedding_identity == identity
            and snap.chunk_embeddings is not None):
        old_index = {c['id']: i for i, c in enumerate(snap.conversations)}
        rows_by_conv = {}
        for row, conv_idx in enumerate(snap.chunk_to_conv):
            rows_by_conv.setdefault(int(conv_idx), []).append(row)
        for conv in convs:
            i = old_index.get(conv['id'])
            if i is None:
                continue
            texts = _embed_input(conv)
            if _embed_input(snap.conversations[i]) != texts:
                continue
            rows = rows_by_conv.get(i, [])
            if len(rows) != len(texts):
                continue  # incomplete chunk coverage: re-embed this record
            reuse[conv['id']] = (np.array(snap.embeddings[i]),
                                 np.array(snap.chunk_embeddings[rows]))

    to_embed = [c for c in convs if c['id'] not in reuse]
    new_vecs = {}
    if to_embed:
        payload = [dict(c, user_messages=_embed_input(c)) for c in to_embed]
        conv_vecs, all_vecs, _ = indexer.embed_conversations(
            payload, embedder, progress_callback=progress_callback)
        pos = 0
        for j, rec in enumerate(payload):
            count = len(rec['user_messages'])
            new_vecs[rec['id']] = (conv_vecs[j],
                                   np.array(all_vecs[pos:pos + count]))
            pos += count

    embeddings = np.zeros((len(convs), embedder.dim), dtype=np.float64)
    chunk_parts, chunk_map = [], []
    for idx, conv in enumerate(convs):
        vec, chunk_vecs = reuse.get(conv['id']) or new_vecs[conv['id']]
        embeddings[idx] = vec
        chunk_parts.append(chunk_vecs)
        chunk_map.extend([idx] * len(chunk_vecs))
    if chunk_parts:
        return embeddings, np.vstack(chunk_parts), chunk_map, len(to_embed)
    return embeddings, None, None, len(to_embed)


def _build_graph(convs, embeddings):
    """Build graph data; an empty corpus yields a valid empty graph without
    touching the math routines."""
    if not convs:
        return {'nodes': [], 'edges': [], 'clusters': [], 'timeline': [],
                'stats': {'totalConversations': 0, 'totalMessages': 0}}
    cluster_info = indexer.build_clusters(embeddings)
    edges = indexer.build_edges(embeddings, convs)
    return indexer.build_graph_data(convs, embeddings, cluster_info, edges)


def ingest_conversations(data_dir, incoming, embedder, force_reembed=False,
                         progress_callback=None, max_retries=3):
    """Reconcile `incoming` into the archive at `data_dir` and publish.

    Returns an IngestResult whose snapshot is the committed generation.
    Raises ValueError on reconciliation conflicts and StaleGenerationError
    if the compare-and-swap loses `max_retries + 1` races in a row; the
    existing index and notes are untouched on any failure.
    """
    # Legacy note migration plus one backup happen before any index work.
    notes_store.load_notes(data_dir)
    notes_store.backup_notes(data_dir)
    identity = _embedding_identity(embedder)

    stale_error = None
    for _ in range(max_retries + 1):
        snap = storage.load_snapshot(data_dir)
        existing = snap.conversations if snap is not None else []
        convs, report = reconcile_conversations(existing, incoming)
        embeddings, chunk_embeddings, chunk_to_conv, embedded = _build_vectors(
            snap, convs, embedder, identity, force_reembed, progress_callback)
        graph_data = _build_graph(convs, embeddings)
        stats = graph_data.get('stats')
        if isinstance(stats, dict):
            # Stats carry the actual provenance: the known model name (even
            # unverified) or 'unknown', never an inherited hardcoded default.
            stats['embeddingModel'] = (identity['model'] if identity
                                       else 'unknown')
            stats['embeddingDim'] = int(embeddings.shape[1])
        try:
            committed = indexer.save_pipeline_output(
                convs, embeddings, chunk_embeddings, chunk_to_conv,
                graph_data, data_dir,
                expected_generation=snap.token if snap is not None else None,
                embedding_identity=identity)
        except StaleGenerationError as e:
            stale_error = e
            continue
        return IngestResult(before=len(existing), after=len(convs),
                            embedded=embedded, report=report,
                            snapshot=committed)
    raise stale_error
