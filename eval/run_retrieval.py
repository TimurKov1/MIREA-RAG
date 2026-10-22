"""
run_retrieval.py — оценка поиска на тестовом наборе (без языковой модели: проверяем только сам поиск).

Фрагмент считается правильным, если он из нужного файла и содержит цитату-доказательство (evidence) из
eval/golden.jsonl (сравнение без учёта пробелов). Метрики:
    R@k   доля вопросов, у которых правильный фрагмент попал в первые k результатов
    MRR   среднее 1/место первого правильного фрагмента (1 — первое место, 0,5 — второе, 0 — не нашли)

Вопросы-подсчёты и перечни (категории number, filter, list) требуют сбора ответа из многих документов,
поэтому они показаны отдельной строкой и в «обычные» не входят (так показатели по обычным вопросам остаются сравнимыми).

Запуск из корня проекта:   python -m eval.run_retrieval
Результат: таблица в консоли и в файле eval/results/retrieval.md (перезаписывается).
"""
import json
import os
import re
import time
from collections import defaultdict

from rag.search import ALPHA, Searcher

GOLD = "eval/golden_v3.jsonl"
REPORT = "eval/results/retrieval_v2.md"
AGGREGATION = {"number", "filter", "list", "inst_programs", "inst_list"}
DEPTH = 200


def squash(text):
    return re.sub(r"\s+", " ", text).strip()


def is_relevant(chunk, question):
    files = {f.split("/")[-1] for f in question["source_files"]}
    if chunk["id"].split("#")[0].split("/")[-1] not in files:
        return False
    return any(squash(e) in squash(chunk["text"]) for e in question["evidence"])


def first_place(results, question):
    """Место первого правильного фрагмента (с 1) или None."""
    for place, r in enumerate(results, 1):
        if is_relevant(r["chunk"], question):
            return place
    return None


def summarize(places):
    n = len(places)
    ok = lambda k: sum(1 for p in places if p and p <= k) / n
    return {"n": n, "r1": ok(1), "r5": ok(5), "r10": ok(10), "mrr": sum(1 / p for p in places if p) / n}


def main():
    with open(GOLD, encoding="utf-8") as f:
        gold = [json.loads(line) for line in f if line.strip()]
    questions = [g for g in gold if g["answerable"] and g["category"] != "followup"]

    report = REPORT
    searcher = Searcher()
    places = [first_place(searcher.search(g["question"], k=DEPTH), g) for g in questions]

    by_category = defaultdict(list)
    for p, g in zip(places, questions):
        by_category[g["category"]].append(p)
    normal = [p for c, ps in by_category.items() if c not in AGGREGATION for p in ps]
    aggregation = [p for c, ps in by_category.items() if c in AGGREGATION for p in ps]

    rows = [("обычные (всего)", summarize(normal))]
    rows += [(c, summarize(ps)) for c, ps in sorted(by_category.items()) if c not in AGGREGATION]
    rows += [("агрегации (всего)", summarize(aggregation))]
    rows += [(c, summarize(ps)) for c, ps in sorted(by_category.items()) if c in AGGREGATION]

    sets = sorted({g.get("set", "v1") for g in questions})
    set_rows = []
    for name in sets:
        for group, in_group in (("обычные", lambda g: g["category"] not in AGGREGATION), ("агрегации", lambda g: g["category"] in AGGREGATION)):
            ps = [p for p, g in zip(places, questions) if g.get("set", "v1") == name and in_group(g)]
            if ps:
                set_rows.append((f"{name}: {group}", summarize(ps)))

    print(f"Вопросов: {len(questions)}, фрагментов в индексе: {len(searcher.chunks)}, alpha = {ALPHA}\n")
    print(f"{'категория':22} {'n':>3} {'R@1':>5} {'R@5':>5} {'R@10':>5} {'MRR':>5}")
    for name, s in rows:
        print(f"{name:22} {s['n']:3} {s['r1']:5.2f} {s['r5']:5.2f} {s['r10']:5.2f} {s['mrr']:5.2f}")

    print("\nПо наборам вопросов:")
    for name, s in set_rows:
        print(f"{name:22} {s['n']:3} {s['r1']:5.2f} {s['r5']:5.2f} {s['r10']:5.2f} {s['mrr']:5.2f}")

    lines = [f"# Оценка поиска ({time.strftime('%Y-%m-%d %H:%M')})", "",
             f"Гибридный поиск (alpha = {ALPHA}), фрагментов в индексе: {len(searcher.chunks)}, вопросов с ответом: {len(questions)}.", "",
             "| Категория | Вопросов | R@1 | R@5 | R@10 | MRR |", "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| {name} | {s['n']} | {s['r1']:.2f} | {s['r5']:.2f} | {s['r10']:.2f} | {s['mrr']:.2f} |" for name, s in rows]
    lines += ["", "По наборам вопросов (v1 — исходные, v2_new — добавленные, para — перефразировки):", "",
              "| Набор: группа | Вопросов | R@1 | R@5 | R@10 | MRR |", "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| {name} | {s['n']} | {s['r1']:.2f} | {s['r5']:.2f} | {s['r10']:.2f} | {s['mrr']:.2f} |" for name, s in set_rows]
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nСохранено: {report}")


if __name__ == "__main__":
    main()
