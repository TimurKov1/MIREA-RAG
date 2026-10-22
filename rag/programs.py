import re
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "priem"
PROGRAMS_JSON = ROOT / "index" / "programs.json"

YEAR = 2026
LEVELS = {"03": "бакалавриат", "05": "специалитет", "04": "магистратура"}


ABOUT = ["описание", "Что вы будете изучать", "Преимущества профиля в РТУ МИРЭА", "Возможный карьерный путь выпускников",
         "Профессии, которые могут выбрать выпускники", "Результаты обучения по профилю", "Основные места прохождения практик", "партнёры"]


class Exam(BaseModel):
    subject: str
    kind: str
    min_score: int | None = None


class Program(BaseModel):
    id: int
    url: str
    file: str = ""
    code: str
    direction: str
    name: str
    level: str
    form: str | None = None
    city: str | None = None
    institute: str | None = None
    duration_months: int | None = None
    budget_places: int | None = None
    paid_places: int | None = None
    prices: dict[str, int] = {}
    pass_budget: int | None = None
    pass_paid: int | None = None
    avg_pass_budget: float | None = None
    pass_budget_by_year: dict[int, int] = {}
    competition: float | None = None
    exams: list[Exam] = []
    about: dict[str, str] = {}

    @property
    def title(self) -> str:
        return f"{self.code} {self.direction} — {self.name}"

    @property
    def price(self) -> int | None:
        return self.prices.get("Базовая")


def fields(text):
    """Строки вида «- **ключ**: значение» верхнего уровня страницы → словарь."""
    return {m[1]: m[2] for m in re.finditer(r"^- \*\*([^*]+)\*\*: (.*)$", text, re.M)}


def section(text, name):
    """Вложенные строки списка под «- **name**:» (без маркера «- »)."""
    m = re.search(rf"^- \*\*{re.escape(name)}\*\*:\n((?:  .*\n?)*)", text, re.M)
    return [ln.strip().removeprefix("- ") for ln in m[1].splitlines() if ln.strip()] if m else []


def topic(text, name):
    """Текст раздела «- **name**: …»: значение в той же строке и вложенные строки (с разметкой списков убранной)."""
    m = re.search(rf"^- \*\*{re.escape(name)}\*\*:(.*)\n?((?:  .*\n?)*)", text, re.M)
    if not m:
        return ""
    lines = [m[1].strip()] + [re.sub(r"^\s*-?\s*", "", ln).strip() for ln in m[2].splitlines()]
    return "\n".join(re.sub(r"\*\*([^*]+)\*\*", r"\1", ln) for ln in lines if ln and ln != "-")


def kv(line):
    """«год: 2026; бюджет: 29; платные: 60» → {"год": "2026", "бюджет": "29", "платные": "60"}"""
    return dict(part.split(": ", 1) for part in line.split("; ") if ": " in part)


def number(value, kind=int):
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def level_of(code, declared):
    parts = code.split(".")
    if len(parts) == 3 and parts[1] in LEVELS and len(parts[0]) == 2:
        return LEVELS[parts[1]]
    return "аспирантура" if "аспирантура" in (declared or "") else (declared or "").split("/")[0]


