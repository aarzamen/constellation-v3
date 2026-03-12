"""Embedding engine for Constellation V3.

Local model: all-MiniLM-L6-v2 via sentence-transformers.
Clean interface for future extensibility.

# TODO: Evaluate Gemini Embedding 2 for future multimodal support
# 3072-dim, Matryoshka, text+image+video+audio in one space
# Would require full re-embed (incompatible with MiniLM-L6-v2 384-dim)
"""

import numpy as np


class Embedder:
    """Local embedding engine using sentence-transformers."""

    def __init__(self, model_name='all-MiniLM-L6-v2'):
        import sys
        print(f"Loading embedding model: {model_name}...", file=sys.stderr)
        print("(First run will download ~80MB model, cached for future use)", file=sys.stderr)
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()
        print(f"Model loaded: {model_name} ({self.dim}d)", file=sys.stderr)

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
