import re
from typing import Generator, Iterable

from ..schemas import AgentChunk

_GREETING_RE = re.compile(
    r"^(Здравствуйте|Добрый день|Добрый вечер|Привет)[!.,]?\s*",
    re.IGNORECASE,
)


def single_chunk_stream(text: str) -> Generator[AgentChunk, None, None]:
    yield AgentChunk(text=text, type="template")


def prepend_chunk_and_stream(
    template_text: str,
    llm_stream: Iterable[str],
    strip_greeting: bool = False,
):
    yield AgentChunk(text=template_text, type="template")
    if strip_greeting:
        # Buffer LLM tokens, strip greeting, then yield
        buf = ""
        stripped = False
        for chunk in llm_stream:
            if not chunk:
                continue
            if not stripped:
                buf += chunk
                if len(buf) > 15 or "\n" in buf:
                    cleaned = _GREETING_RE.sub("", buf, count=1)
                    stripped = True
                    if cleaned:
                        yield AgentChunk(text=cleaned, type="llm")
            else:
                yield AgentChunk(text=chunk, type="llm")
        if not stripped and buf:
            cleaned = _GREETING_RE.sub("", buf, count=1)
            if cleaned:
                yield AgentChunk(text=cleaned, type="llm")
    else:
        for chunk in llm_stream:
            if chunk:
                yield AgentChunk(text=chunk, type="llm")