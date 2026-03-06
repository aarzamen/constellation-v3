"""Pure numpy math utilities for Constellation V3.

KMeans, PCA, cosine similarity — no sklearn dependency.
"""

import numpy as np


def safe_normalize(X: np.ndarray) -> np.ndarray:
    """L2-normalize rows, handling zero vectors gracefully."""
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # prevent divide by zero
    return X / norms


def cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Compute pairwise cosine similarity. Normalizes input first."""
    normed = safe_normalize(embeddings)
    return normed @ normed.T


def cosine_similarity_query(query: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a query vector and all embeddings."""
    query_norm = safe_normalize(query.reshape(1, -1) if query.ndim == 1 else query)
    emb_norm = safe_normalize(embeddings)
    return (query_norm @ emb_norm.T).flatten()


def kmeans(X: np.ndarray, n_clusters: int, n_init: int = 10,
           max_iter: int = 300, seed: int = 42) -> tuple:
    """K-means clustering on normalized vectors (spherical k-means).
    Returns (labels, centroids)."""
    rng = np.random.RandomState(seed)
    X_norm = safe_normalize(X)
    n_clusters = min(n_clusters, len(X_norm))

    if n_clusters < 1:
        return np.zeros(len(X_norm), dtype=int), X_norm[:1].copy()

    best_labels, best_centroids, best_inertia = None, None, np.inf

    for init_round in range(n_init):
        # Use different seed per init round
        init_rng = np.random.RandomState(seed + init_round)

        # K-means++ initialization
        indices = [init_rng.randint(len(X_norm))]
        for _ in range(1, n_clusters):
            dists = np.min(
                [np.sum((X_norm - X_norm[idx]) ** 2, axis=1) for idx in indices],
                axis=0
            )
            total = dists.sum()
            if total == 0:
                remaining = [i for i in range(len(X_norm)) if i not in indices]
                if remaining:
                    indices.append(init_rng.choice(remaining))
                else:
                    break
            else:
                probs = dists / total
                indices.append(init_rng.choice(len(X_norm), p=probs))
        centroids = X_norm[indices].copy()

        for _ in range(max_iter):
            # Assign each point to nearest centroid
            sims = X_norm @ centroids.T
            labels = np.argmax(sims, axis=1)

            # Update centroids
            new_centroids = np.zeros_like(centroids)
            for k in range(n_clusters):
                members = X_norm[labels == k]
                if len(members) > 0:
                    mean_vec = members.mean(axis=0)
                    norm = np.linalg.norm(mean_vec)
                    if norm > 0:
                        new_centroids[k] = mean_vec / norm
                    else:
                        new_centroids[k] = centroids[k]
                else:
                    # Empty cluster: reinitialize to random data point
                    new_centroids[k] = X_norm[init_rng.randint(len(X_norm))]

            if np.allclose(centroids, new_centroids, atol=1e-6):
                break
            centroids = new_centroids

        # Compute inertia (sum of squared distances to assigned centroid)
        inertia = 0.0
        for k in range(n_clusters):
            members = X_norm[labels == k]
            if len(members) > 0:
                inertia += np.sum((members - centroids[k]) ** 2)

        if inertia < best_inertia:
            best_labels, best_centroids, best_inertia = labels.copy(), centroids.copy(), inertia

    return best_labels, best_centroids


def silhouette_score(X: np.ndarray, labels: np.ndarray) -> float:
    """Simplified silhouette score for cluster quality evaluation."""
    X_norm = safe_normalize(X)
    n = len(X_norm)
    unique_labels = np.unique(labels)

    if len(unique_labels) < 2:
        return -1.0

    # Check no cluster is empty
    for ul in unique_labels:
        if np.sum(labels == ul) == 0:
            return -1.0

    scores = np.zeros(n)
    for i in range(n):
        same = X_norm[labels == labels[i]]
        if len(same) > 1:
            a_i = np.mean(1.0 - X_norm[i] @ same.T)
        else:
            a_i = 0.0

        b_i = np.inf
        for k in unique_labels:
            if k == labels[i]:
                continue
            other = X_norm[labels == k]
            if len(other) > 0:
                dist = np.mean(1.0 - X_norm[i] @ other.T)
                b_i = min(b_i, dist)

        denom = max(a_i, b_i)
        scores[i] = (b_i - a_i) / denom if denom > 0 else 0.0

    return float(np.mean(scores))


def auto_cluster(X: np.ndarray, k_min: int = 5, k_max: int = 20) -> tuple:
    """Find optimal cluster count via silhouette score sweep.
    Returns (best_k, labels, centroids)."""
    n = len(X)

    # Cap k_max: never more clusters than N/3
    k_max = min(k_max, max(2, n // 3))
    k_min = max(2, min(k_min, k_max))

    if n < 4:
        # Too few points for meaningful clustering
        labels = np.zeros(n, dtype=int)
        centroids = safe_normalize(X.mean(axis=0, keepdims=True))
        return 1, labels, centroids

    best_k, best_score = k_min, -2.0
    best_labels, best_centroids = None, None

    for k in range(k_min, k_max + 1):
        labels, centroids = kmeans(X, k)
        score = silhouette_score(X, labels)
        if score > best_score:
            best_k, best_score = k, score
            best_labels, best_centroids = labels.copy(), centroids.copy()

    return best_k, best_labels, best_centroids


def pca_3d(X: np.ndarray) -> np.ndarray:
    """Project high-dimensional vectors to 3D via PCA (SVD)."""
    centered = X - X.mean(axis=0)

    # Guard against zero-variance data
    if np.allclose(centered, 0):
        return np.zeros((len(X), 3))

    U, S, Vt = np.linalg.svd(centered, full_matrices=False)

    # Project onto top 3 components
    n_components = min(3, Vt.shape[0])
    projection = centered @ Vt[:n_components].T

    # Pad to 3D if fewer than 3 components
    if n_components < 3:
        padding = np.zeros((len(X), 3 - n_components))
        projection = np.column_stack([projection, padding])

    # Scale to reasonable viewport range
    max_val = np.abs(projection).max()
    if max_val > 0:
        projection *= 100.0 / max_val

    return projection
