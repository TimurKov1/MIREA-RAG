"""
compare_answers.py — сравнение нескольких прогонов eval.run_answers.

Запуск из корня проекта:
    python -m eval.compare_answers                                   # предыдущий и последний прогоны
    python -m eval.compare_answers a.jsonl b.jsonl c.jsonl …         # любые прогоны; первый — базовый
Печатает таблицу по группам вопросов (балл судьи, доля верных, слова, источник, время) и пишет её вместе со списком вопросов,
где оценка отличается от базового прогона, в eval/results/compare.md.
"""
import json
import os
import sys

from eval.run_answers import AGGREGATION

DEFAULT = ["eval/results/answers_prev.jsonl", "eval/results/answers.jsonl"]
REPORT = "eval/results/compare.md"


def load(path):
    with open(path, encoding="utf-8") as f:
        return {r["id"]: r for r in (json.loads(line) for line in f if line.strip())}


def group_of(r):
    if not r["answerable"]:
        return "без ответа"
    return "агрегации" if r["category"] in AGGREGATION else "обычные"


def stats(rows):
    scores = [r["score"] for r in rows if r.get("score") is not None]
    share = lambda key: (lambda xs: f"{sum(xs) / len(xs):.0%}" if xs else "—")([r[key] for r in rows if r.get(key) is not None])
    seconds = [r["seconds"] for r in rows if r.get("seconds") is not None]
    return (f"{sum(scores) / len(scores):.2f}" if scores else "—", f"{sum(s == 2 for s in scores) / len(scores):.0%}" if scores else "—",
            share("words_ok"), share("source_ok"), f"{sum(seconds) / len(seconds):.1f}" if seconds else "—")


def main():
    paths = sys.argv[1:] or DEFAULT
    runs = {os.path.basename(p).removeprefix("answers_").removesuffix(".jsonl"): load(p) for p in paths}
    names = list(runs)
    base = runs[names[0]]
    ids = [i for i in base if all(i in r for r in runs.values())]
    lines = [f"# Сравнение прогонов (базовый: {names[0]})", "",
             "Для каждой группы: средний балл судьи (из 2) / доля верных / слова / источник / среднее время на вопрос, с.", "",
             "| Группа | Вопросов | " + " | ".join(names) + " |", "|---|---:|" + "---|" * len(names)]
    for group in ("обычные", "агрегации", "без ответа", "все"):
        part = [i for i in ids if group == "все" or group_of(base[i]) == group]
        cells = [" / ".join(stats([runs[n][i] for i in part])) for n in names]
        lines.append(f"| {group} | {len(part)} | " + " | ".join(cells) + " |")
    for n in names[1:]:
        better = sum(1 for i in ids if (runs[n][i]["score"] or 0) > (base[i]["score"] or 0))
        worse = sum(1 for i in ids if (runs[n][i]["score"] or 0) < (base[i]["score"] or 0))
        lines.append(f"\n{n} относительно {names[0]}: лучше у {better} вопросов, хуже у {worse}.")
    lines += ["", "## Вопросы, где оценка отличается от базового прогона", ""]
    for i in ids:
        scores = {n: runs[n][i]["score"] for n in names}
        if len(set(scores.values())) > 1:
            lines.append(f"- **{i}** ({base[i]['set']}, {base[i]['category']}) {base[i]['question'][:80]}: " + ", ".join(f"{n} {s}" for n, s in scores.items()))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:12 + len(names)]) + f"\n\nСохранено: {REPORT}")


if __name__ == "__main__":
    main()
