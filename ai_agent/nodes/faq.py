import re

from ..state import AgentState
from ..services.faq_service import FAQService
from ..utils.prompts import FAQ_ANSWER_PROMPT

# Questions about info NOT in our data — must NOT hallucinate, offer admin transfer
_UNKNOWN_INFO_PATTERNS = [
    r"метро|станци[яию]",
    r"парков[кои]|машин[уыа]|автомобил",
    r"рядом|около|близко|недалеко|ориентир",
    r"добраться|доехать|дойти|маршрут|навигат",
    r"этаж|подъезд|вход|домофон|код",
    r"wi-?fi|вай-?фай|интернет",
]
_UNKNOWN_RE = re.compile("|".join(_UNKNOWN_INFO_PATTERNS), re.IGNORECASE)

_ADMIN_TRANSFER_RESPONSE = (
    "Извините, я не располагаю такой информацией, "
    "но могу переключить вас на старшего администратора для уточнения. "
    "Переключить или записать вас на приём?"
)

_BOOKING_DONE_SUFFIX = (
    "\n\nВАЖНО: пациент уже записан на приём. "
    "НЕ предлагай записаться повторно. Вместо «Хотите записаться?» "
    "используй «Могу ещё чем-то помочь?» или «Есть ещё вопросы?»."
)


_MID_CONVERSATION_HINT = (
    "\n\nВАЖНО: ты уже В СЕРЕДИНЕ разговора с пациентом. "
    "НЕ здоровайся заново ('Здравствуйте', 'Добрый день'). "
    "Ответь кратко на вопрос и всё."
)


def faq_node(
    state: AgentState,
    faq_service: FAQService,
) -> AgentState:
    user_question = state["user_message"]
    match = faq_service.match(user_question)
    booking_suffix = _BOOKING_DONE_SUFFIX if state.get("booking_completed") else ""
    # If we're mid-conversation, tell LLM not to greet again
    mid_conv = _MID_CONVERSATION_HINT if state.get("history") else ""

    # Direct address question → template answer (no LLM embellishment)
    if re.search(r"где\s+(вы\s+)?наход|какой.*адрес|ваш адрес", user_question, re.IGNORECASE):
        state["template_response"] = (
            "Мы находимся по адресу: Москва, ул. Шверника, 17к3. "
            "Хотите записаться на приём?"
        )
        state["need_llm_stream"] = False
        return state

    # Check if question is about info NOT in our data — prevent hallucination
    # Must run BEFORE FAQ match to prevent LLM from embellishing FAQ answers
    if _UNKNOWN_RE.search(user_question):
        state["template_response"] = _ADMIN_TRANSFER_RESPONSE
        state["need_llm_stream"] = False
        return state

    if match:
        state["faq_match"] = match.model_dump()
        state["template_response"] = ""
        state["llm_prompt"] = FAQ_ANSWER_PROMPT.format(
            user_question=user_question,
            faq_question=match.question,
            faq_answer=match.answer,
        ) + booking_suffix + mid_conv
        state["need_llm_stream"] = True
        return state

    # No stem match — LLM with full knowledge prompt (already includes FAQ)
    base_prompt = state.get("full_system_prompt", "")
    state["template_response"] = None
    state["need_llm_stream"] = True
    state["llm_prompt"] = None

    faq_system = base_prompt + booking_suffix + mid_conv
    history = state.get("history", [])
    conversation = [{"role": "system", "content": faq_system}]
    for msg in history:
        conversation.append({"role": msg["role"], "content": msg["content"]})
    conversation.append({"role": "user", "content": user_question})
    state["fallback_messages"] = conversation

    return state
