#!/usr/bin/env python3
"""
parser.py — выгрузка сайта для RAG: текст страниц (.md) + скачанные документы.

Установка:
    pip install httpx beautifulsoup4 lxml trafilatura

Запуск:  python3 parser.py [источник]   (все настройки — в блоке НАСТРОЙКИ ниже; источники — в SOURCES, по умолчанию priem)

Ограничение ссылок: парсятся только URL, подходящие под один из шаблонов ALLOW. Стартовые адреса разрешены
всегда. Всё остальное попадает в links.csv со статусом skipped.

Результат (папка OUT_DIR):
    pages/<хост>/<slug>__<hash>.md   текст страниц (с front matter: url, title, дата)
    files/<sha256[:12]>_<имя>        скачанные документы
    links.csv                        все найденные ссылки: статус (done/duplicate/skipped/error) и причина

Дубликаты не сохраняются: одинаковые URL (после нормализации), редиректы на уже обработанный адрес,
страницы с одинаковым текстом, файлы с одинаковым содержимым.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None

HTML_PARSER = "lxml" if importlib.util.find_spec("lxml") else "html.parser"


START_URLS = ["https://priem.mirea.ru"]

OUT_DIR = "data/priem"

ALLOW = [
    r"^https://priem\.mirea\.ru/first-degree/",
    r"^https://priem\.mirea\.ru/magistracy/",
    r"^https://priem\.mirea\.ru/spo/",
    r"^https://priem\.mirea\.ru/branches/",
    r"^https://priem\.mirea\.ru/phd/",
    r"^https://priem\.mirea\.ru/transfer-restoration/",
    r"^https://priem\.mirea\.ru/about/",
]

DOCS_FROM_SITE = True

IDS_APIS = [
    "https://priem.mirea.ru/lk/api/directions/get_places?eli=1&li=1&efi=1",
    "https://priem.mirea.ru/lk/api/directions/get_places?eli=3&li=1&efi=1",
    "https://priem.mirea.ru/lk/api/directions/get_places?eli=3&li=1&efi=2",
    "https://priem.mirea.ru/lk/api/directions/get_places?eli=3&li=1&efi=4",
    "https://priem.mirea.ru/lk/api/directions/get_places?eli=4&li=1&efi=1",
]
IDS_LINK = "https://priem.mirea.ru/guide-direction?direction_id={id}"
DETAIL_API = "https://priem.mirea.ru/lk/api/directions/{id}"

SOURCES = {
    "priem": {},
    "institutes": {
        "urls": ["https://www.mirea.ru/education/the-institutes-and-faculties/"],
        "out": "data/institutes",
        "allow": [r"^https://mirea\.ru/education/the-institutes-and-faculties/"],
        "ids_apis": [],
        "detail_api": "",
        "docs_from_site": False,
        "concurrency": 2, "delay": 1.0, "retries": 6, "retry_delay": 3.0,
        "drop_lines": ["Размер шрифта:", "Отображение изображений:", "Цвет фона:", "Вид шрифта:", "Кернинг:", "Вид изображений:", "Развернуть"],
        "title_suffix": " - РТУ МИРЭА",
        "deny": [r"/(achievement-of-students|dostizheniya[^/]*)/"],
        "max_pages": 600,
        "boilerplate_min_pages": 5,
        "extractor": "fallback",
    },
}

CONCURRENCY = 5
DELAY = 0.3
TIMEOUT = 30
RETRIES = 3
RETRY_DELAY = 1.0
MAX_FILE_MB = 100
MIN_TEXT = 80
INSECURE = False
USER_AGENT = "site-scraper/1.0 (university RAG project)"

DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".rtf"}
SKIP_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tif", ".tiff",
    ".zip", ".rar", ".7z", ".gz", ".tar", ".exe", ".msi", ".dmg",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".mkv", ".webm",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot", ".map",
}
DOC_CTYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}
GENERIC_BINARY = {"application/octet-stream", "binary/octet-stream", "application/download",
                  "application/force-download", "application/x-download"}
HTML_CTYPES = {"text/html", "application/xhtml+xml"}
MAX_HTML_BYTES = 8_000_000
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
TRACKING_PARAMS = {"fbclid", "gclid", "yclid", "ysclid", "_openstat", "mc_cid", "mc_eid", "igshid",
                   "phpsessid", "jsessionid"}
BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "tr", "table", "section", "article", "h1", "h2", "h3", "h4",
              "h5", "h6", "blockquote", "pre", "dt", "dd", "hr"]


class Skip(Exception):
    """Ссылку намеренно не сохраняем (причина — в тексте)."""


class FetchError(Exception):
    """Не удалось скачать после всех повторов."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def ext_of(url: str) -> str:
    return Path(urlparse(url).path).suffix.lower()


