import pytest

from rag.programs import PROGRAMS_JSON, fields, load_programs, section, topic

PAGE = """- **название**: Анализ данных
- **места**: год: 2026; бюджет: 29; платные: 60
- **вступительные испытания**:
  - предмет: Информатика; тип: Экзамен; мин. балл: 50
  - предмет: Русский язык; тип: Экзамен; мин. балл: 50
- **описание**: Готовим аналитиков.
  Работа с **данными**.
"""


def test_fields_reads_top_level_lines():
    assert fields(PAGE)["места"] == "год: 2026; бюджет: 29; платные: 60"


def test_section_reads_nested_lines():
    assert section(PAGE, "вступительные испытания") == ["предмет: Информатика; тип: Экзамен; мин. балл: 50", "предмет: Русский язык; тип: Экзамен; мин. балл: 50"]
    assert section(PAGE, "нет такого") == []


def test_topic_joins_value_and_nested_lines_without_markup():
    assert topic(PAGE, "описание") == "Готовим аналитиков.\nРабота с данными."


def test_program_title_and_price(programs):
    assert programs[0].title == "09.03.04 Программная инженерия — Системная и программная инженерия" and programs[0].price == 400000


@pytest.mark.skipif(not PROGRAMS_JSON.exists(), reason="нет index/programs.json")
def test_real_table_is_consistent():
    programs = load_programs()
    assert len(programs) > 100
    assert len({p.id for p in programs}) == len(programs)
    assert {p.level for p in programs} <= {"бакалавриат", "специалитет", "магистратура", "аспирантура"}
