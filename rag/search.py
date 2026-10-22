import re
import sys

import numpy as np
import Stemmer
from qdrant_client import models
from rank_bm25 import BM25Okapi

from rag.embed import embed
from rag.qdrant_store import COLLECTION, connect, scroll_all
from rag.tracing import observe

ALPHA = 0.6
stemmer = Stemmer.Stemmer("russian")


def tokenize(text):
    words = re.findall(r"\d+(?:\.\d+)+|\w+", text.lower())
    return [w if any(ch.isdigit() for ch in w) else stemmer.stemWord(w) for w in words]


def minmax(x):
    return (x - x.min()) / (x.max() - x.min() + 1e-9)


class Searcher:
    """Гибридный поиск: смысловой через Qdrant, BM25 в памяти по тем же фрагментам; оценки нормируются и складываются с весами ALPHA и 1 - ALPHA."""

    def __init__(self, client=None):
        self.client = client or connect()
        points = scroll_all(self.client)
        self.chunks = [p.payload for p in points]
        self.position = {p.id: i for i, p in enumerate(points)}
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in self.chunks])

    @observe(name="search", capture_output=False)
    def search(self, query, k=10, level=None):
        """level: ограничить уровнем образования («бакалавриат», «магистратура», «аспирантура», «СПО», «общее»)."""
        flt = models.Filter(must=[models.FieldCondition(key="level", match=models.MatchValue(value=level))]) if level else None
        found = self.client.query_points(COLLECTION, query=embed([query])[0].tolist(), limit=len(self.chunks), query_filter=flt, with_payload=False).points
        if not found:
            return []
        ids = np.array([self.position[p.id] for p in found])
        dense = np.array([p.score for p in found])
        bm25 = self.bm25.get_scores(tokenize(query))[ids]
        score = ALPHA * minmax(dense) + (1 - ALPHA) * minmax(bm25)
        return [{"score": float(score[i]), "chunk": self.chunks[ids[i]]} for i in np.argsort(-score)[:k]]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit()
    question = " ".join(sys.argv[1:])
    print("Вопрос:", question)
    for r in Searcher().search(question, k=5):
        c = r["chunk"]
        print(f"  {r['score']:.3f}  {c['id'].split('/')[-1][:38]:38} | {c['text']}")
