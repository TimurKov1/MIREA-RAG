from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rag import web


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web, "Searcher", lambda: SimpleNamespace(
        client=SimpleNamespace(close=lambda: None),
    ))
    with TestClient(web.app, base_url="https://university.test") as client:
        yield client


def test_page_and_static_files(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "./api/chat" in client.get("/static/app.js").text
    assert "Помощник абитуриента" in client.get("/").text


def test_chat_history_reset_and_sources(client, monkeypatch):
    seen = []

    def respond(searcher, user, question, history):
        seen.append((user, question, list(history)))
        return "**Ответ**", [
            {"title": "Документы", "place": "https://www.mirea.ru/"},
            {"title": "Справка", "place": "javascript:alert(1)"},
        ], question

    monkeypatch.setattr(web, "respond", respond)
    endpoint = "/api/chat"
    response = client.post(endpoint, json={"question": "  Условия поступления? "})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.json() == {
        "answer": "Ответ",
        "sources": [
            {"title": "Документы", "url": "https://www.mirea.ru/"},
            {"title": "Справка", "url": None},
        ],
    }
    for _ in range(web.HISTORY_TURNS + 1):
        assert client.post(endpoint, json={"question": "А сроки?"}).status_code == 200
    assert seen[0][1] == "Условия поступления?"
    assert seen[1][2] == [
        {"role": "user", "content": "Условия поступления?"},
        {"role": "assistant", "content": "Ответ"},
    ]
    assert len(seen[-1][2]) == web.HISTORY_TURNS * 2
    assert client.delete(endpoint).status_code == 204
    assert client.post(endpoint, json={"question": "Какие документы?"}).status_code == 200
    assert seen[0][0] == seen[-1][0]
    assert seen[-1][2] == []


@pytest.mark.parametrize("question", ["  ", "а" * 501])
def test_validates_question(client, question):
    assert client.post("/api/chat", json={"question": question}).status_code == 422


def test_overlapping_requests(client, monkeypatch):
    entered, release = Event(), Event()

    def respond(searcher, user, question, history):
        if question == "Долгий запрос":
            entered.set()
            assert release.wait(timeout=5)
        return "Ответ", [], question

    monkeypatch.setattr(web, "MAX_IN_FLIGHT", 1)
    monkeypatch.setattr(web, "respond", respond)
    endpoint = "/api/chat"
    assert client.post(endpoint, json={"question": "Первый вопрос"}).status_code == 200
    cookie = client.cookies[web.COOKIE]
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(client.post, endpoint, json={"question": "Долгий запрос"})
        try:
            assert entered.wait(timeout=5)
            assert client.post(endpoint, json={"question": "Повтор"}).status_code == 409
            assert client.delete(endpoint).status_code == 409
            response = client.post(
                endpoint, json={"question": "Другой диалог"},
                headers={"Cookie": f"{web.COOKIE}=different-session"},
            )
            assert response.status_code == 429
            assert response.headers["retry-after"] == "5"
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert client.cookies[web.COOKIE] == cookie


def test_expired_session_releases_slot(client, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(web, "MAX_SESSIONS", 1)
    monkeypatch.setattr(web, "monotonic", lambda: clock[0])
    monkeypatch.setattr(web, "respond", lambda *args: ("Ответ", [], "Запрос"))
    endpoint = "/api/chat"

    assert client.post(endpoint, json={"question": "Запрос"}).status_code == 200
    previous = client.cookies[web.COOKIE]
    client.cookies.clear()
    assert client.post(endpoint, json={"question": "Запрос"}).status_code == 503
    clock[0] += web.SESSION_TTL
    assert client.post(
        endpoint, json={"question": "Запрос"},
        headers={"Cookie": f"{web.COOKIE}={previous}"},
    ).status_code == 200
    assert client.cookies[web.COOKIE] != previous
