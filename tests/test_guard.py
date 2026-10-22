import json

import pytest

from rag import guard


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    guard._by_user.clear()
    guard._all.clear()
    monkeypatch.setattr(guard, "LOG_PATH", str(tmp_path / "logs" / "qa.jsonl"))


def test_normal_question_passes():
    assert guard.check("u1", "сколько мест?") is None


def test_too_long_question_is_blocked():
    assert "слишком длинный" in guard.check("u1", "а" * (guard.MAX_QUESTION_CHARS + 1))


def test_per_minute_limit():
    results = [guard.check("u1", "вопрос") for _ in range(guard.PER_MINUTE + 1)]
    assert results[:-1] == [None] * guard.PER_MINUTE and "Подождите минуту" in results[-1]
    assert guard.check("u2", "вопрос") is None


def test_per_minute_window_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(guard.time, "time", lambda: now[0])
    for _ in range(guard.PER_MINUTE):
        assert guard.check("u1", "вопрос") is None
    assert guard.check("u1", "вопрос") is not None
    now[0] += 61
    assert guard.check("u1", "вопрос") is None


def test_daily_limits(monkeypatch):
    monkeypatch.setattr(guard, "PER_MINUTE", 10**6)
    monkeypatch.setattr(guard, "PER_DAY", 3)
    assert [guard.check("u1", "в") for _ in range(3)] == [None] * 3
    assert "дневной лимит" in guard.check("u1", "в")
    monkeypatch.setattr(guard, "TOTAL_PER_DAY", 4)
    assert guard.check("u2", "в") is None
    assert "лимит запросов к помощнику исчерпан" in guard.check("u3", "в")


def test_log_writes_json_lines(tmp_path):
    guard.log(user="u1", question="вопрос", answer="ответ")
    guard.log(user="u2", question="второй", blocked="лимит")
    lines = (tmp_path / "logs" / "qa.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert records[0]["answer"] == "ответ" and records[1]["blocked"] == "лимит" and "time" in records[0]
