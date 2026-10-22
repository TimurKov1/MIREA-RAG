"""
Трассировка в Langfuse (необязательная). Включается, если заданы LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY (и LANGFUSE_HOST, если сервер свой);
без них все функции ниже ничего не делают, а пакет langfuse можно не устанавливать, так что поведение проекта не меняется.

Что получается в Langfuse на каждый вопрос: дерево шагов respond → agent → (rewrite, search, rerank, вызовы инструментов, вызовы модели) с входами, выходами,
токенами, временем и стоимостью. Вызовы модели и эмбеддинги записывает обёртка langfuse.openai (см. rag/llm.py), остальные шаги отмечены декоратором observe.
"""
import os
from contextlib import nullcontext

ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))

if ENABLED:
    from langfuse import get_client, observe, propagate_attributes
else:
    def observe(func=None, **_):
        """Заглушка: поддерживает и @observe, и @observe(name=...)."""
        return func if func is not None else (lambda f: f)


def session(user, name):
    """Контекст, в котором все шаги записываются на пользователя (user_id) и диалог (session_id) и получают имя трассы."""
    return propagate_attributes(user_id=str(user), session_id=str(user), trace_name=name) if ENABLED else nullcontext()


def annotate(**fields):
    """Дописывает вход, выход и метаданные к текущему шагу (корню трассы)."""
    if ENABLED:
        get_client().update_current_span(**fields)


def score(trace_id, name, value, comment=None):
    """Оценка трассы (например, 👍/👎 пользователя): число 1 или 0."""
    if ENABLED:
        get_client().create_score(trace_id=trace_id, name=name, value=value, comment=comment)


def current_trace_id():
    return get_client().get_current_trace_id() if ENABLED else None


def flush():
    """Отправляет накопленные записи: нужно перед выходом короткого скрипта; бот отправляет сам в фоне."""
    if ENABLED:
        get_client().flush()
