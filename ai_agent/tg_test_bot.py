"""
Тестовый Telegram-бот: диалог через POST /chat (AI Agent API).

Переменные окружения:
  TELEGRAM_BOT_TOKEN — токен от @BotFather
  AI_AGENT_API_URL    — базовый URL API (локально :8000; в compose — http://ai-agent-api:8000)

Перед polling снимается webhook: иначе Telegram не шлёт апдейты в getUpdates.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from .agent import GREETING_TEXT
from .version import __version__

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

DEFAULT_API_URL = "http://127.0.0.1:8000"

_START_RE = re.compile(r"^/start(?:@\S*)?(?:\s|$)", re.IGNORECASE)
_VERSION_RE = re.compile(r"^/version(?:@\S*)?(?:\s|$)", re.IGNORECASE)
_HELP_RE = re.compile(r"^/help(?:@\S*)?(?:\s|$)", re.IGNORECASE)
_CHAT_CMD_RE = re.compile(r"^/chat(?:@\S*)?(?:\s|$)", re.IGNORECASE)

TG_TEXT_LIMIT = 4096


async def _filter_start(message: Message) -> bool:
    t = (message.text or "").strip()
    return bool(t and _START_RE.match(t))


async def _filter_version(message: Message) -> bool:
    t = (message.text or "").strip()
    return bool(t and _VERSION_RE.match(t))


async def _filter_help(message: Message) -> bool:
    t = (message.text or "").strip()
    return bool(t and _HELP_RE.match(t))


async def _filter_chat_cmd(message: Message) -> bool:
    t = (message.text or "").strip()
    return bool(t and _CHAT_CMD_RE.match(t))


def _api_base() -> str:
    return os.getenv("AI_AGENT_API_URL", DEFAULT_API_URL).rstrip("/")


async def fetch_greeting() -> str | None:
    url = f"{_api_base()}/greeting"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json().get("greeting")


async def call_agent_chat(message_text: str, session_id: str) -> tuple[str, str | None]:
    url = f"{_api_base()}/chat"
    payload = {"message": message_text, "session_id": session_id}
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
    return data.get("reply", ""), data.get("call_signal")


async def session_reset(session_id: str) -> None:
    url = f"{_api_base()}/session/reset"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json={"session_id": session_id})
        response.raise_for_status()


def _split_telegram_chunks(text: str, limit: int = TG_TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


async def _send_long_reply(message: Message, text: str) -> None:
    for chunk in _split_telegram_chunks(text):
        await message.answer(chunk)


def _help_text() -> str:
    return (
        "Как тестировать ассистента\n\n"
        "• /chat — начать новый диалог с ассистентом (сбрасывает предыдущий контекст)\n"
        "• После /chat пишите обычным текстом — сообщения уходят в AI Agent\n"
        "• /start — описание бота\n"
        "• /version — версия бота\n\n"
        f"Версия: {__version__}"
    )


async def cmd_start(message: Message) -> None:
    logger.info("cmd_start chat_id=%s", message.chat.id)
    text = (
        "Тестовый бот клиники Psy Family.\n\n"
        "/chat — начать новый диалог с ассистентом\n"
        "/help — подробнее о командах\n"
        "/version — версия бота\n\n"
        f"Версия: {__version__}"
    )
    try:
        await message.answer(text)
    except TelegramAPIError:
        logger.exception("Telegram API error in cmd_start")


async def cmd_version(message: Message) -> None:
    logger.info("cmd_version chat_id=%s", message.chat.id)
    try:
        await message.answer(f"Версия бота: {__version__}\nAPI: {_api_base()}")
    except TelegramAPIError:
        logger.exception("Telegram API error in cmd_version")


async def cmd_help(message: Message) -> None:
    logger.info("cmd_help chat_id=%s", message.chat.id)
    try:
        await message.answer(_help_text())
    except TelegramAPIError:
        logger.exception("Telegram API error in cmd_help")


async def on_chat_command(message: Message) -> None:
    """Команда /chat — начать новый диалог с ассистентом."""
    session_id = str(message.chat.id)
    logger.info("on_chat_command chat_id=%s", message.chat.id)
    try:
        await session_reset(session_id)
        greeting = await fetch_greeting() or GREETING_TEXT
        await message.answer(greeting)
    except (httpx.HTTPError, ValueError, KeyError):
        await message.answer(GREETING_TEXT)
        logger.warning("GET /greeting недоступен, локальное приветствие")


async def on_plain_text(message: Message) -> None:
    if not message.text:
        return
    session_id = str(message.chat.id)
    try:
        reply, signal = await call_agent_chat(message.text, session_id)
        text = reply or "(пустой ответ)"
        if signal:
            text = f"{text}\n\n[signal: {signal}]"
        await _send_long_reply(message, text)
        if signal in ("end_call", "transfer_operator"):
            await session_reset(session_id)
            await message.answer("— Диалог завершён, контекст сброшен —")
    except httpx.HTTPStatusError as e:
        logger.exception("HTTP error from API")
        await message.answer(
            f"Ошибка API: {e.response.status_code}. Проверьте LLM_API_KEY и логи ai-agent-api."
        )
    except httpx.RequestError as e:
        logger.exception("Request to API failed")
        await message.answer(f"Не удалось достучаться до API: {e!s}")


async def on_unknown_slash(message: Message) -> None:
    await message.answer(
        "Эта команда не используется. Чтобы говорить с ассистентом, "
        "отправьте обычный текст без «/» в начале (см. /help). "
        "Или нажмите /chat для пробного ответа."
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("Задайте TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    bot = Bot(token=token)
    try:
        await bot.set_my_short_description(f"Тест AI Psy Family · v{__version__}")
    except TelegramAPIError:
        logger.warning("Не удалось задать short_description бота (версия в чате всё равно в /start и /help)")

    dp = Dispatcher()
    dp.message.register(cmd_start, _filter_start)
    dp.message.register(cmd_version, _filter_version)
    dp.message.register(cmd_help, _filter_help)
    dp.message.register(on_chat_command, _filter_chat_cmd)
    dp.message.register(on_plain_text, F.text, ~F.text.startswith("/"))
    dp.message.register(on_unknown_slash, F.text.startswith("/"))

    logger.info("PsyFamily tg test bot v%s, API %s", __version__, _api_base())
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Webhook снят (если был), дальше только long polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
