"""Build vector index, clusters, and graph data from embeddings.

Constellation V3 — core/indexer.py
"""

import json
import os
import re
from collections import Counter
from datetime import datetime

import numpy as np

from core.math_utils import (
    auto_cluster,
    cosine_similarity_matrix,
    kmeans,
    pca_3d,
    silhouette_score,
)
from core.parser import extract_top_terms


# Cluster color palettes
DEFAULT_PALETTE = [
    '#7957d9',  # Violet (primary)
    '#57b4d9',  # Cyan-blue
    '#d9a857',  # Warm gold
    '#57d98b',  # Green
    '#d957a8',  # Magenta-pink
    '#d97957',  # Warm orange
    '#57d9d9',  # Teal
    '#a857d9',  # Purple
    '#d9d957',  # Yellow
    '#5779d9',  # Blue
    '#d95757',  # Red
    '#57d957',  # Lime
    '#d957d9',  # Fuchsia
    '#8b8bd9',  # Lavender
    '#d98b57',  # Amber
    '#57d9a8',  # Mint
    '#d95779',  # Rose
    '#79d957',  # Chartreuse
    '#5757d9',  # Indigo
    '#d9a8a8',  # Blush
]

COLORBLIND_PALETTE = [
    '#0072B2',  # Blue
    '#E69F00',  # Orange
    '#56B4E9',  # Sky blue
    '#009E73',  # Green
    '#F0E442',  # Yellow
    '#CC79A7',  # Pink
    '#D55E00',  # Vermillion
    '#0072B2',
    '#E69F00',
    '#56B4E9',
    '#009E73',
    '#F0E442',
    '#CC79A7',
    '#D55E00',
    '#0072B2',
    '#E69F00',
    '#56B4E9',
    '#009E73',
    '#F0E442',
    '#CC79A7',
]


def embed_conversations(conversations: list, embedder) -> np.ndarray:
    """Embed all conversations using mean pooling over user messages.

    For each conversation:
    1. Embed every user message individually
    2. Average the resulting vectors
    3. L2-normalize the mean vector

    Returns (N, dim) numpy array where N = number of conversations.
    """
    all_user_messages = []
    message_counts = []
    for conv in conversations:
        msgs = conv['user_messages']
        if not msgs:
            msgs = [conv.get('name', 'untitled')]
        all_user_messages.extend(msgs)
        message_counts.append(len(msgs))

    print(f"Embedding {len(all_user_messages)} messages from "
          f"{len(conversations)} conversations...")
    all_embeddings = embedder.embed(all_user_messages)

    # Mean pool per conversation
    conv_embeddings = []
    chunk_to_conv = []
    idx = 0
    for conv_idx, count in enumerate(message_counts):
        if count == 0:
            # Fallback if no user messages
            mean_vector = np.zeros(embedder.dim)
            conv_embeddings.append(mean_vector)
            continue
            
        msg_vectors = all_embeddings[idx:idx + count]
        mean_vector = msg_vectors.mean(axis=0)
        norm = np.linalg.norm(mean_vector)
        if norm > 0:
            mean_vector /= norm
        conv_embeddings.append(mean_vector)
        for _ in range(count):
            chunk_to_conv.append(conv_idx)
        idx += count

    return np.array(conv_embeddings), all_embeddings, chunk_to_conv


def build_clusters(embeddings: np.ndarray, k_override: int = None) -> dict:
    """Cluster conversation embeddings."""
    if k_override:
        labels, centroids = kmeans(embeddings, k_override)
        k = k_override
    else:
        k, labels, centroids = auto_cluster(embeddings, k_min=5, k_max=20)
        print(f"Auto-detected {k} clusters (silhouette score sweep)")

    return {
        'k': k,
        'labels': labels,
        'centroids': centroids,
    }


