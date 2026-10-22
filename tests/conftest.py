import os
from pathlib import Path

for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "QDRANT_URL"):
    os.environ.pop(name, None)
os.environ.setdefault("LLM_API_KEY", "test-key")

import pytest

from rag import tools as tools_module
from rag.programs import Exam, Program

ROOT = Path(__file__).resolve().parent.parent


def make_program(**kw):
    base = dict(id=1, url="https://priem.mirea.ru/guide-direction?direction_id=1", file="guide-direction__aaa.md", code="09.03.04",
                direction="Программная инженерия", name="Системная и программная инженерия", level="бакалавриат", form="очная", city="г. Москва",
                institute="Институт информационных технологий", duration_months=48, budget_places=30, paid_places=60, prices={"Базовая": 400000},
                pass_budget=250, exams=[Exam(subject="Математика (профиль)", kind="Экзамен", min_score=50)])
    return Program(**{**base, **kw})


@pytest.fixture
def programs():
    return [
        make_program(),
        make_program(id=2, file="guide-direction__bbb.md", code="09.03.04", name="Разработка игр", budget_places=10, paid_places=100, prices={"Базовая": 500000}, pass_budget=270),
        make_program(id=3, file="guide-direction__ccc.md", code="01.03.04", direction="Прикладная математика", name="Анализ данных", budget_places=29, paid_places=60, prices={"Базовая": 379000}, pass_budget=280),
        make_program(id=4, file="guide-direction__ddd.md", code="09.04.04", direction="Программная инженерия", name="Анализ данных", level="магистратура", duration_months=24,
                     budget_places=20, paid_places=10, prices={"Базовая": 300000}, pass_budget=None),
    ]


@pytest.fixture
def tools(programs, monkeypatch):
    monkeypatch.setattr(tools_module, "load_programs", lambda: programs)
    return tools_module.Tools(search_docs=lambda q: [{"title": "Страница", "place": "https://x/doc", "text": f"текст про {q}", "chunk_ids": ["pages/x.md#0"]}])
