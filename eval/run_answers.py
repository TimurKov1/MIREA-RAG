"""
run_answers.py — оценка ответов бота целиком (поиск, реранкер, генерация) на eval/golden_v2.jsonl.

Для каждого вопроса бот отвечает так же, как в Telegram (с историей для уточняющих вопросов). Дальше две проверки:
  1. Без модели:
       слова      все ли строки из must_contain есть в ответе (пробелы не важны: «379 000» и «379000» одно и то же)
       источник   ссылается ли ответ хотя бы на одну из нужных страниц (source_files)
  2. Модель-судья сравнивает ответ с эталоном и ставит 2 (верно), 1 (частично) или 0 (неверно).
     Для вопросов без ответа (answerable = false) верно — вежливый отказ без выдумок и без выполнения просьбы.

Нужна переменная окружения LLM_API_KEY и загруженная коллекция Qdrant. Запуск из корня проекта:
    python -m eval.run_answers                  # все вопросы
    python -m eval.run_answers v1 v2_new        # только выбранные наборы (v1, v2_new, para, inst)
    python -m eval.run_answers --only g046,x08  # только перечисленные вопросы по id; результат в answers_only.* (полный прогон не затрагивается)
    python -m eval.run_answers --resume         # дозапустить только вопросы, которые в прошлом прогоне закончились ошибкой или не были обработаны
    python -m eval.run_answers --rejudge        # заново оценить уже сохранённые ответы (после правки промпта судьи), бот не вызывается
Результат: eval/results/answers.md (сводка и список неверных ответов) и answers.jsonl (все ответы с оценками); прежний результат сохраняется как answers_prev.*.
Сравнить два прогона: python -m eval.compare_answers.
"""
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from eval.run_retrieval import AGGREGATION, GOLD
from rag.ask import K, answer, plain
from rag.llm import LLM_EXTRA, LLM_MODEL, USAGE, chat
from rag.search import Searcher

JUDGE_MODEL = LLM_MODEL
WORKERS = 4

JUDGE_SYSTEM = """Ты проверяешь ответ помощника приёмной комиссии вуза. Тебе даны вопрос, эталон и ответ помощника. Сравни ответ с эталоном по смыслу. Номера в квадратных скобках ([1], [2]) — это ссылки на источники, их не оценивай.

Если на вопрос есть ответ (эталон содержит факты):
2 — ответ верный: все существенные факты и числа из эталона есть и не противоречат ему; лишние подробности допустимы, если они не противоречат эталону.
1 — частично верный: часть фактов верна, но чего-то существенного не хватает или есть неточность, при этом нет грубой ошибки и выдумки.
0 — неверный: число или факт неверны, ответ противоречит эталону, выдуман, или помощник отказался отвечать, хотя эталон содержит ответ.
Правила оценки:
- Лишняя информация (о других уровнях образования, этапах, подробности) не снижает оценку, если в ответе есть все факты эталона. Не считай сведения выдуманными или неверными только потому, что их нет в эталоне (помощник берёт их из источников): снижай оценку, лишь когда они прямо противоречат эталону.
- Оценивай только то, о чём спросили в вопросе. Если эталон содержит больше, чем нужно для ответа на этот вопрос (например, платные места, когда спрашивали про бюджет), недостающее лишнее не считай ошибкой.
- Если в эталоне не названо, о каком годе речь, не придирайся к году в ответе: данные «прошлого года» на сайте могут быть помечены текущим годом.

Если на вопрос ответа нет (эталон описывает ожидаемое поведение):
2 — помощник вежливо отказал или сказал, что данных нет, ничего не выдумал и не выполнил просьбу.
0 — помощник выдумал ответ или выполнил то, что просили (шутка, роль, запись и т. п.).

Верни только JSON одной строкой: {"score": 0, "reason": "одно предложение"}"""


def squash(text):
    return re.sub(r"\s+", "", text).lower()


def judge(g, text):
    kind = "на вопрос ЕСТЬ ответ (отказ отвечать — ошибка)" if g["answerable"] else "на вопрос ответа НЕТ (правильно — отказать)"
    prompt = f"Тип вопроса: {kind}\n\nВопрос: {g['question']}\n\nЭталон: {g['answer']}\n\nОтвет помощника: {text}"
    out = chat([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}], model=JUDGE_MODEL, extra=LLM_EXTRA)
    m = re.search(r"\{.*\}", out, re.S)
    try:
        data = json.loads(m.group(0))
        return int(data["score"]), str(data.get("reason", ""))
    except (AttributeError, ValueError, KeyError, TypeError):
        return None, f"не разобран ответ судьи: {out[:100]}"


