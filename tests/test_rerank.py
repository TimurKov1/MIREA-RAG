import rag.rerank as rerank_module
from rag.rerank import rerank

RESULTS = [{"chunk": {"text": f"фрагмент {i}"}} for i in range(1, 7)]


def ids(results):
    return [r["chunk"]["text"][-1] for r in results]


def test_model_order_is_applied_and_rest_follows(monkeypatch):
    monkeypatch.setattr(rerank_module, "chat", lambda *a, **k: "3, 1")
    assert ids(rerank("в", RESULTS, top=4)) == ["3", "1", "2", "4"]


def test_garbage_and_duplicates_are_ignored(monkeypatch):
    monkeypatch.setattr(rerank_module, "chat", lambda *a, **k: "номера: 2, 2, 99, 0")
    assert ids(rerank("в", RESULTS, top=3)) == ["2", "1", "3"]


def test_model_failure_keeps_search_order(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("API недоступен")
    monkeypatch.setattr(rerank_module, "chat", boom)
    assert ids(rerank("в", RESULTS, top=3)) == ["1", "2", "3"]


def test_single_result_skips_the_model(monkeypatch):
    monkeypatch.setattr(rerank_module, "chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("модель не нужна")))
    assert rerank("в", RESULTS[:1]) == RESULTS[:1]


def test_prompt_contains_only_snippet(monkeypatch):
    sent = []
    monkeypatch.setattr(rerank_module, "chat", lambda messages, **k: (sent.append(messages), "1")[1])
    rerank("в", [{"chunk": {"text": "а" * 5000}}, {"chunk": {"text": "б"}}])
    assert "а" * rerank_module.SNIPPET in sent[0][1]["content"] and "а" * (rerank_module.SNIPPET + 1) not in sent[0][1]["content"]
