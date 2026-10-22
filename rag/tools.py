import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from rag.programs import YEAR, Program, load_programs
from rag.search import tokenize
from rag.tracing import observe

LEVELS = Literal["бакалавриат", "специалитет", "магистратура", "аспирантура"]
SORT = Literal["budget_places", "paid_places", "pass_budget", "price", "name"]
LIMIT = 20
ABOUT_CHARS = 3000


class Filters(BaseModel):
    """Условия отбора программ. Не заданные поля не учитываются."""
    level: LEVELS | None = Field(None, description="уровень образования")
    institute: str | None = Field(None, description="часть названия института, например «информационных технологий»")
    city: str | None = Field(None, description="часть названия города")
    form: str | None = Field(None, description="форма обучения: очная, очно-заочная, заочная")
    name_contains: str | None = Field(None, description="слово из названия направления или программы, например «безопасность»")
    code_prefix: str | None = Field(None, description="начало кода направления, например «09.03»")
    exam_subject: str | None = Field(None, description="предмет вступительного испытания, например «Физика»")
    duration_years: int | None = Field(None, description="срок обучения в целых годах", ge=1, le=7)
    duration_months: int | None = Field(None, description="срок обучения в месяцах, если он не целое число лет: 5 лет 6 месяцев = 66", ge=1, le=96)
    min_budget_places: int | None = Field(None, description="не меньше стольких бюджетных мест", ge=0)
    max_budget_places: int | None = Field(None, ge=0)
    min_paid_places: int | None = Field(None, ge=0)
    max_paid_places: int | None = Field(None, ge=0)
    min_pass_budget: int | None = Field(None, description="проходной балл на бюджет в прошлом году не ниже", ge=0)
    max_pass_budget: int | None = Field(None, description="проходной балл на бюджет в прошлом году не выше", ge=0)
    min_price: int | None = Field(None, description="базовая стоимость обучения в год не ниже, рублей", ge=0)
    max_price: int | None = Field(None, description="базовая стоимость обучения в год не выше, рублей", ge=0)


class FindProgramsArgs(Filters):
    sort_by: SORT = Field("name", description="по какому полю сортировать результат")
    descending: bool = Field(False, description="сортировать по убыванию (например, самые дорогие или с наибольшим числом мест)")
    limit: int = Field(10, description=f"сколько программ показать, не больше {LIMIT}", ge=1, le=LIMIT)


class CountProgramsArgs(Filters):
    group_by: Literal["level", "institute", "city", "form"] | None = Field(None, description="если задано, показать число по каждому значению поля")
    count: Literal["programs", "directions"] = Field("programs", description="что считать: programs — программы (профили), directions — направления подготовки (уникальные коды, "
                                                     "у направления может быть несколько программ)")


class GetProgramArgs(BaseModel):
    query: str = Field(description="название программы или направления, код, можно с уровнем: «анализ данных магистратура», «09.03.04»", min_length=2)
    level: LEVELS | None = Field(None, description="уровень образования, если он известен: у программ с одинаковым названием бывают разные уровни")


class CompareProgramsArgs(BaseModel):
    names: list[str] = Field(description="названия или коды двух-пяти программ для сравнения, например [«технологии виртуальных пространств», «разработка и дизайн компьютерных игр»]",
                             min_length=2, max_length=5)
    level: LEVELS | None = Field(None, description="уровень образования, если он у всех программ один и известен")


class SearchDocsArgs(BaseModel):
    query: str = Field(description="вопрос или ключевые слова для поиска по текстам сайта: правила приёма, сроки, документы, льготы, контакты, институты", min_length=2)


