"""Lightweight BM25 Lexical Search Engine for Constellation V3.

Native Python implementation to avoid heavy framework dependencies.
"""

import math
import re
from collections import Counter


def tokenize(text: str) -> list:
    """Simple alphanumeric tokenizer."""
    if not text:
        return []
    # Lowercase and extract word characters
    return re.findall(r'\b\w+\b', text.lower())


class BM25Index:
    """Okapi BM25 inverted index for exact-match retrieval."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_lengths = []
        self.avg_doc_len = 0.0
        self.doc_count = 0
        self.term_freqs = []  # term frequencies per document
        self.doc_freqs = Counter()  # number of documents containing each term
        self.idf = {}  # inverted document frequency cache
        self.is_built = False

    def build(self, documents: list[str]):
        """Build the index from a list of document strings."""
        self.doc_count = len(documents)
        self.doc_lengths = np_zeros = [0] * self.doc_count
        self.term_freqs = [{} for _ in range(self.doc_count)]
        self.doc_freqs.clear()
        
        total_len = 0
        for i, doc in enumerate(documents):
            tokens = tokenize(doc)
            self.doc_lengths[i] = len(tokens)
            total_len += len(tokens)
            
            freq = dict(Counter(tokens))
            self.term_freqs[i] = freq
            for term in freq:
                self.doc_freqs[term] += 1

        self.avg_doc_len = total_len / max(1, self.doc_count)

        # Precompute IDF for all terms
        self.idf.clear()
        for term, df in self.doc_freqs.items():
            # Standard Okapi IDF
            v = math.log(1 + (self.doc_count - df + 0.5) / (df + 0.5))
            self.idf[term] = v

        self.is_built = True

    def get_scores(self, query: str) -> list[float]:
        """Score all documents against the query. Returns a list of floats."""
        if not self.is_built or self.doc_count == 0:
            return []

        tokens = tokenize(query)
        scores = [0.0] * self.doc_count

        for term in tokens:
            if term not in self.idf:
                continue
                
            term_idf = self.idf[term]
            for i in range(self.doc_count):
                tf = self.term_freqs[i].get(term, 0)
                if tf == 0:
                    continue
                    
                doc_len = self.doc_lengths[i]
                
                # BM25 formula scoring
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * (doc_len / self.avg_doc_len))
                scores[i] += term_idf * (numerator / denominator)

        return scores
