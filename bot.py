"""
Telegram-бот: ежедневная задача по математике ЕГЭ с math-ege.sdamgia.ru
Отправка каждый день в 08:00 МСК
"""

import os
import re
import random
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx
from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Диапазон ID задач на math-ege.sdamgia.ru
PROBLEM_ID_MIN = 1000
PROBLEM_ID_MAX = 700000

MSK = timezone(timedelta(hours=3))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Текущая задача (хранится между отправкой и нажатием кнопки)
current_problem: dict = {}


def clean_text(text: str) -> str:
    """Убирает мягкие переносы (­) и лишние пробелы/переносы строк."""
    text = text.replace("\u00ad", "")   # мягкий перенос
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def fetch_problem(problem_id: int) -> dict | None:
    """Загружает задачу по ID с math-ege.sdamgia.ru."""
    url = f"https://math-ege.sdamgia.ru/problem?id={problem_id}"

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Ошибка загрузки задачи {problem_id}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Основной блок задачи
    main_div = soup.find("div", class_="prob_maindiv")
    if not main_div:
        return None

    # Все блоки pbody: первый — задача, второй — решение
    pbodies = main_div.find_all("div", class_="pbody")
    if not pbodies:
        return None

    task_text = clean_text(pbodies[0].get_text(separator="\n"))
    if not task_text or len(task_text) < 5:
        return None

    # Решение — второй pbody (если есть)
    solution = ""
    if len(pbodies) >= 2:
        solution = clean_text(pbodies[1].get_text(separator="\n"))

    # Ответ — div.answer (display:none, но html содержит текст)
    answer = ""
    answer_div = main_div.find("div", class_="answer")
    if answer_div:
        raw = answer_div.get_text(separator=" ", strip=True)
        # «Ответ: -1» → «-1»
        answer = re.sub(r"[Оо]тв[её]т\s*:?\s*", "", raw).strip()

    # Тип задания (например, «Тип 6»)
    task_type = ""
    prob_nums = main_div.find("span", class_="prob_nums")
    if prob_nums:
        task_type = prob_nums.get_text(strip=True).replace("\u00ad", "")

    return {
        "id": problem_id,
        "url": url,
        "task_type": task_type,
        "task": task_text,
        "answer": answer or "—",
        "solution": solution or "—",
    }


async def get_random_problem() -> dict | None:
    """Берёт случайную задачу, повторяя попытки при неудаче."""
    for _ in range(15):
        problem_id = random.randint(PROBLEM_ID_MIN, PROBLEM_ID_MAX)
        problem = await fetch_problem(problem_id)
        if problem:
            return problem
    return None


def format_task_message(problem: dict, date_str: str) -> str:
    type_label = f"  _{problem['task_type']}_" if problem["task_type"] else ""
    return (
        f"📐 *Задача дня — {date_str}*{type_label}\n\n"
        f"{problem['task']}\n\n"
        f"[Открыть на сайте]({problem['url']})"
    )


async def send_daily_problem(bot: Bot) -> None:
    """Отправляет ежедневную задачу."""
    global current_problem

    now_msk = datetime.now(MSK).strftime("%d.%m.%Y")
    logger.info(f"Отправка задачи на {now_msk}...")

    problem = await get_random_problem()
    if not problem:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="⚠️ Не удалось загрузить задачу. Попробуй /task вручную.",
        )
        return

    current_problem = problem
    text = format_task_message(problem, now_msk)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Показать решение и ответ", callback_data="show_solution")]
    ])

    await bot.send_message(
        chat_id=CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard,
        disable_web_page_preview=False,
    )
    logger.info(f"Задача #{problem['id']} отправлена")


# ── Хэндлеры ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я присылаю задачу по математике ЕГЭ каждый день в *08:00 МСК*.\n\n"
        "Команды:\n"
        "/task — получить случайную задачу прямо сейчас\n"
        "/start — это сообщение",
        parse_mode="Markdown",
    )


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global current_problem

    msg = await update.message.reply_text("⏳ Загружаю задачу...")
    problem = await get_random_problem()

    if not problem:
        await msg.edit_text("❌ Не удалось загрузить задачу. Попробуй ещё раз.")
        return

    current_problem = problem
    now_msk = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    text = format_task_message(problem, now_msk)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Показать решение и ответ", callback_data="show_solution")]
    ])

    await msg.delete()
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard,
        disable_web_page_preview=False,
    )


async def callback_solution(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not current_problem:
        await query.message.reply_text("⚠️ Задача не найдена. Запроси новую через /task.")
        return

    p = current_problem
    text = (
        f"✅ *Ответ:* `{p['answer']}`\n\n"
        f"📖 *Решение:*\n{p['solution']}\n\n"
        f"[Смотреть полностью на сайте]({p['url']})"
    )

    # Telegram ограничивает сообщения 4096 символами
    if len(text) > 4096:
        text = text[:4090] + "…"

    await query.message.reply_text(
        text,
        parse_mode="Markdown",
        disable_web_page_preview=False,
    )


# ── Точка входа ────────────────────────────────────────────────────────────────

async def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан в .env")
    if not CHAT_ID:
        raise ValueError("CHAT_ID не задан в .env")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("task", cmd_task))
    app.add_handler(CallbackQueryHandler(callback_solution, pattern="^show_solution$"))

    # Планировщик: 08:00 МСК = 05:00 UTC
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        send_daily_problem,
        trigger=CronTrigger(hour=5, minute=0, timezone="UTC"),
        args=[app.bot],
        id="daily_problem",
        name="Ежедневная задача ЕГЭ",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Планировщик запущен — задача будет отправляться в 08:00 МСК (05:00 UTC)")

    logger.info("Бот запущен. Нажми Ctrl+C для остановки.")
    await app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
