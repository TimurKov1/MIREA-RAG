import json

import numpy as np
import pytest

import rag.search as search
from rag.qdrant_store import build, connect, point_id
from rag.search import Searcher, minmax, tokenize

CHUNKS = [
    {"id": "pages/a.md#0", "url": "u1", "title": "Бюджет", "level": "бакалавриат", "text": "бюджетные места на программе анализ данных"},
    {"id": "pages/b.md#0", "url": "u2", "title": "Платное", "level": "магистратура", "text": "стоимость платного обучения в магистратуре"},
    {"id": "pages/c.md#0", "url": "u3", "title": "Код", "level": "бакалавриат", "text": "направление 09.03.04 программная инженерия"},
]
VECTORS = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)


def test_tokenize_keeps_codes_and_stems():
    assert "09.03.04" in tokenize("направление 09.03.04")
    assert tokenize("программы") == tokenize("программа")


def test_minmax_range():
    x = minmax(np.array([3.0, 1.0, 2.0]))
    assert x.min() == pytest.approx(0, abs=1e-6) and x.max() == pytest.approx(1, abs=1e-6)


def test_point_id_is_stable():
    assert point_id("pages/a.md#0") == point_id("pages/a.md#0") != point_id("pages/a.md#1")


@pytest.fixture
def searcher(tmp_path, monkeypatch):
    index = tmp_path / "index"
    index.mkdir()
    np.save(index / "vectors.npy", VECTORS)
    (index / "chunks.json").write_text(json.dumps(CHUNKS, ensure_ascii=False), encoding="utf-8")
    build(index_path=str(index), path=tmp_path / "qdrant")
    monkeypatch.setattr(search, "embed", lambda texts: np.array([QUERY["vector"]], dtype=np.float32))
    return Searcher(client=connect(tmp_path / "qdrant"))


QUERY = {"vector": [1, 0, 0]}


def test_searcher_loads_all_chunks(searcher):
    assert sorted(c["id"] for c in searcher.chunks) == sorted(c["id"] for c in CHUNKS)


def test_dense_part_decides(searcher):
    QUERY["vector"] = [0, 1, 0]
    assert searcher.search("что-то нейтральное", k=1)[0]["chunk"]["id"] == "pages/b.md#0"


def test_bm25_part_finds_code(searcher):
    QUERY["vector"] = [0.1, 0.1, 0.1]
    assert searcher.search("09.03.04", k=1)[0]["chunk"]["id"] == "pages/c.md#0"


def test_level_filter(searcher):
    QUERY["vector"] = [0, 1, 0]
    found = searcher.search("обучение", k=5, level="бакалавриат")
    assert found and {r["chunk"]["level"] for r in found} == {"бакалавриат"}
    assert searcher.search("обучение", k=5, level="аспирантура") == []
