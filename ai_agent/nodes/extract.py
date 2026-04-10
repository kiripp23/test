import logging

from ..state import AgentState
from ..schemas import ExtractedMessage
from ..services.llm_service import LLMService
from ..utils.prompts import EXTRACTION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def extract_node(state: AgentState, llm: LLMService) -> AgentState:
    # Studio sends {"messages": [...]} instead of {"user_message": "..."}
    if not state.get("user_message"):
        logger.warning("No user_message in state, keys: %s", list(state.keys()))
        # Try to extract from various Studio input formats
        for key in ("messages", "input", "content"):
            val = state.get(key)
            if val:
                logger.warning("Found key=%s, type=%s, val=%s", key, type(val), val)
                if isinstance(val, list) and val:
                    last = val[-1]
                    content = last.content if hasattr(last, "content") else (last.get("content", "") if isinstance(last, dict) else str(last))
                    state["user_message"] = content
                    break
                elif isinstance(val, str):
                    state["user_message"] = val
                    break
        if not state.get("user_message"):
            state["user_message"] = ""

    messages = [{"role": "system", "content": EXTRACTION_SYSTEM_PROMPT}]
    messages += state.get("history", [])[-10:]
    messages.append({"role": "user", "content": state["user_message"]})

    try:
        extracted = llm.extract_structured(
            messages=messages,
            schema=ExtractedMessage,
        )
    except Exception:
        logger.exception("LLM extraction failed for: %s", state["user_message"])
        extracted = ExtractedMessage(
            patient_intent="arbitrary_message",
            message_category="clinic_related",
        )

    state["extracted"] = extracted
    state["message_category"] = extracted.message_category
    print(f"EXTRACT: msg='{state['user_message'][:50]}' intent={extracted.patient_intent} name={extracted.patient_name} doctor={extracted.doctor_name} info={extracted.doctor_info}", flush=True)
    if extracted.patient_name:
        state["patient_name"] = extracted.patient_name
    if extracted.birth_date:
        state["birth_date"] = extracted.birth_date

    return state