def matches(p: Program, f: Filters) -> bool:
    text = lambda *parts: " ".join(x for x in parts if x).lower()
    checks = [
        f.level is None or p.level == f.level,
        f.institute is None or f.institute.lower() in (p.institute or "").lower(),
        f.city is None or f.city.lower() in (p.city or "").lower(),
        f.form is None or f.form.lower() == (p.form or "").lower(),
        f.name_contains is None or f.name_contains.lower() in text(p.direction, p.name),
        f.code_prefix is None or p.code.startswith(f.code_prefix),
        f.exam_subject is None or any(f.exam_subject.lower() in e.subject.lower() for e in p.exams),
        f.duration_years is None or p.duration_months == f.duration_years * 12,
        f.duration_months is None or p.duration_months == f.duration_months,
    ]
    ranges = [(p.budget_places, f.min_budget_places, f.max_budget_places), (p.paid_places, f.min_paid_places, f.max_paid_places),
              (p.pass_budget, f.min_pass_budget, f.max_pass_budget), (p.price, f.min_price, f.max_price)]
    for value, low, high in ranges:
        if low is not None or high is not None:
            checks.append(value is not None and (low is None or value >= low) and (high is None or value <= high))
    return all(checks)


def programs_text(rows, n_of):
    return "\n".join(f"[{n_of(p)}] " + describe(p) for p in rows)


def describe(p: Program, full=False):
    parts = [p.title, p.level, p.institute or "институт не указан"]
    parts.append(f"места {YEAR}: бюджет {p.budget_places if p.budget_places is not None else 'нет данных'}, платные {p.paid_places if p.paid_places is not None else 'нет данных'}")
    parts.append(f"проходной в прошлом году: на бюджет {p.pass_budget if p.pass_budget is not None else 'нет данных'}, на платное {p.pass_paid if p.pass_paid is not None else 'нет данных'}")
    parts.append(f"базовая цена в год: {p.price} руб." if p.price else "цена: нет данных")
    if full:
        parts += [f"форма: {p.form}, {p.city}", f"срок обучения: {duration_text(p.duration_months)}"]
        if p.prices:
            parts.append("цены: " + ", ".join(f"{k} {v}" for k, v in p.prices.items()))
        if p.avg_pass_budget is not None:
            parts.append(f"средний проходной за три года: {p.avg_pass_budget}")
        if p.pass_budget_by_year:
            parts.append("проходной на бюджет по годам: " + ", ".join(f"{y}: {s}" for y, s in sorted(p.pass_budget_by_year.items())))
        if p.exams:
            parts.append("вступительные испытания: " + "; ".join(f"{e.subject} (мин. {e.min_score})" for e in p.exams))
    text = "; ".join(parts)
    if full and p.about:
        about = "\n".join(f"{name}: {body}" for name, body in p.about.items())
        text += "\n\nО программе:\n" + (about if len(about) <= ABOUT_CHARS else about[:ABOUT_CHARS].rsplit(" ", 1)[0] + " …")
    return text


def duration_text(months):
    if not months:
        return "нет данных"
    if months % 12:
        return f"{months} мес."
    years = months // 12
    return f"{years} {'год' if years == 1 else 'года' if years < 5 else 'лет'}"


def name_score(query_tokens, p: Program):
    tokens = set(tokenize(f"{p.code} {p.direction} {p.name}"))
    return sum(1 for t in query_tokens if t in tokens) / len(query_tokens) if query_tokens else 0