def site_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize(raw: str) -> str | None:
    """Канонический вид URL: без #фрагмента и трекинг-меток, параметры отсортированы, без www."""
    url, _ = urldefrag(raw.strip())
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return None
    host = site_key(url)
    if p.scheme not in ("http", "https") or not host:
        return None
    netloc = host + (f":{port}" if port and port not in (80, 443) else "")
    path = quote(unquote(re.sub(r"/{2,}", "/", p.path) or "/"), safe="/:@!$&'()*+,;=-._~")
    pairs = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS))
    return urlunparse((p.scheme, netloc, path, "", urlencode(pairs, quote_via=quote, safe="/:@,"), ""))


def safe_name(name: str, maxlen: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", unquote(name)).strip(" ._")
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    return (stem[:maxlen] + ext[:10]) if name else "file"


def filename_from_disposition(disp: str) -> str:
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", disp, re.I) or re.search(r'filename\s*=\s*"?([^";]+)"?', disp, re.I)
    return unquote(m.group(1).strip().strip('"')) if m else ""


def page_relpath(url: str) -> str:
    p = urlparse(url)
    slug = re.sub(r"[^\w\-]+", "-", unquote(p.path).strip("/")).strip("-")[:80] or "index"
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"pages/{p.netloc.replace(':', '_')}/{slug}__{h}.md"


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).rstrip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


def fallback_text(soup: BeautifulSoup) -> str:
    """Запасной экстрактор: без меню, подвалов и скриптов; блочные теги → переводы строк."""
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"]):
        tag.extract()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    for br in root.find_all("br"):
        br.replace_with("\n")
    for tag in root.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in root.get_text("").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def strip_lines(text: str, drop: set) -> str:
    """Убирает строки целиком по списку шаблонного текста (меню, подписи виджетов) и схлопывает пустые строки."""
    if not drop:
        return text
    text = "\n".join(line for line in text.splitlines() if line.strip() not in drop)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_html(raw: bytes, base_url: str, min_chars: int, drop: set = frozenset(), extractor: str = "auto") -> dict:
    """Разбор страницы: заголовок, все ссылки, основной текст. Выполняется в потоке."""
    soup = BeautifulSoup(raw, HTML_PARSER)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if not title and soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
    base_tag = soup.find("base", href=True)
    base = urljoin(base_url, base_tag["href"]) if base_tag else base_url
    links = [urljoin(base, a["href"].strip()) for a in soup.find_all("a", href=True) if a["href"].strip()]

    text = ""
    if trafilatura is not None and extractor != "fallback":
        try:
            text = (trafilatura.extract(raw, url=base_url, output_format="markdown", include_tables=True,
                                        include_comments=False, include_links=False, include_images=False,
                                        favor_recall=True) or "").strip()
        except Exception:
            text = ""
    text = strip_lines(text, drop)
    if len(text) < min_chars:
        fb = strip_lines(fallback_text(soup), drop)
        if len(fb) > len(text):
            text = fb
    return {"title": re.sub(r"\s+", " ", title).strip()[:300], "links": links, "text": normalize_text(text)}


NOISE_KEY = re.compile(r"(^|_)ids?$|_slug$|^(slug|color|logo|sort_order|priority|is_favorite|snapshot_at|"
                       r"competition_list_url|created_at|updated_at|deleted_at|logo_url|color_code|cover_url|model_type|"
                       r"video_player_code|tandem_title|diploma_template|events|similar_directions)$")
LABELS = {
    "name": "название", "is_accredited": "аккредитована", "tags": "теги", "grants": "гранты", "amount": "сумма",
    "score_from": "баллы от", "score_to": "баллы до", "score_range": "диапазон баллов", "year": "год",
    "stipends": "стипендии", "is_grant_available": "гранты доступны", "prices": "стоимость обучения",
    "price": "цена", "type_label": "тип", "last_year_threshold_score": "проходной балл прошлого года",
    "avg_threshold_score": "средний проходной балл", "threshold_budget": "проходной на бюджет",
    "threshold_paid": "проходной на платное", "year_from": "с года", "year_to": "по год", "years_count": "лет",
    "places": "места", "budget": "бюджет", "paid": "платные", "exams": "вступительные испытания",
    "title": "предмет", "type": "тип", "min_score": "мин. балл", "direction": "направление", "code": "код",
    "program": "программа", "education_level": "уровень образования", "education_form": "форма обучения",
    "education_location": "город", "study_duration": "срок обучения", "months": "месяцев",
    "partners": "партнёры", "items": "список", "description": "описание",
    "strategic_direction": "стратегическое направление", "min_admission_score": "минимальный балл",
    "admissions_stat": "статистика приёма", "applications_count": "заявлений", "competition": "конкурс",
    "consent_count": "согласий", "enrolled_count": "зачислено", "avg_score": "средний балл",
    "admissions_history": "история приёма", "plan": "план", "scores": "баллы по годам", "min": "мин.",
    "avg_budget": "средний на бюджет", "max_budget": "макс. на бюджет", "avg_paid": "средний на платное",
    "max_paid": "макс. на платное", "all_places": "места по годам", "target": "целевая квота",
    "special": "особая квота", "combined": "совмещённые", "exclusive": "отдельная квота",
    "paid_for_foreign": "платные для иностранцев", "paid_for_college": "платные (после колледжа)",
    "paid_for_he": "платные (после высшего)", "target_places": "целевые места",
    "combined_places": "совмещённые места", "learning_plans": "учебные планы", "term_number": "семестр",
    "subjects": "дисциплины", "subject": "дисциплина", "pass_form": "форма контроля",
    "options": "варианты по выбору", "olympiads": "олимпиады", "professions_grouped": "профессии",
}


def clean(v):
    """Значение для текста: bool → да/нет, HTML → обычный текст."""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, str):
        if re.search(r"<[a-zA-Z/][^>]*>", v):
            v = BeautifulSoup(re.sub(r"</p>|</ul>|</ol>|<br\s*/?>", "\n", re.sub(r"<li[^>]*>", "\n- ", v)),
                          HTML_PARSER).get_text()
        return normalize_text(v)
    return v


def is_empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def visible(d: dict) -> list[tuple[str, object]]:
    """Поля объекта, которые попадают в текст (type скрыт, если рядом есть читаемый type_label)."""
    return [(k, v) for k, v in d.items() if not NOISE_KEY.search(k) and not (k == "type" and "type_label" in d)]


def is_flat(v) -> bool:
    return isinstance(v, dict) and not any(isinstance(x, (dict, list)) and x for x in v.values())


def inline(d: dict) -> str:
    """Плоский объект → одна строка «поле: значение; поле: значение»."""
    p = [(LABELS.get(k, k), clean(x)) for k, x in visible(d) if not is_empty(clean(x))]
    if len(p) == 1 and "year" in d:
        return ""
    if len(p) == 1:
        return str(p[0][1])
    return "; ".join(f"{k}: {x}" for k, x in p)


def plan_lines(terms) -> list[str]:
    """Учебный план → по строке на семестр: «Физика (Зачет, Экзамен); Спорт (Зачет) [на выбор: ...]»."""
    lines = []
    for t in terms or []:
        subjects: dict[str, dict] = {}
        for sub in t.get("subjects") or []:
            item = subjects.setdefault(sub.get("subject", ""), {"forms": [], "options": []})
            if sub.get("pass_form") and sub["pass_form"] not in item["forms"]:
                item["forms"].append(sub["pass_form"])
            for o in sub.get("options") or []:
                if o.get("subject") and o["subject"] not in item["options"]:
                    item["options"].append(o["subject"])
        parts = [f"{name} ({', '.join(i['forms'])})" + (f" [на выбор: {', '.join(i['options'])}]" if i["options"] else "")
                 for name, i in subjects.items()]
        lines.append(f"Семестр {t.get('term_number')}: " + "; ".join(parts))
    return lines


def tidy_direction(d: dict) -> dict:
    """Сокращает ответ API направления: блоки content → разделы с заголовками, партнёры и учебный план → строки."""
    out: dict = {}
    for k, v in d.items():
        if k == "content" and isinstance(v, list):
            for block in sorted((b for b in v if isinstance(b, dict)), key=lambda b: (b.get("content_type") or {}).get("sort_order", 0)):
                title = (block.get("content_type") or {}).get("title")
                if title and block.get("content"):
                    out[title] = block["content"]
        elif k == "partners" and isinstance(v, list):
            out[k] = [f"{g.get('type_label') or ''}: " + ", ".join(i.get("name", "") for i in g.get("items") or [])
                      for g in v if isinstance(g, dict)]
        elif k == "learning_plans" and isinstance(v, list):
            out[k] = [line for p in v if isinstance(p, dict) for line in plan_lines(p.get("plan"))]
        else:
            out[k] = v
    return out


def dig(d, *keys):
    """d[k1][k2]... без ошибок, если на пути нет поля."""
    for k in keys:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def find_first(data, key: str):
    """Первое непустое значение поля `key` на любой глубине JSON."""
    if isinstance(data, dict):
        if data.get(key) not in (None, "", [], {}):
            return data[key]
        data = list(data.values())
    if isinstance(data, list):
        for v in data:
            found = find_first(v, key)
            if found is not None:
                return found
    return None


def json_to_md(data, level: int = 0) -> list[str]:
    """JSON → вложенный список. Пустые значения и технические поля пропускаются, плоские объекты — в одну строку."""
    pad = "  " * level
    if isinstance(data, dict):
        items = [(LABELS.get(k, k), v) for k, v in visible(data)]
    elif isinstance(data, list):
        items = [("", v) for v in data]
    else:
        items = [("", data)]
    lines: list[str] = []
    for label, v in items:
        v = clean(v)
        if is_empty(v):
            continue
        head = f"{pad}- **{label}**:" if label else f"{pad}-"
        if is_flat(v):
            text = inline(v)
            if text:
                lines.append(f"{head} {text}")
        elif isinstance(v, (dict, list)):
            sub = json_to_md(v, level + 1)
            if sub:
                lines.append(head)
                lines += sub
        elif isinstance(v, str) and "\n" in v:
            lines.append(head)
            lines += [f"{pad}  {ln}" for ln in v.split("\n") if ln.strip()]
        else:
            lines.append(f"{head} {v}")
    return lines


def remove_boilerplate(out: Path, min_pages: int, min_run: int = 3) -> tuple[int, int]:
    """После обхода убирает из сохранённых страниц меню, списки центров и подвал: подряд идущие строки (не меньше min_run), каждая из которых
    повторяется на min_pages и более страницах. Одиночные повторяющиеся строки остаются: это, например, должность («Заведующий кафедрой»)
    рядом с уникальной фамилией. Заголовок страницы остаётся; страницы, в которых после очистки нет текста, удаляются.
    Возвращает (изменено страниц, удалено страниц)."""
    pages = {}
    for path in sorted((out / "pages").rglob("*.md")):
        head, _, body = path.read_text(encoding="utf-8").removeprefix("---\n").partition("\n---\n")
        pages[path] = (head, body.split("\n"))
    freq: Counter = Counter()
    for _, lines in pages.values():
        freq.update({ln.strip() for ln in lines if ln.strip() and not ln.startswith("# ")})
    changed = removed = 0
    for path, (head, lines) in pages.items():
        idx = [k for k, ln in enumerate(lines) if ln.strip() and not ln.startswith("# ")]
        drop, run = set(), []
        for k in idx + [None]:
            if k is not None and freq[lines[k].strip()] >= min_pages:
                run.append(k)
                continue
            if len(run) >= min_run:
                drop.update(run)
            run = []
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(ln for k, ln in enumerate(lines) if k not in drop)).strip()
        content = "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.startswith("# "))
        if len(content) < 40:
            path.unlink()
            removed += 1
        elif text != "\n".join(lines).strip():
            path.write_text("---\n" + head + "\n---\n\n" + text + "\n", encoding="utf-8")
            changed += 1
    return changed, removed


