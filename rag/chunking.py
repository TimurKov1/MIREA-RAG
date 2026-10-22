import json
import re
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "priem"
MAX_CHARS = 1200
MIN_CHARS = 250


@dataclass
class Chunk:
    id: str
    url: str
    title: str
    level: str
    text: str


def chunk_body(body):
    chunks, cur = [], ""
    for line in body.strip().split("\n"):
        new_section = line.startswith("#") or line.startswith("- **")
        too_long = len(cur) + len(line) + 1 > MAX_CHARS
        if cur and (too_long or (new_section and len(cur) >= MIN_CHARS)):
            chunks.append(cur.strip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def level_of(url, title, body):
    m = re.search(r"\*\*уровень образования\*\*: (\w+)", body)
    if m:
        return m.group(1)
    if not url:
        return re.split(r"[/,]", title.split(": ")[-1])[0]
    section = url.replace("https://priem.mirea.ru/", "").split("/")[0]
    levels = {"first-degree": "бакалавриат", "magistracy": "магистратура", "phd": "аспирантура", "spo": "СПО"}
    return levels.get(section, "общее")


def load_chunks(folder=DATA):
    """Фрагменты из папки с данными парсера (pages/ и summary/). У основной папки id как раньше, у остальных с префиксом имени папки."""
    folder = Path(folder).resolve()
    prefix = "" if folder == DATA else f"{folder.name}/"
    chunks = []
    for path in sorted((folder / "pages").rglob("*.md")) + sorted((folder / "summary").glob("*.md")):
        head, _, body = path.read_text(encoding="utf-8").removeprefix("---\n").partition("\n---\n")
        fields = dict(line.split(": ", 1) for line in head.split("\n"))
        url = json.loads(fields.get("url", '""'))
        title = json.loads(fields["title"])
        level = level_of(url, title, body)
        for i, part in enumerate(chunk_body(body)):
            chunks.append(Chunk(f"{prefix}{path.relative_to(folder)}#{i}", url, title, level, f"[{level}] {title}\n\n{part}"))
    return chunks


if __name__ == "__main__":
    chunks = load_chunks()
    lengths = sorted(len(c.text) for c in chunks)
    print(f"Фрагментов: {len(chunks)}; длина: мин {lengths[0]}, медиана {lengths[len(lengths) // 2]}, макс {lengths[-1]}")
    for c in chunks[1000:1002]:
        print("\n" + "=" * 80 + f"\n{c.id}\n" + "-" * 80 + f"\n{c.text}")
