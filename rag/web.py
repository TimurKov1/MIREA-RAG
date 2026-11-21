"""Канал веб-чата для существующего ассистента."""
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_urlsafe
from time import monotonic
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from rag import guard
from rag.ask import HISTORY_TURNS, plain, respond
from rag.search import Searcher

STATIC = Path(__file__).with_name("web_static")
COOKIE = "mirea_chat"
SESSION_TTL = 30 * 60
MAX_SESSIONS = 256
MAX_IN_FLIGHT = 2


@dataclass
class Session:
    touched: float
    history: list[dict[str, str]] = field(default_factory=list)
    busy: bool = False


class Question(BaseModel):
    question: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1, max_length=guard.MAX_QUESTION_CHARS,
    )]


class Source(BaseModel):
    title: str
    url: str | None


class ChatAnswer(BaseModel):
    answer: str
    sources: list[Source]


def source_url(place):
    try:
        parsed = urlsplit(place)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return place
    except ValueError:
        pass
    return None


@asynccontextmanager
async def lifespan(app):
    searcher = await run_in_threadpool(Searcher)
    app.state.searcher = searcher
    app.state.sessions = {}
    app.state.in_flight = 0
    try:
        yield
    finally:
        for key in app.state.sessions:
            guard.forget_user(key)
        await run_in_threadpool(searcher.client.close)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatAnswer)
async def chat(
    question: Question,
    response: Response,
    session_key: Annotated[str | None, Cookie(alias=COOKIE)] = None,
):
    state = app.state
    now = monotonic()
    for key, item in list(state.sessions.items()):
        if not item.busy and now - item.touched >= SESSION_TTL:
            del state.sessions[key]
            guard.forget_user(key)

    session = state.sessions.get(session_key)
    if session is not None and session.busy:
        raise HTTPException(409, "В предыдущем запросе ещё готовится ответ.")
    if state.in_flight >= MAX_IN_FLIGHT:
        raise HTTPException(429, "Помощник занят. Повторите запрос через несколько секунд.",
                            headers={"Retry-After": "5"})
    if session is None:
        if len(state.sessions) >= MAX_SESSIONS:
            raise HTTPException(503, "Слишком много открытых диалогов. Попробуйте позже.")
        session_key = token_urlsafe(32)
        session = state.sessions[session_key] = Session(now)

    session.busy = True
    state.in_flight += 1
    try:
        text, found, _ = await run_in_threadpool(
            respond, state.searcher, session_key, question.question, session.history,
        )
        answer = plain(text)
        session.history.extend([
            {"role": "user", "content": question.question},
            {"role": "assistant", "content": answer},
        ])
        del session.history[:-2 * HISTORY_TURNS]
    finally:
        session.touched = monotonic()
        session.busy = False
        state.in_flight -= 1

    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE, session_key, max_age=SESSION_TTL, httponly=True,
        secure=os.getenv("WEB_COOKIE_SECURE", "1") != "0", samesite="lax",
    )
    return ChatAnswer(
        answer=answer,
        sources=[Source(title=source["title"], url=source_url(source["place"])) for source in found],
    )


@app.delete("/api/chat", status_code=204)
async def reset_chat(session_key: Annotated[str | None, Cookie(alias=COOKIE)] = None):
    session = app.state.sessions.get(session_key)
    if session and session.busy:
        raise HTTPException(409, "Дождитесь ответа перед очисткой диалога.")
    if session:
        session.history.clear()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