class Tools:
    """Инструменты агента. Каждый возвращает текст для модели; источники регистрируются с номерами, на которые модель ссылается в ответе."""

    def __init__(self, search_docs=None):
        self.programs = load_programs()
        self.search_docs_fn = search_docs
        self.sources = []

    def register(self, title, place, text="", chunk_ids=()):
        for n, s in enumerate(self.sources, 1):
            if s["place"] == place:
                return n
        self.sources.append({"title": title, "place": place, "text": text, "chunk_ids": list(chunk_ids)})
        return len(self.sources)

    def program_source(self, p):
        return self.register(p.title, p.url, chunk_ids=[f"pages/priem.mirea.ru/{p.file}#0"] if p.file else [])


    def find_programs(self, a: FindProgramsArgs) -> str:
        rows = [p for p in self.programs if matches(p, a)]
        key = {"name": lambda p: p.title, "budget_places": lambda p: p.budget_places, "paid_places": lambda p: p.paid_places,
               "pass_budget": lambda p: p.pass_budget, "price": lambda p: p.price}[a.sort_by]
        known = [p for p in rows if key(p) is not None]
        rows = sorted(known, key=key, reverse=a.descending) + [p for p in rows if key(p) is None]
        shown = rows[:a.limit]
        if not rows:
            return "Не найдено ни одной программы с такими условиями."
        return (f"Найдено программ: {len(rows)}, показано {len(shown)} (данные на {YEAR} год).\n"
                + programs_text(shown, lambda p: self.program_source(p)))

    def count_programs(self, a: CountProgramsArgs) -> str:
        rows = [p for p in self.programs if matches(p, a)]
        unit = lambda group: len({p.code for p in group}) if a.count == "directions" else len(group)
        what = "Направлений подготовки" if a.count == "directions" else "Программ"
        if a.group_by is None:
            return f"{what} с такими условиями: {unit(rows)}."
        groups = {}
        for p in rows:
            groups.setdefault(getattr(p, a.group_by) or "не указано", []).append(p)
        return (f"{what} с такими условиями: {unit(rows)}. По полю «{a.group_by}»:\n"
                + "\n".join(f"- {k}: {unit(g)}" for k, g in sorted(groups.items(), key=lambda kv: -unit(kv[1]))))

    def resolve(self, query, level=None):
        """Программа по названию или коду: ("one", программа, похожие), ("many", [(оценка, программа)], лучшая оценка) или ("none", None, None)."""
        tokens = tokenize(query)
        pool = [p for p in self.programs if level is None or p.level == level]
        exact = [p for p in pool if set(tokenize(p.name)) == set(tokens) or set(tokenize(f"{p.direction} {p.name}")) == set(tokens) or p.code in query]
        if len(exact) == 1:
            p = exact[0]
            similar = [q for s, q in sorted(((name_score(tokens, q), q) for q in pool if q is not p), key=lambda sp: -sp[0]) if s >= 0.99][:3]
            return "one", p, similar
        scored = sorted(((name_score(tokens, p), p) for p in (exact or pool)), key=lambda sp: -sp[0])
        scored = [(s, p) for s, p in scored if s > 0][:5]
        if not scored:
            return "none", None, None
        best = scored[0][0]
        top = [p for s, p in scored if s == best]
        if best >= 0.99 and len(top) == 1:
            return "one", top[0], []
        return "many", scored, best

    def get_program(self, a: GetProgramArgs) -> str:
        kind, found, extra = self.resolve(a.query, a.level)
        if kind == "none":
            return "Программа с таким названием не найдена."
        if kind == "one":
            text = f"[{self.program_source(found)}] " + describe(found, full=True)
            if extra:
                text += "\nЕсть программы с похожим названием: " + "; ".join(f"[{self.program_source(q)}] {q.title} ({q.level})" for q in extra)
            return text
        head = ("Подходит несколько программ, нужно уточнить (уровень, институт или название):" if extra >= 0.99
                else "Точного совпадения с названием нет. Ближайшие по названию (возможно, нужной программы в базе нет):")
        return head + "\n" + programs_text([p for _, p in found], lambda p: self.program_source(p))

    def compare_programs(self, a: CompareProgramsArgs) -> str:
        programs = []
        for name in a.names:
            kind, found, extra = self.resolve(name, a.level)
            if kind == "none":
                return f"Программа «{name}» не найдена. Уточните название."
            if kind == "many":
                return (f"Название «{name}» подходит к нескольким программам, сравнить нельзя, пока не уточнено (уровень, институт или полное название):\n"
                        + programs_text([p for _, p in found], lambda p: self.program_source(p)))
            programs.append(found)
        lines = [f"[{self.program_source(p)}] " + describe(p) + f"; срок обучения: {duration_text(p.duration_months)}" +
                 (("; вступительные испытания: " + "; ".join(f"{e.subject} (мин. {e.min_score})" for e in p.exams)) if p.exams else "") for p in programs]
        metrics = [("проходной балл на бюджет в прошлом году", "pass_budget", "выше"), ("проходной балл на платное в прошлом году", "pass_paid", "выше"),
                   ("бюджетных мест", "budget_places", "больше"), ("платных мест", "paid_places", "больше"), ("базовая цена в год, руб.", "price", "выше")]
        compare = []
        for label, field, word in metrics:
            values = [(p, getattr(p, field)) for p in programs if getattr(p, field) is not None]
            if len(values) < 2:
                continue
            top = max(v for _, v in values)
            winners = [p.name for p, v in values if v == top]
            result = "одинаково у всех" if len(winners) == len(values) else f"{word} у: {', '.join(winners)}"
            compare.append(f"- {label}: " + "; ".join(f"{p.name} {v}" for p, v in values) + f" ({result})")
        return "Сравнение программ (данные на %d год):\n%s\n\nИтог по показателям:\n%s" % (YEAR, "\n".join(lines), "\n".join(compare))

    def search_docs(self, a: SearchDocsArgs) -> str:
        if self.search_docs_fn is None:
            return "Поиск по текстам недоступен."
        found = self.search_docs_fn(a.query)
        return "\n\n".join(f"[{self.register(s['title'], s['place'], s['text'], s.get('chunk_ids', ()))}] {s['text']}" for s in found) or "Ничего не найдено."


    SPECS = {
        "search_docs": (SearchDocsArgs, "Поиск по текстам сайта приёмной комиссии и институтов: правила и сроки приёма, документы, льготы, стипендии, "
                        "общежития, контакты, руководство и кафедры институтов. Подходит для вопросов, ответ на которые написан в тексте."),
        "find_programs": (FindProgramsArgs, "Отбор и сортировка программ по таблице данных: проходной балл, бюджетные и платные места, цена, институт, уровень, "
                          "предметы. Подходит для вопросов «какие программы…», «самая дорогая», «где больше всего мест», «с проходным ниже…»."),
        "count_programs": (CountProgramsArgs, "Подсчёт программ по условиям (и по группам: по уровню, институту, городу, форме обучения). "
                           "Подходит для вопросов «сколько программ…»."),
        "get_program": (GetProgramArgs, "Карточка одной программы по названию или коду: места, цены, проходные баллы по годам, предметы, срок, институт, а также описание: чему учат, "
                        "карьера и профессии выпускников, места практики, партнёры. Для просьбы «расскажи про программу» вызывай её. "
                        "Если названию соответствует несколько программ, вернёт список для уточнения."),
        "compare_programs": (CompareProgramsArgs, "Сравнение двух-пяти программ: места, цены, проходные баллы, предметы, срок и итог, у какой программы показатель выше. "
                             "Для вопросов «что проходнее», «что дороже», «чем отличаются программы», «сравни…»."),
    }

    def schemas(self):
        return [{"type": "function", "function": {"name": name, "description": description, "parameters": model.model_json_schema()}}
                for name, (model, description) in self.SPECS.items()]

    @observe(name="tool")
    def call(self, name, arguments):
        """Выполняет вызов, который вернула модель: проверяет аргументы по схеме, ошибки возвращает текстом, чтобы модель могла исправиться."""
        if name not in self.SPECS:
            return f"Неизвестный инструмент {name}. Доступны: {', '.join(self.SPECS)}."
        model = self.SPECS[name][0]
        try:
            args = model.model_validate(json.loads(arguments) if isinstance(arguments, str) else arguments)
        except (ValueError, ValidationError) as e:
            return f"Ошибка в аргументах инструмента {name}: {str(e)[:400]}. Исправьте вызов."
        return getattr(self, name)(args)
