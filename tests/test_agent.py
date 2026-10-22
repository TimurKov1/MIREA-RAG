import json
from types import SimpleNamespace

import pytest

import rag.agent as agent

PAGE = {"score": 1, "chunk": {"id": "pages/a.md#0", "title": "Страница", "url": "https://u", "text": "[x] Страница\n\nтекст страницы"}}
SEARCHER = SimpleNamespace(search=lambda q, k=10: [PAGE])


def tool_call(name, args, call_id="c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def message(content="", calls=None):
    return SimpleNamespace(content=content, tool_calls=calls or [])


@pytest.fixture(autouse=True)
def stubs(tools, monkeypatch):
    monkeypatch.setattr(agent, "rewrite", lambda q, h: q)
    monkeypatch.setattr(agent, "rerank", lambda q, results, top=5: results[:top])
    monkeypatch.setattr(agent, "Tools", lambda search_docs=None: tools)


def script(monkeypatch, *messages):
    queue = list(messages)
    seen = []
    monkeypatch.setattr(agent, "chat_tools", lambda msgs, schemas: (seen.append(list(msgs)), queue.pop(0))[1])
    return seen


def test_answer_without_tools(monkeypatch):
    script(monkeypatch, message("Ответ по источнику [1]."))
    text, cited, query = agent.agent_answer(SEARCHER, "вопрос")
    assert text.startswith("Ответ по источнику") and cited.tool_calls == [] and len(cited) == 1


def test_tool_call_result_goes_back_to_model(monkeypatch):
    seen = script(monkeypatch, message(calls=[tool_call("count_programs", {"level": "магистратура"})]), message("Программ магистратуры: 1."))
    text, cited, _ = agent.agent_answer(SEARCHER, "сколько программ в магистратуре")
    assert cited.tool_calls == ["count_programs"] and text == "Программ магистратуры: 1."
    tool_messages = [m for m in seen[1] if m["role"] == "tool"]
    assert len(tool_messages) == 1 and "Программ с такими условиями: 1." in tool_messages[0]["content"]


def test_tool_errors_are_returned_to_model_not_raised(monkeypatch):
    seen = script(monkeypatch, message(calls=[tool_call("find_programs", {"limit": 999})]), message("Уточните запрос."))
    agent.agent_answer(SEARCHER, "вопрос")
    assert "Ошибка в аргументах" in [m for m in seen[1] if m["role"] == "tool"][0]["content"]


def test_step_limit_forces_final_answer(monkeypatch):
    calls = [message(calls=[tool_call("count_programs", {}, f"c{i}")]) for i in range(agent.MAX_STEPS)]
    script(monkeypatch, *calls)
    monkeypatch.setattr(agent, "chat", lambda msgs, **k: "Итоговый ответ по собранному.")
    text, cited, _ = agent.agent_answer(SEARCHER, "вопрос")
    assert text == "Итоговый ответ по собранному." and len(cited.tool_calls) == agent.MAX_STEPS


def test_greeting_instead_of_answer_is_rejected(monkeypatch):
    script(monkeypatch, message("Здравствуйте! Чем могу помочь?"))
    with pytest.raises(RuntimeError):
        agent.agent_answer(SEARCHER, "сколько мест на программе")


def test_greeting_in_question_is_allowed(monkeypatch):
    script(monkeypatch, message("Здравствуйте! Чем могу помочь?"))
    text, _, _ = agent.agent_answer(SEARCHER, "Здравствуйте")
    assert text.startswith("Здравствуйте")
