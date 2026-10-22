import numpy as np

from rag.llm import client

EMBED_MODEL = "baai/bge-m3"


def embed(texts, *, model=EMBED_MODEL, batch_size=16, timeout=300.0):
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = []
    for i in range(0, len(texts), batch_size):
        r = client().embeddings.create(model=model, input=texts[i:i + batch_size], timeout=timeout)
        vectors += [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
    m = np.array(vectors, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)