def generate_cluster_label(conversations: list, labels: np.ndarray,
                           centroids: np.ndarray, embeddings: np.ndarray,
                           cluster_id: int) -> str:
    """Generate a label for a cluster from its closest conversations."""
    mask = labels == cluster_id
    if not np.any(mask):
        return f"Cluster {cluster_id}"

    cluster_indices = np.where(mask)[0]
    cluster_embeddings = embeddings[cluster_indices]

    # Find conversations closest to centroid
    sims = cluster_embeddings @ centroids[cluster_id]
    top_indices = np.argsort(sims)[::-1][:5]

    # Extract terms from those conversations' titles and first messages
    all_text = []
    for idx in top_indices:
        conv_idx = cluster_indices[idx]
        conv = conversations[conv_idx]
        all_text.append(conv['name'])
        if conv['user_messages']:
            all_text.append(conv['user_messages'][0][:200])

    combined = ' '.join(all_text)
    terms = extract_top_terms(combined, n=3)
    if terms:
        return ' / '.join(terms)
    return f"Cluster {cluster_id}"


def build_edges(embeddings: np.ndarray, conversations: list,
                percentile: float = 95.0, max_edges_per_node: int = 15) -> list:
    """Build edge list from embedding similarity."""
    sim_matrix = cosine_similarity_matrix(embeddings)
    np.fill_diagonal(sim_matrix, 0)

    upper_tri = sim_matrix[np.triu_indices_from(sim_matrix, k=1)]
    threshold = np.percentile(upper_tri, percentile)
    print(f"Edge threshold: {threshold:.3f} (top {100 - percentile:.0f}% of pairs)")

    edges = []
    edge_counts = np.zeros(len(conversations), dtype=int)
    pairs = np.argwhere(np.triu(sim_matrix > threshold, k=1))
    pair_sims = [(i, j, sim_matrix[i, j]) for i, j in pairs]
    pair_sims.sort(key=lambda x: -x[2])

    for i, j, sim in pair_sims:
        if edge_counts[i] < max_edges_per_node and edge_counts[j] < max_edges_per_node:
            edges.append({
                'source': conversations[i]['id'],
                'target': conversations[j]['id'],
                'weight': float(sim),
            })
            edge_counts[i] += 1
            edge_counts[j] += 1

    print(f"Created {len(edges)} edges (threshold={threshold:.3f}, "
          f"max {max_edges_per_node}/node)")
    return edges


def build_graph_data(conversations: list, embeddings: np.ndarray,
                     cluster_info: dict, edges: list) -> dict:
    """Build the frontend-ready graph_data.json structure."""
    labels = cluster_info['labels']
    centroids = cluster_info['centroids']
    k = cluster_info['k']

    # PCA 3D positions
    positions = pca_3d(embeddings)

    # Temporal helix positions
    timestamps = []
    for conv in conversations:
        try:
            dt = datetime.fromisoformat(conv['created_at'].replace('Z', '+00:00'))
            timestamps.append(dt.timestamp())
        except (ValueError, AttributeError):
            timestamps.append(0)

    ts_array = np.array(timestamps)
    if ts_array.max() > ts_array.min():
        t_norm = (ts_array - ts_array.min()) / (ts_array.max() - ts_array.min())
    else:
        t_norm = np.zeros_like(ts_array)

    # For temporal helix: x = time, y/z = PCA components 1-2
    centered = embeddings - embeddings.mean(axis=0)
    U, S, Vt = np.linalg.svd(centered, full_matrices=False)
    pca_2d = centered @ Vt[:2].T
    max_val = np.abs(pca_2d).max()
    if max_val > 0:
        pca_2d *= 80 / max_val
    t_positions = np.column_stack([t_norm * 200 - 100, pca_2d])

    # Generate cluster labels and colors
    clusters = []
    for c in range(k):
        label = generate_cluster_label(conversations, labels, centroids,
                                       embeddings, c)
        count = int(np.sum(labels == c))
        clusters.append({
            'id': c,
            'label': label,
            'color': DEFAULT_PALETTE[c % len(DEFAULT_PALETTE)],
            'count': count,
        })

    # Build nodes
    nodes = []
    for i, conv in enumerate(conversations):
        first_user_msg = ''
        if conv['user_messages']:
            first_user_msg = conv['user_messages'][0][:200]

        all_text = conv['name'] + ' ' + ' '.join(conv['user_messages'][:3])
        top_terms = extract_top_terms(all_text, n=4)

        date_str = ''
        try:
            dt = datetime.fromisoformat(conv['created_at'].replace('Z', '+00:00'))
            date_str = dt.strftime('%Y-%m-%d')
        except (ValueError, AttributeError):
            pass

        cluster_id = int(labels[i])
        nodes.append({
            'id': conv['id'],
            'name': conv['name'],
            'cluster': cluster_id,
            'clusterLabel': clusters[cluster_id]['label'],
            'color': clusters[cluster_id]['color'],
            'x': float(positions[i, 0]),
            'y': float(positions[i, 1]),
            'z': float(positions[i, 2]),
            'tx': float(t_positions[i, 0]),
            'ty': float(t_positions[i, 1]),
            'tz': float(t_positions[i, 2]),
            'messageCount': len(conv['messages']),
            'date': date_str,
            'topTerms': top_terms,
            'snippet': first_user_msg,
        })

    # Build timeline data
    timeline = build_timeline(conversations, labels)

    # Stats
    total_messages = sum(len(c['messages']) for c in conversations)
    dates = [n['date'] for n in nodes if n['date']]
    date_range = [min(dates), max(dates)] if dates else ['', '']

    stats = {
        'totalConversations': len(conversations),
        'totalMessages': total_messages,
        'dateRange': date_range,
        'embeddingModel': 'all-MiniLM-L6-v2',
        'embeddingDim': 384,
        'clusterCount': k,
        'edgeThresholdPercentile': 95,
        'edgeCount': len(edges),
    }

    return {
        'nodes': nodes,
        'edges': edges,
        'clusters': clusters,
        'timeline': timeline,
        'stats': stats,
    }


