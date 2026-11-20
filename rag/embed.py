import os
from functools import cache

import numpy as np
from openai import OpenAI

from rag.llm import client

EMBED_MODEL = os.environ.get("EMBED_MODEL") or "baai/bge-m3"


@cache
def embedding_client():
    base_url = os.environ.get("EMBED_BASE_URL")
    if not base_url:
        return client()
    return OpenAI(base_url=base_url, api_key=os.environ.get("EMBED_API_KEY") or "local", max_retries=1)


def embed(texts, *, model=EMBED_MODEL, batch_size=16, timeout=300.0):
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = []
    for i in range(0, len(texts), batch_size):
        r = embedding_client().embeddings.create(model=model, input=texts[i:i + batch_size], timeout=timeout)
        vectors += [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
    m = np.array(vectors, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)
