import atexit
import os
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

from rag.index import INDEX_PATH, load_index

COLLECTION = "mirea"
PATH = Path(__file__).resolve().parent.parent / "index" / "qdrant"
URL = os.environ.get("QDRANT_URL")


def connect(path=PATH):
    if URL:
        return QdrantClient(url=URL)
    client = QdrantClient(path=str(path))
    atexit.register(client.close)
    return client


def point_id(chunk_id):
    """Постоянный id точки из id фрагмента."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def build(index_path=INDEX_PATH, path=PATH):
    """Загружает готовые векторы и фрагменты из индекса (python -m rag.index) в коллекцию Qdrant; эмбеддинги не пересчитываются."""
    vectors, chunks = load_index(index_path)
    client = connect(path)
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=vectors.shape[1], distance=models.Distance.COSINE))
    for start in range(0, len(chunks), 256):
        client.upsert(COLLECTION, points=[models.PointStruct(id=point_id(chunks[i]["id"]), vector=vectors[i].tolist(), payload=chunks[i])
                                          for i in range(start, min(start + 256, len(chunks)))])
    print(f"Qdrant: {URL or path}   (точек {client.count(COLLECTION).count})")
    client.close()


def scroll_all(client):
    points, offset = [], None
    while True:
        batch, offset = client.scroll(COLLECTION, limit=1000, offset=offset, with_payload=True, with_vectors=False)
        points += batch
        if offset is None:
            return points


if __name__ == "__main__":
    build()
