from types import SimpleNamespace

import rag.ask as ask
from rag.ask import format_sources, group_sources, plain, tidy

SOURCES = [{"title": f"Источник {n}", "place": f"https://x/{n}", "text": "", "chunk_ids": []} for n in (1, 2, 3)]


def test_tidy_renumbers_by_first_mention():
    text, cited = tidy("Первое [3]. Второе [1][3].", SOURCES)
    assert text == "Первое [1]. Второе [2][1]."
    assert [s["title"] for s in cited] == ["Источник 3", "Источник 1"]


def test_tidy_drops_unknown_numbers_and_duplicates():
    text, cited = tidy("Факт [1][1] и ещё [9].", SOURCES)
    assert "[9]" not in text and "[1][1]" not in text
    assert len(cited) == 1


def test_tidy_single_source_cited_once_at_end():
    text, cited = tidy("Раз [2]. Два [2]. Три [2].", SOURCES)
    assert text == "Раз. Два. Три. [1]" and len(cited) == 1


def test_tidy_without_references():
    assert tidy("В материалах нет информации.", SOURCES) == ("В материалах нет информации.", [])


def test_plain_removes_bold():
    assert plain("**Итого** 5 мест") == "Итого 5 мест"


def test_group_sources_merges_chunks_of_one_file_in_order():
    def r(cid, body):
        return {"chunk": {"id": cid, "title": "T", "url": "https://u", "text": f"[бакалавриат] T\n\n{body}"}}
    sources = group_sources([r("pages/a.md#2", "три"), r("pages/b.md#0", "другой"), r("pages/a.md#0", "один")])
    assert len(sources) == 2
    assert sources[0]["text"].endswith("три\n\nодин") is False and sources[0]["text"].index("один") < sources[0]["text"].index("три")
    assert sources[0]["chunk_ids"] == ["pages/a.md#0", "pages/a.md#2"]


def test_format_sources():
    assert format_sources(SOURCES[:1]) == "  [1] Источник 1 — https://x/1"


def test_rewrite_without_history_returns_question(monkeypatch):
    monkeypatch.setattr(ask, "chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("модель не нужна")))
    assert ask.rewrite("сколько мест?", []) == "сколько мест?"


def test_rewrite_uses_history_and_rejects_runaway_output(monkeypatch):
    history = [{"role": "user", "content": "про анализ данных"}, {"role": "assistant", "content": "29 мест"}]
    monkeypatch.setattr(ask, "chat", lambda *a, **k: "«Сколько платных мест на программе Анализ данных?»\nлишнее")
    assert ask.rewrite("а платных?", history) == "Сколько платных мест на программе Анализ данных?"
    monkeypatch.setattr(ask, "chat", lambda *a, **k: "x" * 5000)
    assert ask.rewrite("а платных?", history) == "а платных?"


def test_answer_falls_back_when_agent_fails(monkeypatch):
    import rag.agent
    monkeypatch.setattr(rag.agent, "agent_answer", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сбой")))
    monkeypatch.setattr(ask, "rewrite", lambda q, h: q)
    monkeypatch.setattr(ask, "rerank", lambda q, results, top=5: results[:top])
    monkeypatch.setattr(ask, "chat", lambda *a, **k: "Ответ по источнику [1].")
    searcher = SimpleNamespace(search=lambda q, k=10: [{"score": 1, "chunk": {"id": "pages/a.md#0", "title": "T", "url": "https://u", "text": "[x] T\n\nтекст"}}])
    text, cited, query = ask.answer(searcher, "вопрос")
    assert text.startswith("Ответ по источнику") and len(cited) == 1 and query == "вопрос"
