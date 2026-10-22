from rag.chunking import MAX_CHARS, chunk_body, level_of, load_chunks


def test_chunk_body_respects_max_length():
    body = "\n".join(f"строка номер {i} " + "слово " * 20 for i in range(60))
    chunks = chunk_body(body)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHARS + 200 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks).count("строка номер") == 60


def test_chunk_body_starts_new_chunk_at_section():
    body = "# Заголовок\n" + "текст " * 60 + "\n- **места**: бюджет: 5\n" + "ещё " * 10
    chunks = chunk_body(body)
    assert any(c.startswith("- **места**") for c in chunks)


def test_chunk_body_empty():
    assert chunk_body("") == []


def test_level_of_from_field_and_url():
    assert level_of("", "t", "- **уровень образования**: магистратура") == "магистратура"
    assert level_of("https://priem.mirea.ru/first-degree/info", "t", "") == "бакалавриат"
    assert level_of("https://priem.mirea.ru/phd/x", "t", "") == "аспирантура"
    assert level_of("https://priem.mirea.ru/about/x", "t", "") == "общее"


def test_load_chunks_builds_ids_and_prefix(tmp_path):
    folder = tmp_path / "institutes"
    (folder / "pages").mkdir(parents=True)
    (folder / "summary").mkdir()
    page = '---\nurl: "https://priem.mirea.ru/magistracy/info"\ntitle: "О магистратуре"\n---\n\n# О магистратуре\n\nДва года обучения.\n'
    (folder / "pages" / "a.md").write_text(page, encoding="utf-8")
    (folder / "summary" / "s.md").write_text('---\nurl: ""\ntitle: "Сводка: магистратура"\n---\n\n# Сводка\n\nНаправлений: 3.\n', encoding="utf-8")
    chunks = load_chunks(folder)
    assert [c.id for c in chunks] == ["institutes/pages/a.md#0", "institutes/summary/s.md#0"]
    assert chunks[0].level == "магистратура"
    assert chunks[0].text.startswith("[магистратура] О магистратуре\n\n")
