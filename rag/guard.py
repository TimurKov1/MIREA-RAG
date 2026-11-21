import json
import os
import time
from collections import defaultdict, deque
from threading import Lock

MAX_QUESTION_CHARS = 500 
PER_MINUTE = 5 
PER_DAY = 100
TOTAL_PER_DAY = 2000
LOG_PATH = "logs/qa.jsonl"

_by_user = defaultdict(deque)
_all = deque()
_lock = Lock()


def check(user, question):
    if len(question) > MAX_QUESTION_CHARS:
        return f"Вопрос слишком длинный (больше {MAX_QUESTION_CHARS} символов). Сформулируйте его короче."
    with _lock:
        now = time.time()
        for times in (_by_user[user], _all):
            while times and now - times[0] > 86400:
                times.popleft()
        if len(_all) >= TOTAL_PER_DAY:
            return "На сегодня лимит запросов к помощнику исчерпан. Попробуйте завтра или обратитесь в приёмную комиссию."
        if len(_by_user[user]) >= PER_DAY:
            return "Вы исчерпали дневной лимит вопросов. Попробуйте завтра."
        if sum(1 for t in _by_user[user] if now - t < 60) >= PER_MINUTE:
            return "Слишком много вопросов подряд. Подождите минуту."
        _by_user[user].append(now)
        _all.append(now)
        return None


def forget_user(user):
    with _lock:
        _by_user.pop(user, None)


def log(**record):
    if os.environ.get("RAG_LOG_DIALOGS", "1") == "0":
        return
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")