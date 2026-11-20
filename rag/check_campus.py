"""Проверка сервисов вуза без Telegram и без отправки пользовательских данных."""

from rag.agent import agent_answer
from rag.embed import embed
from rag.llm import chat, client
from rag.search import Searcher


def main():
    print("Модели vLLM:", ", ".join(model.id for model in client().models.list().data))
    reply = chat([{"role": "user", "content": "Ответь одним словом: готов."}])
    if not reply:
        raise RuntimeError("vLLM вернул пустой ответ")
    print("Генерация:", reply)
    print("Эмбеддинги:", embed(["Сроки подачи документов"]).shape)

    searcher = Searcher()
    question = "Какие документы нужны для подачи заявления на бакалавриат?"
    answer, sources, _ = agent_answer(searcher, question)
    if not answer or not sources:
        raise RuntimeError("Ассистент не вернул ответ с источниками")
    print(f"Ответ с источниками ({len(sources)}): {answer}")

    answer, sources, _ = agent_answer(searcher, "Сколько программ бакалавриата есть в базе?")
    if not answer or not sources.tool_calls:
        raise RuntimeError("Ассистент не вернул ответ с вызовом инструмента")
    print(f"Инструменты: {', '.join(sources.tool_calls)}. Ответ: {answer}")


if __name__ == "__main__":
    main()