def build_timeline(conversations: list, labels: np.ndarray) -> list:
    """Build monthly timeline data with cluster breakdown."""
    months = {}
    for i, conv in enumerate(conversations):
        try:
            dt = datetime.fromisoformat(conv['created_at'].replace('Z', '+00:00'))
            month_key = dt.strftime('%Y-%m')
        except (ValueError, AttributeError):
            continue

        if month_key not in months:
            months[month_key] = {'month': month_key, 'count': 0, 'clusterBreakdown': {}}
        months[month_key]['count'] += 1
        cl = str(int(labels[i]))
        months[month_key]['clusterBreakdown'][cl] = \
            months[month_key]['clusterBreakdown'].get(cl, 0) + 1

    return sorted(months.values(), key=lambda x: x['month'])


def save_pipeline_output(conversations: list, embeddings: np.ndarray,
                         chunk_embeddings: np.ndarray, chunk_to_conv: list,
                         graph_data: dict, data_dir: str):
    """Save all pipeline outputs to disk."""
    os.makedirs(data_dir, exist_ok=True)

    # Save embeddings as numpy binary
    np.save(os.path.join(data_dir, 'embeddings.npy'), embeddings)
    
    if chunk_embeddings is not None and chunk_to_conv is not None:
        np.save(os.path.join(data_dir, 'chunk_embeddings.npy'), chunk_embeddings)
        with open(os.path.join(data_dir, 'chunk_to_conv.json'), 'w') as f:
            json.dump(chunk_to_conv, f)

    # Save conversations (without user_messages to save space)
    conv_data = []
    for conv in conversations:
        conv_data.append({
            'id': conv['id'],
            'name': conv['name'],
            'created_at': conv['created_at'],
            'provider': conv['provider'],
            'messages': conv['messages'],
        })
    with open(os.path.join(data_dir, 'conversations.json'), 'w') as f:
        json.dump(conv_data, f)

    # Save clusters
    clusters_data = {
        'k': graph_data['stats']['clusterCount'],
        'clusters': graph_data['clusters'],
    }
    with open(os.path.join(data_dir, 'clusters.json'), 'w') as f:
        json.dump(clusters_data, f, indent=2)

    # Save graph data
    with open(os.path.join(data_dir, 'graph_data.json'), 'w') as f:
        json.dump(graph_data, f)

    print(f"Pipeline output saved to {data_dir}/")