class RateLimiter:
    """Не чаще одного запроса в `delay` секунд суммарно по всем воркерам."""

    def __init__(self, delay: float):
        self.delay = delay
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.delay
        if start > now:
            await asyncio.sleep(start - now)


def write_overviews(programs, folder):
    """Короткие перечни направлений по уровням образования (код, название и профили, без мест и баллов).
    Зачем: подробные сводки режутся на десятки фрагментов, и на вопрос «какие программы есть?» поиск не может собрать
    полный список. Перечень занимает 2–6 фрагментов, и вопрос находит его целиком."""
    by_level = defaultdict(lambda: defaultdict(set))
    for p in programs:
        by_level[p["level"] or "уровень не указан"][(p["code"] or "", p["program"] or "")].add(p["name"] or "")
    for level, directions in by_level.items():
        n_profiles = sum(len(names) for names in directions.values())
        title = f"Перечень направлений подготовки: {level}"
        lines = [f"# {title}", "", f"Направлений: {len(directions)}, программ (профилей): {n_profiles}.", ""]
        for (code, program), names in sorted(directions.items()):
            profiles = sorted(names - {program})
            lines.append(f"- {code} {program}".strip() + (" — профили: " + "; ".join(profiles) if profiles else ""))
        header = {"title": title, "type": "summary", "programs": n_profiles}
        front = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in header.items()) + "\n---\n\n"
        slug = re.sub(r"[^\w\-]+", "-", f"{level}__перечень").strip("-")
        (Path(folder) / f"{slug}.md").write_text(front + "\n".join(lines) + "\n", encoding="utf-8")
    return len(by_level)


