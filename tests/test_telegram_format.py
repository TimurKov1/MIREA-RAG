from rag.telegram_format import LIMIT, bold, escape, format_reply, sources_html, split_text, style

SOURCES = [{"title": "Страница <1>", "place": "https://priem.mirea.ru/x?a=1&b=2"}, {"title": "Сводка", "place": "сводка по данным priem.mirea.ru"}]


def test_escape_html():
    assert escape("<b>&") == "&lt;b&gt;&amp;"


def test_bold_converts_pairs_and_drops_stray_markers():
    assert bold("**Итого** 5") == "<b>Итого</b> 5"
    assert bold("осталось ** одиночное") == "осталось  одиночное"


def test_style_heading_list_and_text():
    assert style("Что вы будете изучать:") == "<b>Что вы будете изучать:</b>"
    assert style("- пункт **важный**") == "• пункт <b>важный</b>"
    assert style("обычная <строка>") == "обычная &lt;строка&gt;"
    assert style("Ключевые числа: **5**") == "Ключевые числа: <b>5</b>"


def test_split_text_parts_fit_the_limit():
    text = "\n\n".join("абзац " * 200 for _ in range(10)) + "\n" + "я" * (LIMIT * 2 + 5)
    parts = split_text(text)
    assert len(parts) > 1 and all(len(p) <= LIMIT for p in parts)
    assert "".join(parts).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_split_text_short_and_empty():
    assert split_text("коротко") == ["коротко"]
    assert split_text("") == [""]


def test_sources_html_links_only_for_urls():
    out = sources_html(SOURCES)
    assert out.startswith("<blockquote expandable>") and out.endswith("</blockquote>")
    assert '<a href="https://priem.mirea.ru/x?a=1&amp;b=2">Страница &lt;1&gt;</a>' in out
    assert "[2] Сводка (сводка по данным priem.mirea.ru)" in out and "<a" not in out.split("[2]")[1]


def test_format_reply_pairs_html_and_plain():
    (html_text, plain_text), = format_reply("**Ответ** [1]", SOURCES)
    assert "<b>Ответ</b>" in html_text and "<blockquote expandable>" in html_text
    assert "**" not in plain_text and "Источники:" in plain_text and "<b>" not in plain_text and "&lt;" not in plain_text


def test_format_reply_long_answer_stays_under_telegram_limit():
    parts = format_reply("абзац\n\n" * 3000, SOURCES)
    assert len(parts) > 1 and all(len(h) <= 4096 for h, _ in parts)
    assert "Источники" in parts[-1][0]


def test_format_reply_without_sources():
    (html_text, _), = format_reply("просто ответ", [])
    assert "blockquote" not in html_text
