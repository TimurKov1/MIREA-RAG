import html
import re

LIMIT = 3800
HEADING = re.compile(r"^[^\[\]•\-*\d][^\[\]]{0,58}:$")


def escape(text):
    return html.escape(text, quote=False)


def bold(text):
    """Жирный шрифт модели **…** в теги <b>; текст при этом уже экранирован. Непарные звёздочки убираются."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text).replace("**", "")


def style(line):
    """Одна строка ответа в HTML: заголовок выделяется жирным, маркеры списка заменяются на «•»."""
    stripped = line.strip()
    if "**" not in stripped and HEADING.match(stripped) and len(stripped.split()) <= 6:
        return f"<b>{escape(stripped)}</b>"
    m = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
    if m:
        return f"{m[1]}• {bold(escape(m[2]))}"
    return bold(escape(line))


def split_text(text, limit=LIMIT):
    """Делит длинный ответ по абзацам (слишком длинный абзац по строкам, слишком длинную строку по длине), чтобы части умещались в сообщение Telegram."""
    units = []
    for paragraph in text.split("\n\n"):
        lines = [paragraph] if len(paragraph) <= limit else paragraph.split("\n")
        for i, line in enumerate(lines):
            for j in range(0, max(len(line), 1), limit):
                units.append((line[j:j + limit], "\n\n" if i == 0 and j == 0 else "\n" if j == 0 else ""))
    parts, current = [], ""
    for piece, sep in units:
        if current and len(current) + len(sep) + len(piece) > limit:
            parts.append(current)
            current = piece
        else:
            current = current + sep + piece if current else piece
    if current:
        parts.append(current)
    return parts or [""]


def sources_html(sources):
    """Свёрнутый блок источников: названия, кликабельные там, где есть адрес страницы."""
    lines = []
    for n, s in enumerate(sources, 1):
        place = s["place"]
        title = escape(s["title"])
        lines.append(f'[{n}] <a href="{html.escape(place, quote=True)}">{title}</a>' if place.startswith("http") else f"[{n}] {title} ({escape(place)})")
    return "<blockquote expandable><b>Источники</b>\n" + "\n".join(lines) + "</blockquote>"


def sources_plain(sources):
    return "Источники:\n" + "\n".join(f"  [{n}] {s['title']} — {s['place']}" for n, s in enumerate(sources, 1))


def format_reply(text, sources):
    """Ответ для Telegram: список пар (HTML, простой текст). Простой текст нужен как запасной вариант, если Telegram отклонит разметку."""
    chunks = split_text(text)
    html_parts = ["\n".join(style(line) for line in chunk.split("\n")) for chunk in chunks]
    plain_parts = [chunk.replace("**", "") for chunk in chunks]
    if sources:
        block_html, block_plain = sources_html(sources), sources_plain(sources)
        if len(html_parts[-1]) + len(block_html) + 2 <= 4096:
            html_parts[-1] += "\n\n" + block_html
            plain_parts[-1] += "\n\n" + block_plain
        else:
            html_parts.append(block_html)
            plain_parts.append(block_plain)
    return list(zip(html_parts, plain_parts))
