import argparse
import json
import os
import time

import numpy as np

from rag.chunking import load_chunks
from rag.embed import embed

INDEX_PATH = os.environ.get("RAG_INDEX_PATH") or "index/api"


def to_record(c):
    return {"id": c.id, "url": c.url, "title": c.title, "level": c.level, "text": c.text}


def embed_records(records):
    print(f"{len(records)} фрагментов. Считаю векторы…")
    batch_size, parts, started = 32, [], time.time()
    for i in range(0, len(records), batch_size):
        parts.append(embed([r["text"] for r in records[i:i + batch_size]]))
        done = min(i + batch_size, len(records))
        elapsed = time.time() - started
        eta = elapsed / done * (len(records) - done)
        print(f"\r  {done}/{len(records)}   прошло {elapsed:.0f} с, осталось ~{eta:.0f} с   ", end="", flush=True)
    print()
    return np.vstack(parts)


def save_index(vectors, records, path):
    assert len(vectors) == len(records), "число векторов не равно числу фрагментов"
    os.makedirs(path, exist_ok=True)
    np.save(f"{path}/vectors.npy", vectors)
    with open(f"{path}/chunks.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    print(f"Готово: {path}   (фрагментов {len(records)}, векторы {vectors.shape}, {vectors.nbytes / 1e6:.1f} МБ)")


def build_index(path=INDEX_PATH):
    records = [to_record(c) for c in load_chunks()]
    save_index(embed_records(records), records, path)


def add_to_index(folder, path=INDEX_PATH):
    """Добавляет фрагменты папки с данными к индексу, не пересчитывая остальные. Старые фрагменты этой же папки заменяются,
    поэтому команду можно повторять после обновления страниц."""
    vectors, records = load_index(path)
    prefix = f"{os.path.basename(os.path.normpath(folder))}/"
    keep = [i for i, r in enumerate(records) if not r["id"].startswith(prefix)]
    if len(keep) < len(records):
        print(f"Заменяю {len(records) - len(keep)} прежних фрагментов папки {folder}")
    vectors, records = vectors[keep], [records[i] for i in keep]
    new = [to_record(c) for c in load_chunks(folder)]
    ids = {r["id"] for r in records}
    assert not any(r["id"] in ids for r in new), "id новых фрагментов совпадают с уже существующими"
    save_index(np.vstack([vectors, embed_records(new)]) if new else vectors, records + new, path)


def load_index(path=INDEX_PATH):
    vectors = np.load(f"{path}/vectors.npy")
    with open(f"{path}/chunks.json", encoding="utf-8") as f:
        chunks = json.load(f)
    assert len(vectors) == len(chunks), f"индекс {path} повреждён: {len(vectors)} векторов и {len(chunks)} фрагментов"
    return vectors, chunks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Сборка индекса (векторы и фрагменты в index/api). Без аргументов: полная пересборка. Дальше: python -m rag.qdrant_store, чтобы загрузить индекс в Qdrant.")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--reembed-from", metavar="CHUNKS_JSON", help="пересчитать векторы готовых фрагментов")
    actions.add_argument("--add", metavar="ПАПКА", help="добавить фрагменты папки с данными (например data/institutes) к индексу, остальное не пересчитывать")
    args = parser.parse_args()
    if args.reembed_from:
        with open(args.reembed_from, encoding="utf-8") as source:
            records = json.load(source)
        if not records:
            parser.error("список фрагментов пуст")
        save_index(embed_records(records), records, INDEX_PATH)
    elif args.add:
        add_to_index(args.add)
    else:
        build_index()
