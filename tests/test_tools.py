from rag.tools import CountProgramsArgs, FindProgramsArgs


def test_find_programs_filters_and_sorts(tools):
    out = tools.find_programs(FindProgramsArgs(level="бакалавриат", sort_by="budget_places", descending=True, limit=2))
    assert "Найдено программ: 3" in out and "показано 2" in out
    assert out.index("Системная и программная инженерия") < out.index("Анализ данных")
    assert "Разработка игр" not in out


def test_find_programs_numeric_condition(tools):
    out = tools.find_programs(FindProgramsArgs(min_pass_budget=275))
    assert "Анализ данных" in out and "Разработка игр" not in out


def test_find_programs_nothing(tools):
    assert tools.find_programs(FindProgramsArgs(city="Омск")).startswith("Не найдено")


def test_count_programs_total_and_groups(tools):
    assert "Программ с такими условиями: 4." == tools.count_programs(CountProgramsArgs())
    grouped = tools.count_programs(CountProgramsArgs(group_by="level"))
    assert "- бакалавриат: 3" in grouped and "- магистратура: 1" in grouped


def test_count_directions_differs_from_programs(tools):
    assert "Направлений подготовки с такими условиями: 3." == tools.count_programs(CountProgramsArgs(count="directions"))


def test_get_program_by_code_and_ambiguity(tools):
    assert "Системная и программная инженерия" in tools.call("get_program", {"query": "09.03.04 системная"})
    assert "Анализ данных" in tools.call("get_program", {"query": "анализ данных", "level": "магистратура"})
    ambiguous = tools.call("get_program", {"query": "анализ данных"})
    assert "магистратура" in ambiguous and "бакалавриат" in ambiguous


def test_compare_programs(tools):
    out = tools.call("compare_programs", {"names": ["разработка игр", "системная и программная инженерия"]})
    assert "Разработка игр" in out and "Системная" in out


def test_sources_are_numbered_without_duplicates(tools):
    first = tools.register("A", "https://a")
    assert tools.register("B", "https://b") == 2
    assert tools.register("A again", "https://a") == first == 1
    assert len(tools.sources) == 2


def test_call_validates_arguments(tools):
    assert "Ошибка в аргументах" in tools.call("find_programs", {"limit": 1000})
    assert "Ошибка в аргументах" in tools.call("find_programs", "не json")
    assert "Неизвестный инструмент" in tools.call("drop_database", {})


def test_search_docs_registers_sources(tools):
    out = tools.call("search_docs", {"query": "стипендия"})
    assert "стипендия" in out and tools.sources[-1]["place"] == "https://x/doc"


def test_schemas_describe_all_tools(tools):
    names = {s["function"]["name"] for s in tools.schemas()}
    assert names == {"find_programs", "count_programs", "get_program", "compare_programs", "search_docs"}
