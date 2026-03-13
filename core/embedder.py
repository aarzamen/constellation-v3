"""Embedding engine for Constellation.

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

    def embed(self, texts: list, show_progress: bool = True,
              progress_callback=None, batch_size: int = 64) -> np.ndarray:
        """Embed a list of texts. Returns (N, dim) numpy array, L2-normalized.

        Args:
            progress_callback: Optional callable(done, total) called after each batch.
        """
        if progress_callback is None:
            return self.model.encode(
                texts,
                show_progress_bar=show_progress,
                normalize_embeddings=True,
                batch_size=batch_size,
            )

        # Batch encode with progress callback
        all_embeddings = []
        total = len(texts)
        for start in range(0, total, batch_size):
            batch = texts[start:start + batch_size]
            batch_emb = self.model.encode(
                batch,
                show_progress_bar=False,
                normalize_embeddings=True,
                batch_size=batch_size,
            )
            all_embeddings.append(batch_emb)
            progress_callback(min(start + batch_size, total), total)

        return np.vstack(all_embeddings)

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a single query string. Returns (1, dim) numpy array."""
        return self.embed([query], show_progress=False)