class Scraper:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out = Path(args.out)
        (self.out / "pages").mkdir(parents=True, exist_ok=True)
        (self.out / "files").mkdir(exist_ok=True)

        starts = [u if "://" in u else "https://" + u for u in args.urls]
        self.starts = [s for s in (normalize(u) for u in starts) if s]
        self.patterns = [re.compile(p) for p in args.allow]
        self.deny = [re.compile(p) for p in args.deny]
        self.hosts = {site_key(s) for s in self.starts}

        self.queue: asyncio.Queue = asyncio.Queue()
        self.seen: set[str] = set()
        self.finals: dict[str, str] = {}
        self.text_hashes: dict[str, str] = {}
        self.file_hashes: dict[str, str] = {}
        self.list_info: dict[str, dict] = {}
        self.programs: dict[str, dict] = {}
        self.api_pages: dict[str, str] = {}
        self.rows: list[tuple] = []
        self.stats: Counter = Counter()
        self.limiter = RateLimiter(args.delay)
        self.robots: dict[str, RobotFileParser] = {}
        self.client: httpx.AsyncClient | None = None


    def allowed(self, url: str) -> bool:
        if ext_of(url) in SKIP_EXTS or any(p.search(url) for p in self.deny):
            return False
        if self.args.docs_from_site and ext_of(url) in DOC_EXTS and site_key(url) in self.hosts:
            return True
        if self.patterns:
            return any(p.search(url) for p in self.patterns)
        return site_key(url) in self.hosts

    def record(self, url: str, status: str, reason: str = "", found_on: str = "", path: str = "") -> None:
        self.rows.append((url, status, reason, found_on, path))
        self.stats[status] += 1
        if found_on == "(start)" and status != "done":
            log(f"⚠ стартовый адрес не обработан: {url} — {status}: {reason}")
        if sum(self.stats.values()) % 25 == 0:
            log(f"  обработано {sum(self.stats.values())} · сохранено {self.stats['done']} · "
                f"дубликатов {self.stats['duplicate']} · пропущено {self.stats['skipped']} · "
                f"ошибок {self.stats['error']} · в очереди {self.queue.qsize()}")

    def discover(self, raw: str, found_on: str, force: bool = False) -> None:
        """Любая найденная ссылка либо уходит в очередь, либо записывается как skipped/duplicate."""
        url = normalize(raw) if re.match(r"^https?://", raw, re.I) else None
        if url is None:
            return
        if url in self.seen:
            return
        self.seen.add(url)
        if not force and not self.allowed(url):
            self.record(url, "skipped", "not_allowed", found_on)
            return
        self.queue.put_nowait((url, found_on))


    async def robots_ok(self, url: str) -> bool:
        p = urlparse(url)
        key = p.netloc.lower()
        if key not in self.robots:
            lines: list[str] = []
            try:
                r = await self.client.get(f"{p.scheme}://{p.netloc}/robots.txt")
                if r.status_code == 200:
                    lines = r.text.splitlines()
            except httpx.HTTPError:
                pass
            rp = RobotFileParser()
            rp.parse(lines)
            self.robots[key] = rp
            delay = rp.crawl_delay(self.args.user_agent)
            if delay:
                self.limiter.delay = max(self.limiter.delay, float(delay))
        return self.robots[key].can_fetch(self.args.user_agent, url)

    async def run(self) -> None:
        a = self.args
        async with httpx.AsyncClient(
            headers={"User-Agent": a.user_agent, "Accept-Language": "ru,en;q=0.8"},
            follow_redirects=True, timeout=a.timeout, verify=not a.insecure,
        ) as client:
            self.client = client
            for s in self.starts:
                self.discover(s, "(start)", force=True)
            if a.ids_apis:
                await self.load_api_links()
            workers = [asyncio.create_task(self.worker()) for _ in range(a.concurrency)]
            try:
                await self.queue.join()
            finally:
                for w in workers:
                    w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

    async def load_api_links(self) -> None:
        """Списки, которые сайт подгружает через JS: берём верхний `id` каждой записи и строим ссылки по шаблону."""
        a = self.args
        for list_url in a.ids_apis:
            try:
                await self.limiter.wait()
                r = await self.client.get(list_url)
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError) as e:
                log(f"⚠ не удалось получить список из {list_url}: {type(e).__name__}: {e}")
                continue
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [])
            ids = [item["id"] for item in data if isinstance(item, dict) and item.get("id") is not None]
            item_by_id = {item["id"]: item for item in data if isinstance(item, dict) and item.get("id") is not None}
            for i in ids:
                page = normalize(a.ids_link.format(id=i))
                if a.detail_api and page:
                    api = normalize(a.detail_api.format(id=i))
                    self.api_pages[api] = page
                    self.list_info[api] = item_by_id.get(i, {})
                    self.discover(api, list_url, force=True)
                else:
                    self.discover(a.ids_link.format(id=i), list_url, force=True)
            log(f"Из API получено записей: {len(ids)} ({list_url})")

    async def worker(self) -> None:
        while True:
            url, found_on = await self.queue.get()
            try:
                await self.process(url, found_on)
            except Skip as e:
                self.record(url, "skipped", str(e), found_on)
            except FetchError as e:
                self.record(url, "error", str(e), found_on)
            except Exception as e:
                self.record(url, "error", f"internal: {type(e).__name__}: {e}"[:200], found_on)
            finally:
                self.queue.task_done()


    async def fetch(self, url: str) -> dict:
        a = self.args
        for attempt in range(1, a.retries + 1):
            await self.limiter.wait()
            pause = 0.0
            try:
                async with self.client.stream("GET", url) as r:
                    if r.status_code in RETRY_STATUS:
                        err = f"HTTP {r.status_code}"
                        retry_after = r.headers.get("retry-after", "")
                        pause = float(retry_after) if retry_after.isdigit() else 0.0
                        if r.status_code == 429:
                            self.limiter.delay = min(self.limiter.delay * 1.5, 10.0)
                    elif r.status_code != 200:
                        raise FetchError(f"HTTP {r.status_code}")
                    else:
                        return await self.read(r, url)
            except httpx.HTTPError as e:
                err = type(e).__name__
            if attempt == a.retries:
                raise FetchError(err)
            await asyncio.sleep(max(pause, a.retry_delay * 2 ** (attempt - 1)))
        raise FetchError("no attempts")

    async def read(self, r: httpx.Response, url: str) -> dict:
        final = normalize(str(r.url)) or url
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        name = filename_from_disposition(r.headers.get("content-disposition", ""))
        is_file = ctype in DOC_CTYPES or (ctype in GENERIC_BINARY and (
            ext_of(final) in DOC_EXTS or Path(name).suffix.lower() in DOC_EXTS))
        is_json = url in self.api_pages and ctype.endswith("json")
        if not is_file and not is_json and ctype not in HTML_CTYPES:
            raise Skip(f"content-type {ctype or 'unknown'}")
        limit = self.args.max_file_mb * 1024 * 1024 if is_file else MAX_HTML_BYTES
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > limit:
                if is_file:
                    raise Skip("too_large")
                break
        return {"final": final, "ctype": ctype, "body": bytes(buf), "is_file": is_file, "is_json": is_json, "name": name}


    async def process(self, url: str, found_on: str) -> None:
        if self.args.max_pages and self.stats["done"] >= self.args.max_pages:
            raise Skip("max_pages")
        if url not in self.api_pages and not await self.robots_ok(url):
            raise Skip("robots")
        res = await self.fetch(url)
        final = res["final"]
        if final != url:
            if not self.allowed(final):
                raise Skip("redirect_not_allowed")
            owner = self.finals.get(final)
            if owner is not None and owner != url:
                self.record(url, "duplicate", f"redirect to {owner}", found_on)
                return
            self.seen.add(final)
        self.finals[final] = url
        if res["is_file"]:
            self.save_file(url, res, found_on)
        elif res["is_json"]:
            self.save_json(url, res, found_on)
        else:
            await self.save_page(url, res, found_on)

    async def save_page(self, url: str, res: dict, found_on: str) -> None:
        final = res["final"]
        parsed = await asyncio.to_thread(parse_html, res["body"], final, self.args.min_text, self.args.drop_lines, self.args.extractor)
        for href in parsed["links"]:
            self.discover(href, final)
        self.write_page(url, final, parsed["title"], parsed["text"], found_on)

    def save_json(self, url: str, res: dict, found_on: str) -> None:
        try:
            data = json.loads(res["body"])
        except ValueError:
            raise Skip("bad json")
        if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)):
            data = data["data"]
        if isinstance(data, dict):
            self.collect_program(url, data)
            data = tidy_direction(data)
        title = " ".join(str(find_first(data, k)) for k in ("code", "program") if find_first(data, k))
        if find_first(data, "name"):
            title += f" — {find_first(data, 'name')}" if title else str(find_first(data, "name"))
        self.write_page(url, self.api_pages[url], title, "\n".join(json_to_md(data)), found_on)

    def write_page(self, url: str, final: str, title: str, text: str, found_on: str) -> None:
        a = self.args
        if a.title_suffix:
            title = title.removesuffix(a.title_suffix)
        text = strip_lines(text, a.drop_lines)
        if not text:
            self.record(url, "done", "empty", found_on)
            return
        h = text_hash(text)
        dup = self.text_hashes.get(h)
        if dup is not None and dup != url:
            self.record(url, "duplicate", f"same text as {dup}", found_on)
            return
        self.text_hashes[h] = url
        rel = page_relpath(final)
        dest = self.out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        header = {"url": final, "title": title}
        front = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in header.items()) + "\n---\n\n"
        body = text if text.lstrip().startswith("#") or not title else f"# {title}\n\n{text}"
        dest.write_text(front + body + "\n", encoding="utf-8")
        self.record(url, "done", "", found_on, rel)

    def save_file(self, url: str, res: dict, found_on: str) -> None:
        sha = hashlib.sha256(res["body"]).hexdigest()
        dup = self.file_hashes.get(sha)
        if dup is not None and dup != url:
            self.record(url, "duplicate", f"same file as {dup}", found_on)
            return
        self.file_hashes[sha] = url
        name = safe_name(res["name"] or Path(urlparse(res["final"]).path).name or "file")
        if Path(name).suffix.lower() not in DOC_EXTS:
            name += DOC_CTYPES.get(res["ctype"], "")
        rel = f"files/{sha[:12]}_{name}"
        (self.out / rel).write_bytes(res["body"])
        self.record(url, "done", "", found_on, rel)


    def collect_program(self, url: str, d: dict) -> None:
        places, thr = d.get("places") or {}, d.get("last_year_threshold_score") or {}
        self.programs[url] = {
            "page": self.api_pages[url], "name": d.get("name"),
            "code": dig(d, "direction", "code"), "program": dig(d, "direction", "program"),
            "level": dig(d, "direction", "education_level", "name"), "form": dig(d, "direction", "education_form", "name"),
            "city": dig(d, "direction", "education_location", "name"), "duration": dig(d, "study_duration", "name"),
            "unit": dig(self.list_info.get(url), "guide_direction", "guide_formative_unit", "name"),
            "budget": places.get("budget"), "paid": places.get("paid"),
            "threshold": thr.get("threshold_budget"), "threshold_year": thr.get("year")}

    def write_summaries(self) -> None:
        """Сводки: по одному файлу на уровень образования и срок обучения — какие направления и программы есть."""
        groups: dict[tuple, list] = defaultdict(list)
        for r in self.programs.values():
            groups[(r["level"] or "уровень не указан", r["duration"] or "срок не указан")].append(r)
        folder = self.out / "summary"
        folder.mkdir(exist_ok=True)
        for (level, duration), items in groups.items():
            by_dir: dict[tuple, list] = defaultdict(list)
            for r in items:
                by_dir[(r["code"] or "", r["program"] or "")].append(r)
            title = f"Направления подготовки: {level}, срок обучения {duration}"
            lines = [f"# {title}", "", f"Направлений: {len(by_dir)}, программ (профилей): {len(items)}.", ""]
            for (code, program), rs in sorted(by_dir.items()):
                lines += [f"## {code} {program}".strip(), ""]
                for r in sorted(rs, key=lambda r: r["name"] or ""):
                    details = [x for x in (r["form"], r["city"], r["unit"]) if x]
                    if r["budget"] is not None:
                        details.append(f"мест на бюджет: {r['budget']}")
                    if r["paid"] is not None:
                        details.append(f"платных мест: {r['paid']}")
                    if r["threshold"] is not None:
                        details.append(f"проходной на бюджет ({r['threshold_year']}): {r['threshold']}")
                    details.append(f"страница: {r['page']}")
                    lines.append(f"- **{r['name']}**: " + "; ".join(details))
                lines.append("")
            header = {"title": title, "type": "summary", "programs": len(items)}
            front = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in header.items()) + "\n---\n\n"
            slug = re.sub(r"[^\w\-]+", "-", f"{level}__{duration}").strip("-")
            (folder / f"{slug}.md").write_text(front + "\n".join(lines), encoding="utf-8")
        n_overviews = write_overviews(self.programs.values(), folder)
        if groups:
            log(f"Сводных файлов по направлениям: {len(groups)} и перечней по уровням: {n_overviews} (summary/)")


    def write_links(self) -> None:
        with (self.out / "links.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["url", "status", "reason", "found_on", "saved_path"])
            w.writerows(sorted(self.rows))


def load_settings(source: str = "priem") -> argparse.Namespace:
    if source not in SOURCES:
        sys.exit(f"Неизвестный источник {source!r}. Доступны: {', '.join(SOURCES)}")
    cfg = {"urls": START_URLS, "out": OUT_DIR, "allow": ALLOW, "ids_apis": IDS_APIS, "detail_api": DETAIL_API, "docs_from_site": DOCS_FROM_SITE,
           "concurrency": CONCURRENCY, "delay": DELAY, "retries": RETRIES, "retry_delay": RETRY_DELAY, "drop_lines": [], "title_suffix": "",
           "deny": [], "max_pages": 0, "boilerplate_min_pages": 0, "extractor": "auto", **SOURCES[source]}
    if any(u.startswith("ВСТАВЬТЕ") for u in cfg["urls"] + cfg["allow"]):
        sys.exit(f"Заполните адрес и шаблоны для источника {source!r} в блоке SOURCES в начале файла")
    if cfg["ids_apis"] and "{id}" not in IDS_LINK:
        sys.exit("IDS_LINK должен содержать {id}")
    return argparse.Namespace(
        urls=cfg["urls"], out=cfg["out"], allow=cfg["allow"], ids_apis=cfg["ids_apis"], ids_link=IDS_LINK, detail_api=cfg["detail_api"],
        docs_from_site=cfg["docs_from_site"], drop_lines=set(cfg["drop_lines"]), title_suffix=cfg["title_suffix"],
        deny=cfg["deny"], max_pages=cfg["max_pages"], boilerplate_min_pages=cfg["boilerplate_min_pages"], extractor=cfg["extractor"],
        concurrency=max(1, cfg["concurrency"]), delay=cfg["delay"], timeout=TIMEOUT, retries=max(1, cfg["retries"]),
        retry_delay=cfg["retry_delay"], max_file_mb=MAX_FILE_MB, min_text=MIN_TEXT, insecure=INSECURE,
        user_agent=USER_AGENT)


async def amain(args: argparse.Namespace) -> None:
    scraper = Scraper(args)
    try:
        await scraper.run()
    finally:
        scraper.write_links()
        scraper.write_summaries()
        if args.boilerplate_min_pages:
            changed, removed = remove_boilerplate(scraper.out, args.boilerplate_min_pages)
            log(f"Шаблонные строки (повторы на {args.boilerplate_min_pages}+ страницах) убраны: изменено страниц {changed}, удалено пустых {removed}")
        s = scraper.stats
        log(f"Готово: сохранено {s['done']}, дубликатов {s['duplicate']}, пропущено {s['skipped']}, "
            f"ошибок {s['error']}. Результат в {scraper.out}/ (pages/, files/, links.csv)")


def main() -> None:
    args = load_settings(sys.argv[1] if len(sys.argv) > 1 else "priem")
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        log("Прервано.")
        sys.exit(130)


if __name__ == "__main__":
    main()
