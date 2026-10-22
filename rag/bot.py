import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

from rag.ask import HISTORY_TURNS, plain, respond
from rag.search import Searcher
from rag.telegram_format import format_reply

GREETING = ("Здравствуйте! Я помощник приёмной комиссии РТУ МИРЭА. Задайте вопрос о поступлении: программы, баллы, "
            "стоимость, документы, сроки. Я отвечаю по материалам сайта priem.mirea.ru и указываю источники.\n\n"
            "/new — начать новый разговор.")

dp = Dispatcher()
searcher = Searcher()
histories = {}
locks = {}


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(GREETING)


@dp.message(Command("new"))
async def new(message: Message):
    histories.pop(message.chat.id, None)
    await message.answer("Начали сначала. Задайте вопрос.")


async def send(message: Message, html_text, plain_text):
    """Отправляет часть ответа с разметкой; если Telegram её не принял, тот же текст без разметки."""
    try:
        await message.answer(html_text, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest:
        logging.warning("Telegram не принял разметку, отправляю простым текстом")
        await message.answer(plain_text[:4096], disable_web_page_preview=True)


@dp.message()
async def handle(message: Message):
    if not message.text:
        await message.answer("Я понимаю только текстовые вопросы.")
        return
    chat_id = message.chat.id
    async with locks.setdefault(chat_id, asyncio.Lock()):
        await message.bot.send_chat_action(chat_id, "typing")
        history = histories.get(chat_id, [])
        text, found, query = await asyncio.to_thread(respond, searcher, chat_id, message.text, history)
        for html_text, plain_text in format_reply(text.strip() or "Не удалось сформулировать ответ.", found):
            await send(message, html_text, plain_text)
        histories[chat_id] = (history + [{"role": "user", "content": message.text}, {"role": "assistant", "content": plain(text)}])[-2 * HISTORY_TURNS:]


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(os.environ["TELEGRAM_TOKEN"])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
