"""
Fixtures and helpers for testing the Psy Family bot.

Two test levels:
1. Deterministic (unit) — mock LLM extraction, test routing + node logic
2. Integration — real LLM, full dialog scenarios (requires LLM_API_KEY)
"""
import pytest
from ai_agent.schemas import ExtractedMessage
from ai_agent.services.doctors_service import DoctorsService
from ai_agent.services.medesk_service import MedeskService
from ai_agent.services.faq_service import FAQService


@pytest.fixture
def doctors_service():
    return DoctorsService()


@pytest.fixture
def medesk_service():
    return MedeskService()


@pytest.fixture
def faq_service():
    return FAQService()


def make_state(
    user_message: str,
    extracted: ExtractedMessage,
    history=None,
    pending_action=None,
    pending_doctor_name=None,
    patient_name=None,
    birth_date=None,
    selected_slot=None,
    rejected_slots=None,
    tried_doctors=None,
    prev_intent=None,
    call_signal=None,
    matched_doctor=None,
    message_category=None,
    booking_completed=False,
):
    """Build an AgentState dict for testing individual nodes."""
    return {
        "user_message": user_message,
        "history": history or [],
        "extracted": extracted,
        "message_category": message_category or extracted.message_category,
        "prev_intent": prev_intent,
        "pending_action": pending_action,
        "pending_doctor_name": pending_doctor_name,
        "patient_name": patient_name,
        "birth_date": birth_date,
        "selected_slot": selected_slot,
        "rejected_slots": rejected_slots or [],
        "tried_doctors": tried_doctors or [],
        "matched_doctor": matched_doctor,
        "call_signal": call_signal,
        "template_response": None,
        "need_llm_stream": False,
        "llm_prompt": None,
        "final_route": None,
        "booking_completed": booking_completed,
    }


def extract(intent, **kwargs):
    """Shortcut to build ExtractedMessage."""
    return ExtractedMessage(patient_intent=intent, **kwargs)


def collect_response(state):
    """Extract the bot response text from state after node execution."""
    return state.get("template_response", "")