def parse_page(url, text):
    f = fields(text)
    direction = {k.strip("* "): v for k, v in (ln.split(": ", 1) for ln in section(text, "направление") if ": " in ln)}
    direction = {k.strip("*"): v for k, v in direction.items()}
    places = kv(f.get("места", ""))
    last = kv(f.get("проходной балл прошлого года", ""))
    avg = kv(f.get("средний проходной балл", ""))
    duration = kv(f.get("срок обучения", ""))
    stats = kv(f.get("статистика приёма", ""))
    prices = {}
    for ln in section(text, "стоимость обучения"):
        p = kv(ln)
        if p.get("год") == str(YEAR) and number(p.get("цена")) is not None:
            prices[p.get("тип", "")] = number(p["цена"])
    by_year = {}
    for ln in section(text, "баллы по годам"):
        p = kv(ln)
        if number(p.get("год")) is not None and number(p.get("проходной на бюджет")) is not None:
            by_year[number(p["год"])] = number(p["проходной на бюджет"])
    exams = []
    for ln in section(text, "вступительные испытания"):
        p = kv(ln)
        exams.append(Exam(subject=p.get("предмет", ""), kind=p.get("тип", ""), min_score=number(p.get("мин. балл"))))
    about = {name: t for name in ABOUT if (t := topic(text, name))}
    code = direction.get("код", "")
    return Program(
        id=int(re.search(r"direction_id=(\d+)", url)[1]), url=url, code=code, direction=direction.get("программа", ""), name=f.get("название", ""),
        level=level_of(code, direction.get("уровень образования")), form=direction.get("форма обучения"), city=direction.get("город"),
        duration_months=number(duration.get("месяцев")),
        budget_places=number(places.get("бюджет")) if places.get("год") == str(YEAR) else None,
        paid_places=number(places.get("платные")) if places.get("год") == str(YEAR) else None,
        prices=prices, pass_budget=number(last.get("проходной на бюджет")), pass_paid=number(last.get("проходной на платное")),
        avg_pass_budget=number(avg.get("проходной на бюджет"), float), pass_budget_by_year=by_year, competition=number(stats.get("конкурс"), float), exams=exams, about=about)


def institutes():
    """Адрес страницы программы → институт (из сводок по направлениям: «очная; г. Москва; Институт …; мест на бюджет: …; страница: …»)."""
    out = {}
    skip = ("мест на бюджет", "платных мест", "проходной на бюджет", "страница")
    for path in (DATA / "summary").glob("*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^- \*\*.+?\*\*: (.*); страница: (\S+)$", line)
            if m:
                rest = [p for p in m[1].split("; ") if not p.startswith(skip)]
                if len(rest) >= 3:
                    out[m[2]] = rest[2]
    return out


_cache = None


def parse_programs():
    """Все программы из страниц направлений (guide-direction) и сводок (для института)."""
    unit = institutes()
    programs = []
    for path in sorted((DATA / "pages").rglob("guide-direction__*.md")):
        head, _, body = path.read_text(encoding="utf-8").removeprefix("---\n").partition("\n---\n")
        url = re.search(r'^url: "([^"]+)"', head, re.M)[1]
        p = parse_page(url, body)
        p.file = path.name
        p.institute = unit.get(url) if p.level != "аспирантура" else None
        programs.append(p)
    return programs


def save_programs():
    programs = parse_programs()
    PROGRAMS_JSON.parent.mkdir(exist_ok=True)
    PROGRAMS_JSON.write_bytes(TypeAdapter(list[Program]).dump_json(programs, indent=1))
    return programs


def load_programs():
    """Таблица программ: из index/programs.json, если он есть, иначе разбирается из страниц данных. Один раз за процесс."""
    global _cache
    if _cache is None:
        if PROGRAMS_JSON.exists():
            _cache = TypeAdapter(list[Program]).validate_json(PROGRAMS_JSON.read_bytes())
        else:
            _cache = parse_programs()
    return _cache


if __name__ == "__main__":
    from collections import Counter
    ps = save_programs()
    print(f"Сохранено в {PROGRAMS_JSON.relative_to(ROOT)}")
    print(f"Программ: {len(ps)}; по уровням: {dict(Counter(p.level for p in ps))}")
    print(f"С указанным институтом: {sum(1 for p in ps if p.institute)}; с ценой: {sum(1 for p in ps if p.price)}; с бюджетными местами: "
          f"{sum(1 for p in ps if p.budget_places is not None)}; с проходным: {sum(1 for p in ps if p.pass_budget is not None)}")
    print(ps[0].model_dump_json(indent=1, exclude={"pass_budget_by_year"})[:900])