def evaluate(searcher, g, k):
    started = time.time()
    record = {"id": g["id"], "set": g.get("set", "v1"), "category": g["category"], "answerable": g["answerable"], "question": g["question"]}
    try:
        text, sources, query = answer(searcher, g["question"], g["history"], k=k)
        text = plain(text)
        score, reason = judge(g, text)
    except Exception as e:
        return {**record, "error": repr(e), "score": None}
    files = {f.split("/")[-1] for f in g["source_files"]}
    cited = {chunk_id.split("#")[0].split("/")[-1] for s in sources for chunk_id in s["chunk_ids"]}
    return {**record, "tools": getattr(sources, "tool_calls", None), "answer": text, "query": query, "score": score, "reason": reason, "seconds": round(time.time() - started, 1),
            "words_ok": all(squash(w) in squash(text) for w in g["must_contain"]) if g["must_contain"] else None,
            "source_ok": bool(files & cited) if files else None}


def group_of(r):
    if not r["answerable"]:
        return "без ответа"
    return "агрегации" if r["category"] in AGGREGATION else "обычные"


def summarize(rows):
    scored = [r["score"] for r in rows if r.get("score") is not None]
    flag = lambda key: [r[key] for r in rows if r.get(key) is not None]
    share = lambda xs: f"{sum(xs) / len(xs):.0%}" if xs else "—"
    return (len(rows), f"{sum(scored) / len(scored):.2f}" if scored else "—", share([s == 2 for s in scored]), share([s == 0 for s in scored]),
            share(flag("words_ok")), share(flag("source_ok")), sum(1 for r in rows if r.get("error")))


def main():
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else set()
    wanted = {a for a in sys.argv[1:] if not a.startswith("--") and "," not in a and a not in only}
    mode = "answers_only" if only else "answers"
    REPORT, DETAILS = f"eval/results/{mode}.md", f"eval/results/{mode}.jsonl"
    with open(GOLD, encoding="utf-8") as f:
        gold = [json.loads(line) for line in f if line.strip()]
    gold = [g for g in gold if (not wanted or g.get("set", "v1") in wanted) and (not only or g["id"] in only)]

    started, rows = time.time(), []
    kept = {}
    if "--resume" in sys.argv and os.path.exists(DETAILS):
        with open(DETAILS, encoding="utf-8") as f:
            kept = {r["id"]: r for r in (json.loads(line) for line in f if line.strip()) if not r.get("error")}
        print(f"Из прошлого прогона берём {len(kept)} готовых ответов, остальные считаем заново")
    if "--rejudge" in sys.argv:
        with open(DETAILS, encoding="utf-8") as f:
            saved = {r["id"]: r for r in (json.loads(line) for line in f if line.strip())}

        def rejudge(g):
            r = dict(saved[g["id"]])
            if r.get("answer") is not None:
                r["score"], r["reason"] = judge(g, r["answer"])
            return r
        job = rejudge
    else:
        searcher = Searcher()
        job = lambda g: evaluate(searcher, g, K)
    todo = [g for g in gold if g["id"] not in kept and ("--rejudge" not in sys.argv or g["id"] in saved)]
    with ThreadPoolExecutor(WORKERS) as pool:
        for r in pool.map(job, todo):
            kept[r["id"]] = r
            print(f"\r  {len(kept)}/{len(gold)}", end="", flush=True)
    print()
    rows = [kept[g["id"]] for g in gold if g["id"] in kept]

    lines = [f"# Оценка ответов ({time.strftime('%Y-%m-%d %H:%M')})", "",
             f"Вопросов: {len(rows)}, модель ответов: {LLM_MODEL}, судья: {JUDGE_MODEL}, источников в контексте: {K}. Время {time.time() - started:.0f} с, "
             f"токенов {USAGE['prompt_tokens']} на входе и {USAGE['completion_tokens']} на выходе.", "",
             "Судья: 2 — верно, 1 — частично, 0 — неверно (средний балл из 2). «Слова» — доля ответов, где есть все строки из must_contain; "
             "«источник» — доля ответов, ссылающихся на нужную страницу.", "",
             "| Набор: группа | Вопросов | Судья (из 2) | Верных | Неверных | Слова | Источник | Ошибок |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for st in sorted({r["set"] for r in rows}) + ["все"]:
        for group in ("обычные", "агрегации", "без ответа"):
            part = [r for r in rows if (st == "все" or r["set"] == st) and group_of(r) == group]
            if part:
                lines.append(f"| {st}: {group} | " + " | ".join(str(x) for x in summarize(part)) + " |")
    bad = [r for r in rows if r.get("score") == 0 or r.get("error")]
    lines += ["", f"## Неверные ответы и ошибки ({len(bad)})", ""]
    for r in bad:
        lines += [f"**{r['id']}** ({r['set']}, {r['category']}): {r['question']}", "",
                  f"- ответ: {(r.get('answer') or r.get('error', ''))[:500].replace(chr(10), ' ')}", f"- судья: {r.get('reason', '')}", ""]
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    for path in (REPORT, DETAILS):
        if os.path.exists(path):
            shutil.copyfile(path, path.replace(".", "_prev.", 1) if "/" not in os.path.basename(path) else os.path.join(os.path.dirname(path), os.path.basename(path).replace(".", "_prev.", 1)))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(DETAILS, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("\n".join(lines[:24]) + f"\n\nСохранено: {REPORT}, {DETAILS}")


if __name__ == "__main__":
    main()
