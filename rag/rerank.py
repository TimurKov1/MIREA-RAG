import os
import re

from rag.llm import chat
from rag.tracing import observe

RERANK_MODEL = os.environ.get("RERANK_MODEL") or "mistralai/mistral-small-24b-instruct-2501"
SNIPPET = 1000

SYSTEM = """Ты помогаешь искать ответ на вопрос абитуриента по фрагментам материалов приёмной комиссии вуза.
Тебе даны вопрос и пронумерованные фрагменты. Выбери фрагменты, в которых есть сама информация, нужная для ответа (а не просто похожая тема), и расположи их от самого полезного к менее полезному.
Если вопрос просит перечень (программ, направлений), самый полезный фрагмент — тот, который содержит такой перечень.
Выведи только номера через запятую, не больше {top}."""


@observe(name="rerank", capture_input=False)
def rerank(query, results, top=5, model=RERANK_MODEL):
    if len(results) <= 1:
        return results[:top]
    body = "\n\n".join(f"[{i}] {r['chunk']['text'][:SNIPPET]}" for i, r in enumerate(results, 1))
    try:
        out = chat([{"role": "system", "content": SYSTEM.format(top=top)},
                    {"role": "user", "content": f"Вопрос: {query}\n\nФрагменты:\n\n{body}"}], model=model)
    except Exception:
        return results[:top]
    order = []
    for n in re.findall(r"\d+", out):
        i = int(n) - 1
        if 0 <= i < len(results) and i not in order:
            order.append(i)
    order += [i for i in range(len(results)) if i not in order]
    return [results[i] for i in order[:top]]
