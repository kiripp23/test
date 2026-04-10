from __future__ import annotations
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from .schemas import ExtractedMessage


class MessageItem(TypedDict):
    role: str
    content: str


class AgentState(TypedDict, total=False):
    user_message: str
    session_id: str
    messages: List[Any]  # LangGraph Studio compat
    history: List[MessageItem]

    extracted: ExtractedMessage
    prev_intent: str
    message_category: str  # clinic_related | off_topic | emotional | greeting | gratitude

    matched_doctor: Optional[Dict[str, Any]]
    faq_match: Optional[Dict[str, Any]]

    available_slots: List[str]
    selected_slot: Optional[str]

    pending_action: Optional[str]
    pending_doctor_name: Optional[str]

    patient_name: Optional[str]
    birth_date: Optional[str]

    response_mode: str
    template_response: Optional[str]

    need_llm_stream: bool
    llm_prompt: Optional[str]

    final_route: Optional[str]

    rejected_slots: List[str]  # slots patient already rejected
    tried_doctors: List[str]   # doctors already suggested to patient

    call_signal: Optional[str]  # "end_call" | "transfer_operator" | None
    booking_completed: bool  # True after successful booking — affects CTA in prompts
    _append_after_llm: Optional[str]  # text to append after LLM stream (e.g. re-ask prompts)
    _booking_cta: Optional[str]  # CTA to append when answering side questions during booking
    _disease_confirmed_for_pending_doctor: bool  # disease matches current doctor during data collection
    _confirmed_diseases: Optional[List[str]]  # diseases confirmed by router for current doctor
    fallback_messages: Optional[list]
    full_system_prompt: Optional[str]