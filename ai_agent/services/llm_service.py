import json
import os
import time
from typing import Generator, List, Dict, Any, Type

import httpx
from openai import OpenAI
from pydantic import BaseModel

from ..config import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL
from ..utils.trace_logger import record_llm_call

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _build_http_client() -> httpx.Client | None:
    """Build an httpx client with SOCKS5 proxy support if LLM_PROXY is set."""
    proxy = os.getenv("LLM_PROXY", "").strip()
    if not proxy:
        return None
    from httpx_socks import SyncProxyTransport
    transport = SyncProxyTransport.from_url(proxy)
    return httpx.Client(transport=transport, timeout=60.0)


class LLMService:
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
    ):
        self.client = OpenAI(
            api_key=api_key or LLM_API_KEY,
            base_url=base_url or LLM_BASE_URL or OPENROUTER_BASE_URL,
            http_client=_build_http_client(),
        )
        self.model = model or LLM_MODEL

    def extract_structured(self, messages: List[Dict[str, str]], schema: Type[BaseModel]) -> BaseModel:
        # Append JSON instruction to system prompt for reliable extraction.
        # json_object mode works across all providers (OpenRouter, OpenAI, etc.)
        # unlike json_schema which Gemini doesn't support properly.
        patched = list(messages)
        if patched and patched[0]["role"] == "system":
            patched[0] = {
                "role": "system",
                "content": patched[0]["content"] + "\n\nОтветь строго в формате JSON.",
            }

        started = time.monotonic_ns()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=patched,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        latency_ms = (time.monotonic_ns() - started) // 1_000_000
        usage = getattr(response, "usage", None)
        record_llm_call(
            kind="extract",
            model=self.model,
            latency_ms=latency_ms,
            tokens_in=getattr(usage, "prompt_tokens", 0) if usage else 0,
            tokens_out=getattr(usage, "completion_tokens", 0) if usage else 0,
        )

        raw = response.choices[0].message.content
        # Gemini sometimes returns a JSON array instead of object with long context
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            parsed = parsed[0]
        return schema.model_validate(parsed)

    def stream_text(self, messages: List[Dict[str, Any]]) -> Generator[str, None, None]:
        started = time.monotonic_ns()
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            temperature=0.3,
            top_p=0.3,
            max_tokens=200,
        )
        chars_out = 0
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                chars_out += len(delta.content)
                yield delta.content
        latency_ms = (time.monotonic_ns() - started) // 1_000_000
        record_llm_call(
            kind="stream",
            model=self.model,
            latency_ms=latency_ms,
            tokens_out=chars_out // 4,  # rough estimate when no usage in stream
        )

    def one_shot_text(self, messages: List[Dict[str, Any]]) -> str:
        started = time.monotonic_ns()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
            top_p=0.3,
            max_tokens=200,
        )
        latency_ms = (time.monotonic_ns() - started) // 1_000_000
        usage = getattr(response, "usage", None)
        record_llm_call(
            kind="one_shot",
            model=self.model,
            latency_ms=latency_ms,
            tokens_in=getattr(usage, "prompt_tokens", 0) if usage else 0,
            tokens_out=getattr(usage, "completion_tokens", 0) if usage else 0,
        )
        return response.choices[0].message.content or ""
