import os
import re

from rag.tracing import ENABLED

if ENABLED:
    from langfuse.openai import OpenAI
else:
    from openai import OpenAI

API_URL = "https://polza.ai/api/v1"
API_KEY = os.environ.get("LLM_API_KEY")
LLM_MODEL = "deepseek/deepseek-v4-flash"
LLM_EXTRA = {"reasoning": {"enabled": False}}
MAX_ANSWER_TOKENS = 4000
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

_client = None


def client():
    global _client
    if _client is None:
        if not API_KEY:
            raise RuntimeError("Не задана переменная окружения LLM_API_KEY")
        _client = OpenAI(base_url=API_URL, api_key=API_KEY, max_retries=4)
    return _client


def chat(messages, temperature=0.0, timeout=300.0, model=None, extra=None):
    model = model or LLM_MODEL
    if extra is None:
        extra = LLM_EXTRA if model == LLM_MODEL else {}
    r = client().chat.completions.create(model=model, messages=messages, temperature=temperature, max_tokens=MAX_ANSWER_TOKENS,
                                         timeout=timeout, extra_body=extra or None)
    USAGE["calls"] += 1
    if r.usage:
        USAGE["prompt_tokens"] += r.usage.prompt_tokens
        USAGE["completion_tokens"] += r.usage.completion_tokens
    return re.sub(r"<think>.*?</think>", "", (r.choices[0].message.content or ""), flags=re.S).strip()


def chat_tools(messages, tools, temperature=0.0, timeout=300.0, model=None):
    """Запрос с инструментами: возвращает сообщение модели (в нём либо текст, либо tool_calls)."""
    model = model or LLM_MODEL
    r = client().chat.completions.create(model=model, messages=messages, tools=tools, temperature=temperature, max_tokens=MAX_ANSWER_TOKENS,
                                         timeout=timeout, extra_body=LLM_EXTRA if model == LLM_MODEL else None)
    USAGE["calls"] += 1
    if r.usage:
        USAGE["prompt_tokens"] += r.usage.prompt_tokens
        USAGE["completion_tokens"] += r.usage.completion_tokens
    return r.choices[0].message
