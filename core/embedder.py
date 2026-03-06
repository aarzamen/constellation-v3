"""Embedding engine for Constellation V3.

Local model: all-MiniLM-L6-v2 via sentence-transformers.
Clean interface for future extensibility.
"""

import numpy as np


class Embedder:
    """Local embedding engine using sentence-transformers."""

    def __init__(self, model_name='all-MiniLM-L6-v2'):
        print(f"Loading embedding model: {model_name}...")
        print("(First run will download ~80MB model, cached for future use)")
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()
        print(f"Model loaded: {model_name} ({self.dim}d)")

    def embed(self, texts: list, show_progress: bool = True) -> np.ndarray:
        """Embed a list of texts. Returns (N, dim) numpy array, L2-normalized."""
        return self.model.encode(
            texts,
            show_progress_bar=show_progress,
            normalize_embeddings=True,
        )

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a single query string. Returns (1, dim) numpy array."""
        return self.embed([query], show_progress=False)
