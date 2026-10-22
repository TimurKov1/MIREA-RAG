#!/usr/bin/env python3
"""
institutes_summary.py — сводки по институтам из уже сохранённых страниц (data/institutes/pages), сайт заново не парсится.

Зачем: на вопросы «какие институты есть», «какие направления в институте», «в каком институте учат на юриста», «какие кафедры у института»
нужны перечни, а на сайте они разбросаны по десяткам страниц (список институтов вообще лежит в меню, которое парсер убирает как шаблонный текст).

Запуск из корня проекта:   python3 institutes_summary.py
Результат: data/institutes/summary/*.md
    институты__перечень.md      все институты: руководитель, число направлений и кафедр, ссылка
    институты__направления.md   направления подготовки по уровням и в каких институтах они есть
    институты__кафедры.md       все кафедры и подразделения с заведующими
    институт__<имя>.md          по каждому институту: руководство, направления, кафедры, контакты учебного отдела
Дальше: python -m rag.index --add data/institutes, затем python -m rag.qdrant_store
"""
import json
from collections import defaultdict
import sys
from pathlib import Path

from rag.institutes import LEVEL_ORDER, ROOT, by_level, collect, head_of, load_pages

SOURCE = "data/institutes"


def person_line(p):
    parts = [p["name"] + (f" — {p['role']}" if p["role"] else "")]
    if p["address"]:
        parts.append(p["address"])
    if p["email"]:
        parts.append(f"e-mail: {p['email']}")
    if p["hours"]:
        parts.append("часы приёма: " + ", ".join(p["hours"]))
    return "- " + "; ".join(parts)


def write(name, title, url, lines):
    folder = Path(SOURCE, "summary")
    folder.mkdir(parents=True, exist_ok=True)
    front = "---\n" + f"url: {json.dumps(url, ensure_ascii=False)}\ntitle: {json.dumps(title, ensure_ascii=False)}\ntype: \"summary\"\n---\n\n"
    (folder / f"{name}.md").write_text(front + f"# {title}\n\n" + "\n".join(lines).strip() + "\n", encoding="utf-8")


def main():
    insts = collect(load_pages())
    if not insts:
        sys.exit(f"В {SOURCE}/pages нет страниц институтов: сначала python3 parser.py institutes")
    order = sorted(insts.items(), key=lambda kv: kv[1]["name"])

    lines = [f"Институтов в РТУ МИРЭА (по разделу сайта «Институты и факультеты»): {len(order)}.", "",
             "Список институтов: " + "; ".join(inst["name"] for _, inst in order) + ".", ""]
    for slug, inst in order:
        counts = {lvl: len(ps) for lvl, ps in by_level(inst["programs"]).items()}
        count_text = ", ".join(f"{lvl.lower()}: {counts[lvl]}" for lvl in LEVEL_ORDER if lvl in counts) or "нет данных"
        lines.append(f"- **{inst['name']}**: руководитель — {head_of(inst["leadership"]) or 'нет данных'}; направлений ({count_text}); подразделений и кафедр на странице структуры: "
                     f"{len(inst['units'])}; страница: {inst['url']}")
    write("институты__перечень", "Перечень институтов РТУ МИРЭА", ROOT, lines)

    by_code = defaultdict(lambda: defaultdict(set))
    for slug, inst in order:
        for p in inst["programs"]:
            by_code[p["level"]][(p["code"], p["name"])].add(inst["name"])
    lines = ["Направления подготовки институтов РТУ МИРЭА по данным страниц самих институтов. Одно и то же направление может вести несколько институтов. "
             "В сводках приёмной комиссии (priem.mirea.ru) подразделения программ могут быть указаны иначе.", ""]
    for lvl in LEVEL_ORDER:
        if lvl not in by_code:
            continue
        lines += [f"## {lvl} ({len(by_code[lvl])} направлений)", ""]
        lines += [f"- {code} {name} — институты: {', '.join(sorted(names))}" for (code, name), names in sorted(by_code[lvl].items())]
        lines.append("")
    write("институты__направления", "Направления подготовки по институтам", ROOT, lines)

    lines = ["Кафедры и подразделения институтов РТУ МИРЭА по страницам структуры институтов.", ""]
    for slug, inst in order:
        units = sorted(inst["units"], key=lambda u: u["name"])
        if not units:
            continue
        lines += [f"## {inst['name']} ({len(units)})", ""]
        for u in units:
            head = f"; заведующий: {u['head']['name']}" + (f" ({u['head']['role']})" if "и.о." in u["head"]["role"].lower() else "") if u["head"] else ""
            lines.append(f"- {u['name']}{head}; страница: {u['url']}")
        lines.append("")
    write("институты__кафедры", "Кафедры и подразделения институтов", ROOT, lines)

    for slug, inst in order:
        lines = [f"Страница института: {inst['url']}", "", "## Коротко", ""]
        if inst["leadership"]:
            lines.append(f"Руководитель: {head_of(inst["leadership"])}.")
        for lvl, ps in by_level(inst["programs"]).items():
            lines.append(f"{lvl} ({len(ps)}): " + "; ".join(f"{p['code']} {p['name']}" for p in ps) + ".")
        if inst["units"]:
            lines.append(f"Кафедр и подразделений на странице структуры: {len(inst['units'])}.")
        lines.append("")
        if inst["leadership"]:
            lines += [f"## Руководство института ({inst['leadership_url']})", ""] + [person_line(p) for p in inst["leadership"]] + [""]
        progs = by_level(inst["programs"])
        if progs:
            lines += [f"## Направления подготовки ({len(inst['programs'])})", ""]
            for lvl in LEVEL_ORDER:
                if lvl in progs:
                    lines += [f"{lvl}:"] + [f"- {p['code']} {p['name']} — {p['url']}" for p in progs[lvl]] + [""]
        if inst["units"]:
            lines += [f"## Кафедры и подразделения ({len(inst['units'])})", ""]
            for u in sorted(inst["units"], key=lambda u: u["name"]):
                lines.append(f"- {u['name']}" + (f" — заведующий: {u['head']['name']}" if u["head"] else "") + f" — {u['url']}")
            lines.append("")
        if inst["contacts"]:
            lines += [f"## {inst.get('contacts_title', 'Контакты')} ({inst['contacts_url']})", ""] + [person_line(p) for p in inst["contacts"]] + [""]
        write(f"институт__{slug}", f"{inst['name']}: направления, кафедры и руководство", inst["url"], lines)
    print(f"Готово: перечень, направления, кафедры и {len(order)} сводок по институтам в {SOURCE}/summary/")


if __name__ == "__main__":
    main()
