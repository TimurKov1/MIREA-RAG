import json
import re
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE = ROOT_DIR / "data" / "institutes"
INSTITUTES_JSON = ROOT_DIR / "index" / "institutes.json"
ROOT = "https://mirea.ru/education/the-institutes-and-faculties/"
LEVELS = {"bakalavriat": "Бакалавриат", "magistratura": "Магистратура", "spetsialitet": "Специалитет", "aspirantura": "Аспирантура"}
LEVEL_ORDER = list(LEVELS.values())
SECTIONS = {
    "leadership": {"the-leadership-institute", "rukovodstvo-instituta"},
    "programs": {"training-programs", "programmy-obucheniya"},
    "structure": {"the-structure-of-the-institute", "struktura-instituta"},
    "contacts": {"contacts", "kontakty", "uchebnyy-otdel"},
}
NAME = re.compile(r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)? [А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+$")
ALIASES = {
    "institut-informatsionnykh-tekhnologiy": ["ит", "иит"], "institut-iskusstvennogo-intellekta": ["иии", "ии"],
    "institut-kiberbezopasnosti-i-tsifrovykh-tekhnologiy": ["икцт", "икб"], "institut-radioelektroniki-i-industrialnogo-programmirovaniya": ["ирип"],
    "institute-of-fine-chemical-technology": ["итхт", "тонкой химии"], "institut-tekhnologii-upravleniya": ["иту"],
    "institut-peredovykh-izhenernykh-tekhnologiy": ["ипит"],
}


class Person(BaseModel):
    name: str
    role: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    hours: list[str] = []


class Unit(BaseModel):
    name: str
    url: str
    file: str = ""
    head: Person | None = None


class ProgramRef(BaseModel):
    level: str
    code: str
    name: str
    url: str


class Institute(BaseModel):
    slug: str
    name: str
    url: str
    head: str = ""
    leadership_url: str = ""
    leadership: list[Person] = []
    programs: list[ProgramRef] = []
    units: list[Unit] = []
    contacts_title: str = ""
    contacts_url: str = ""
    contacts: list[Person] = []
    aliases: list[str] = []

    @property
    def summary_file(self):
        return f"institutes/summary/институт__{self.slug}.md"


def load_pages():
    pages = {}
    for path in sorted((SOURCE / "pages").rglob("*.md")):
        head, _, body = path.read_text(encoding="utf-8").removeprefix("---\n").partition("\n---\n")
        fields = dict(line.split(": ", 1) for line in head.split("\n") if ": " in line)
        pages[json.loads(fields["url"])] = (json.loads(fields["title"]), [ln.strip() for ln in body.split("\n") if ln.strip()], str(path.relative_to(SOURCE)))
    return pages


def people(lines):
    """Люди со страницы руководства или контактов: фамилия, должность и контакты, которые идут до следующего человека."""
    out, cur = [], None
    for ln in lines:
        if NAME.match(ln):
            cur = {"name": ln, "role": "", "address": "", "phone": "", "email": "", "hours": []}
            out.append(cur)
        elif cur is not None:
            if "@" in ln and "mirea.ru" in ln:
                cur["email"] = re.sub(r"^E-mail:\s*", "", ln)
            elif ln.startswith("+7"):
                cur["phone"] = ln
            elif re.match(r"^(ПН|ВТ|СР|ЧТ|ПТ|СБ)\b", ln):
                cur["hours"].append(ln)
            elif "кабинет" in ln.lower() or re.match(r"^\d{6},|^ул\.|^пр\.", ln):
                cur["address"] = cur["address"] or ln
            elif not cur["role"] and ln not in (":", "Часы приема", "Телефон:", "E-mail:") and not ln.endswith(":"):
                cur["role"] = ln
    return out


def head_of(leadership):
    """Руководитель института: первый в списке руководства (на странице сайта директор указан первым)."""
    p = leadership[0] if leadership else None
    if p is None:
        return ""
    role = re.split(r"(?<!И\.о)\.\s|,\s", p["role"])[0].rstrip(".") if p["role"] else ""
    if "директор" not in role.lower():
        return p["name"] + " (указан первым в разделе «Руководство института»)"
    return f"{p['name']} ({role})"


def collect(pages):
    """Институты из страниц данных: имя, руководство, направления, подразделения, контакты (словари)."""
    insts = defaultdict(lambda: {"leadership": [], "programs": [], "units": [], "contacts": []})
    for url, (title, lines, rel) in pages.items():
        parts = url.removeprefix(ROOT).strip("/").split("/")
        if len(parts) < 2 or not url.startswith(ROOT):
            continue
        slug, section = parts[0], next((k for k, v in SECTIONS.items() if parts[1] in v), None)
        inst = insts[slug]
        short = title.split(" - ")[0]
        if section == "leadership":
            inst["name"] = title.split(" - ", 1)[-1] if " - " in title else inst.get("name", slug)
            inst["leadership"] = people(lines)
            inst["leadership_url"] = url
        elif section == "contacts":
            inst["contacts"] = people(lines)
            inst["contacts_title"] = short
            inst["contacts_url"] = url
        elif section == "programs" and len(parts) >= 4 and parts[2] in LEVELS:
            m = re.match(r"^(\d\d\.\d\d\.\d\d) (.+)$", short)
            if m:
                inst["programs"].append({"level": LEVELS[parts[2]], "code": m[1], "name": m[2], "url": url})
        elif section == "structure" and len(parts) >= 3:
            head = next((p for p in people(lines) if "заведующ" in p["role"].lower()), None)
            inst["units"].append({"name": short, "url": url, "file": f"institutes/{rel}", "head": head})
    for slug, inst in insts.items():
        inst.setdefault("name", slug)
        inst["url"] = ROOT + slug + "/"
    return {s: i for s, i in insts.items() if i["leadership"] or i["programs"] or i["units"]}


def by_level(programs):
    out = defaultdict(list)
    for p in sorted(programs, key=lambda p: (p["code"], p["name"])):
        out[p["level"]].append(p)
    return out


def parse_institutes():
    """Институты как модели pydantic (для инструментов агента)."""
    result = []
    for slug, d in sorted(collect(load_pages()).items(), key=lambda kv: kv[1]["name"]):
        result.append(Institute(slug=slug, name=d["name"], url=d["url"], head=head_of(d["leadership"]), leadership_url=d.get("leadership_url", ""),
                                leadership=d["leadership"], programs=d["programs"], units=d["units"], contacts_title=d.get("contacts_title", ""),
                                contacts_url=d.get("contacts_url", ""), contacts=d["contacts"], aliases=ALIASES.get(slug, [])))
    return result


def save_institutes():
    institutes = parse_institutes()
    INSTITUTES_JSON.parent.mkdir(exist_ok=True)
    INSTITUTES_JSON.write_bytes(TypeAdapter(list[Institute]).dump_json(institutes, indent=1))
    return institutes


_cache = None


def load_institutes():
    """Таблица институтов: из index/institutes.json, если он есть, иначе разбирается из страниц данных. Один раз за процесс."""
    global _cache
    if _cache is None:
        _cache = TypeAdapter(list[Institute]).validate_json(INSTITUTES_JSON.read_bytes()) if INSTITUTES_JSON.exists() else parse_institutes()
    return _cache


if __name__ == "__main__":
    institutes = save_institutes()
    print(f"Сохранено в {INSTITUTES_JSON.relative_to(ROOT_DIR)}: институтов {len(institutes)}, кафедр и подразделений {sum(len(i.units) for i in institutes)}, "
          f"направлений {sum(len(i.programs) for i in institutes)}")
