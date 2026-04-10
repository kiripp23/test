import re

from ..state import AgentState
from ..utils.prompts import CATEGORY_PROMPTS, FALLBACK_SYSTEM_PROMPT

_BOT_NAME_RE = re.compile(
    r"(как\s+(тебя|вас|тeбя)\s+зовут|как\s+твоё\s+имя|кто\s+ты|ты\s+кто|"
    r"твоё\s+имя|как\s+называ[еть]ш|"
    r"расскажи\s+(про|о)\s+себе?|что\s+ты\s+(такое|умеешь))",
    re.IGNORECASE,
)

_BOOKING_DONE_SUFFIX = (
    "\n\nВАЖНО: пациент уже записан на приём. "
    "НЕ предлагай записаться повторно. Вместо «Хотите записаться?» "
    "используй «Могу ещё чем-то помочь?» или «Есть ещё вопросы?»."
)


def _enrich_prompt(system_prompt: str, state: AgentState) -> str:
    """Add booking context to system prompt if booking was completed."""
    if state.get("booking_completed"):
        return system_prompt + _BOOKING_DONE_SUFFIX
    return system_prompt


def fallback_node(state: AgentState) -> AgentState:
    # If router already set a response (e.g. end_conversation, wants_operator), pass through
    if state.get("template_response") is not None and not state.get("need_llm_stream"):
        return state

    category = state.get("message_category", "clinic_related")
    system_prompt = CATEGORY_PROMPTS.get(category, FALLBACK_SYSTEM_PROMPT)
    has_history = bool(state.get("history"))

    # Bot-identity question ("как тебя зовут?", "кто ты?") — deterministic answer
    if _BOT_NAME_RE.search(state.get("user_message", "")):
        suffix = "Могу ещё чем-то помочь?" if state.get("booking_completed") else "Чем могу помочь?"
        state["template_response"] = f"Меня зовут Алиса, я оператор клиники Psy Family. {suffix}"
        state["need_llm_stream"] = False
        return state

    # Greeting template — only for the very first message in the session
    if category == "greeting" and not has_history:
        state["template_response"] = "Здравствуйте! Клиника Psy Family, меня зовут Алиса. Чем могу помочь?"
        state["need_llm_stream"] = False
        return state

    if category == "gratitude":
        state["template_response"] = "Пожалуйста! Могу ещё чем-то помочь?"
        state["need_llm_stream"] = False
        return state

    if category == "off_topic":
        state["template_response"] = (
            "Я могу помочь вам с записью к врачу и информацией о клинике. "
            "Хотите записаться на приём или узнать о наших специалистах?"
        )
        state["need_llm_stream"] = False
        return state

    # emotional and clinic_related — use LLM with full knowledge prompt
    base_prompt = state.get("full_system_prompt") or system_prompt
    enriched = _enrich_prompt(base_prompt, state)
    # If mid-conversation, tell LLM not to greet again
    if state.get("history"):
        enriched += (
            "\n\nВАЖНО: ты уже В СЕРЕДИНЕ разговора с пациентом. "
            "НЕ здоровайся заново ('Здравствуйте', 'Добрый день'). "
            "Ответь кратко на вопрос и всё."
        )
    history = state.get("history", [])
    conversation = [{"role": "system", "content": enriched}]
    for msg in history:
        conversation.append({"role": msg["role"], "content": msg["content"]})
    conversation.append({"role": "user", "content": state["user_message"]})

    state["template_response"] = None
    state["need_llm_stream"] = True
    state["llm_prompt"] = None
    state["fallback_messages"] = conversation
    return state
