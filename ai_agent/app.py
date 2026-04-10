"""HTTP API агента: стриминг для голоса, JSON /chat для Telegram и отладки."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Iterator

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool

from .agent import PsyFamilyAgent
from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from .schemas import ChatRequest, ChatResponse, GreetingResponse, SessionResetRequest
from .version import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = PsyFamilyAgent(
        api_key=LLM_API_KEY,
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
    )
    yield


app = FastAPI(
    title="Psy Family AI Agent",
    version=__version__,
    lifespan=lifespan,
)


def _chunk_text_iterator(agent: PsyFamilyAgent, message: str, session_id: str) -> Iterator[str]:
    for chunk in agent(message, session_id=session_id):
        yield chunk.text


@app.post("/chat_stream")
async def chat_stream(request: Request, body: ChatRequest):
    """Поток UTF-8 текста (text/plain) для интеграции с голосовым ботом / TTS."""
    agent: PsyFamilyAgent = request.app.state.agent

    def iterator():
        yield from _chunk_text_iterator(agent, body.message, body.session_id)

    return StreamingResponse(
        iterate_in_threadpool(iterator()),
        media_type="text/plain; charset=utf-8",
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    """Полный ответ JSON — для Telegram-бота и ручных тестов."""

    def run_chat() -> tuple[str, str | None]:
        agent: PsyFamilyAgent = request.app.state.agent
        try:
            parts: list[str] = []
            for chunk in agent(body.message, session_id=body.session_id):
                parts.append(chunk.text)
            reply = "".join(parts)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("Agent error for message: %s", body.message)
            reply = (
                "Извините, произошла техническая ошибка. "
                "Попробуйте повторить вопрос или начните новый диалог командой /chat."
            )
        signal = agent.get_call_signal(body.session_id)
        if signal in ("end_call", "transfer_operator"):
            agent.drop_session(body.session_id)
        return reply, signal

    reply, call_signal = await run_in_threadpool(run_chat)
    return ChatResponse(reply=reply, call_signal=call_signal)


@app.get("/greeting", response_model=GreetingResponse)
async def greeting(request: Request):
    """Текст приветствия агента (как в голосовом сценарии)."""

    def run() -> str:
        return request.app.state.agent.get_greeting()

    text = await run_in_threadpool(run)
    return GreetingResponse(greeting=text)


@app.post("/session/reset")
async def session_reset(request: Request, body: SessionResetRequest):
    """Сброс состояния сессии (для тестов в Telegram)."""

    def run() -> None:
        request.app.state.agent.drop_session(body.session_id)

    await run_in_threadpool(run)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ai_agent.app:app", host="0.0.0.0", port=8000)
