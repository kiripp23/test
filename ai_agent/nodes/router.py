import logging
import re

from ..state import AgentState
from ..utils.format import format_slot, decline_name

logger = logging.getLogger(__name__)

# Clinic/location questions — forced route to FAQ at any pending state.
_CLINIC_QUESTION_KEYWORDS = (
    "называется", "как называет", "клиника", "адрес", "телефон",
    "как доехать", "как добраться",
)
_CLINIC_WHERE_RE = re.compile(r"\bгде\b.*\b(наход|вы\b)", re.IGNORECASE)

# Question about current doctor's quality/experience/education.
# Triggers when pending_doctor_name is set (handled in the caller).
_DOCTOR_QUALITY_RE = re.compile(
    r"(хорош|опытн|нормальн|подход|компетентн|квалифицир|профессионал|"
    r"знающ|толков|стаж|опыт\b|образован|специализаци|квалификац|категори|сертиф)",
    re.IGNORECASE,
)


def _is_clinic_question(user_message: str) -> bool:
    if not user_message:
        return False
    lower = user_message.lower()
    if any(kw in lower for kw in _CLINIC_QUESTION_KEYWORDS):
        return True
    return bool(_CLINIC_WHERE_RE.search(user_message))


def _is_doctor_quality_question(user_message: str) -> bool:
    """Detect questions/requests about the current doctor's quality/experience.
    Doesn't require "?" — "Расскажите про его стаж" is also a request.
    Safe because caller checks pending_doctor_name.
    """
    if not user_message:
        return False
    return bool(_DOCTOR_QUALITY_RE.search(user_message))


def _get_booking_cta(state: AgentState) -> str | None:
    """Build a CTA reminder based on what data is still missing in the booking flow."""
    pending = state.get("pending_action")
    if not pending:
        return None
    if pending == "ask_patient_name":
        return "\n\nНазовите, пожалуйста, ваше имя и фамилию для записи."
    if pending == "ask_birth_date":
        return "\n\nИ подскажите, пожалуйста, дату рождения для оформления записи."
    if pending == "confirm_slot":
        slot = state.get("selected_slot")
        if slot:
            return f"\n\nТак что, записываем вас на {format_slot(slot)}?"
        return "\n\nТак что, записать вас на приём?"
    if pending == "confirm_alt_doctor":
        doctor_name = state.get("pending_doctor_name")
        if doctor_name:
            return f"\n\nТак что, записать вас к {decline_name(doctor_name, 'dative')}?"
        return "\n\nТак что, записать вас на приём?"
    return None


def _resolve_pending_route(state: AgentState, intent: str, extracted) -> str | None:
    """Determine route based on pending_action state.

    Returns route name if pending state takes priority, None otherwise.
    Handles context-switching: if patient asks a question while we're
    collecting data, route to the appropriate handler first.
    """
    pending = state.get("pending_action")
    if not pending:
        return None

    question_intents = (
        "question_about_doctor",
        "question_about_clinic",
        "wants_operator",
        "end_conversation",
    )

    # Collecting name or DOB — if the expected data was extracted, always go to appointment
    # (even if LLM misclassified intent as a question due to context)
    if pending == "ask_patient_name" and extracted.patient_name:
        return "appointment"
    if pending == "ask_birth_date" and extracted.birth_date:
        return "appointment"

    # Collecting name or DOB — let genuine questions through, otherwise stay in appointment
    if pending in ("ask_patient_name", "ask_birth_date"):
        if intent == "question_about_doctor":
            # Check if question is directed at the BOT, not the doctor
            # e.g. "А вас как зовут?" / "Как тебя зовут?" — should stay in appointment fallback
            import re
            msg_words = set(re.sub(r'[^\w\s]', '', state.get("user_message", "").lower()).split())
            bot_pronouns = {"вас", "вам", "тебя", "тебе", "ты", "вы"}
            name_words = {"зовут", "зовёт", "имя"}
            bot_directed = bool(msg_words & bot_pronouns and msg_words & name_words)
            if not bot_directed:
                return "doctor_info"
            # Bot-directed question — let appointment fallback handle it
            return "appointment"
        if intent == "question_about_clinic":
            return "faq"
        # Patient mentions a disease or wants to book while we're collecting data.
        # If current doctor treats this disease — confirm and stay in booking flow.
        if intent in ("want_procedure", "make_appoint") and extracted.diseases:
            pending_doctor = state.get("pending_doctor_name")
            if pending_doctor:
                from ..services.doctors_service import DoctorsService
                # Flag for appointment_node: disease confirmed for current doctor
                state["_disease_confirmed_for_pending_doctor"] = True
                state["_confirmed_diseases"] = extracted.diseases
        # wants_operator / end_conversation are handled before this function
        return "appointment"

    if pending == "confirm_alt_doctor":
        if intent == "doctor_not_ok":
            return "doctor_info"
        if intent == "question_about_doctor":
            return "doctor_info"
        if intent == "question_about_clinic":
            return "faq"
        return "appointment"

    if pending == "confirm_slot":
        secondary = getattr(extracted, "secondary_intent", None)

        # Short "да"/"нет" during confirm_slot is about the SLOT, not the doctor.
        # LLM often misclassifies as doctor_not_ok / doctor_ok after a detour.
        msg_clean = state.get("user_message", "").strip().strip(".!?,").lower()
        if msg_clean in ("нет", "не", "неа", "не подходит", "не хочу", "неудобно"):
            state["extracted"].patient_intent = "slot_not_suitable"
            return "appointment"
        if msg_clean in ("да", "ок", "хорошо", "подходит", "согласен", "согласна", "давайте"):
            state["extracted"].patient_intent = "chosen_slot"
            return "appointment"

        # Multi-intent: user confirms slot AND asks a question
        # e.g. "Да, подходит. Расскажите про него подробнее."
        if intent == "chosen_slot" and secondary == "question_about_doctor":
            state["extracted"].patient_intent = "question_about_doctor"
            state["pending_action"] = "ask_patient_name"
            return "doctor_info"
        if intent == "question_about_doctor" and secondary == "chosen_slot":
            state["pending_action"] = "ask_patient_name"
            return "doctor_info"
        if intent == "chosen_slot" and secondary == "question_about_clinic":
            state["pending_action"] = "ask_patient_name"
            return "faq"
        if intent == "question_about_clinic" and secondary == "chosen_slot":
            state["pending_action"] = "ask_patient_name"
            return "faq"

        if intent in ("question_about_doctor",):
            return "doctor_info"
        if intent == "question_about_clinic":
            return "faq"

        # Keyword fallback: LLM may misclassify clinic questions during booking
        import re as _re
        _CLINIC_KEYWORDS = (
            "называется", "клиника", "адрес", "телефон",
            "как доехать", "как добраться", "как называет",
        )
        # Broader regex for "где вы находитесь", "где находитесь", "где находится"
        _WHERE_RE = _re.compile(r"\bгде\b.*\bнаход", _re.IGNORECASE)
        msg_lower = state.get("user_message", "").lower()
        matches_keyword = any(kw in msg_lower for kw in _CLINIC_KEYWORDS)
        matches_where = bool(_WHERE_RE.search(state.get("user_message", "")))
        if (matches_keyword or matches_where) and not extracted.patient_slots:
            return "faq"

        # Slot-related intents — stay in appointment
        if intent in (
            "make_appoint", "asks_about_slot", "chosen_slot",
            "slot_not_suitable", "arbitrary_message", "doctor_ok",
        ):
            # Implicit agreement: no new slots requested
            if intent in ("make_appoint", "doctor_ok", "arbitrary_message") and not extracted.patient_slots:
                state["extracted"].patient_intent = "chosen_slot"
            return "appointment"

    return None


def _resolve_intent_route(state: AgentState, intent: str, category: str) -> str:
    """Pure intent-based routing when no pending_action is active."""
    prev_intent = state.get("prev_intent")

    if intent in ("make_appoint", "asks_about_slot", "chosen_slot", "slot_not_suitable"):
        return "appointment"

    # After slot acceptance, treat arbitrary follow-up as continued acceptance
    if prev_intent == "chosen_slot" and intent == "arbitrary_message":
        state["extracted"].patient_intent = "chosen_slot"
        return "appointment"

    if intent in ("want_procedure", "question_about_doctor", "doctor_not_ok"):
        return "doctor_info"

    if intent == "question_about_clinic":
        return "faq"

    # Arbitrary message with doctor name extracted — likely a doctor inquiry
    # e.g. user said "Иванов Равиль Рахимович" after being asked to clarify
    extracted = state["extracted"]
    if intent == "arbitrary_message" and extracted.doctor_name:
        state["extracted"].patient_intent = "question_about_doctor"
        return "doctor_info"

    # Arbitrary message with doctor context — offer appointment
    if (
        intent == "arbitrary_message"
        and prev_intent in ("want_procedure", "question_about_doctor")
        and state.get("pending_doctor_name")
    ):
        gn = decline_name(state["pending_doctor_name"], "dative")
        state["template_response"] = f"Хотите записаться на приём к {gn}?"
        state["need_llm_stream"] = False
        return "fallback"

    # For clinic-related arbitrary messages, try FAQ first
    if intent == "arbitrary_message" and category == "clinic_related":
        return "faq"

    return "fallback"


def _handle_signals(state: AgentState, intent: str) -> bool:
    """Handle special intents (operator transfer, end call).

    Returns True if a signal was handled (route is set), False otherwise.
    """
    if intent == "wants_operator":
        state["template_response"] = "Переключаю вас на оператора, оставайтесь на линии."
        state["need_llm_stream"] = False
        state["call_signal"] = "transfer_operator"
        state["final_route"] = "fallback"
        return True

    if intent == "end_conversation":
        state["template_response"] = "До свидания, будьте здоровы! Ждём вас на приёме."
        state["need_llm_stream"] = False
        state["call_signal"] = "end_call"
        state["final_route"] = "fallback"
        return True

    return False


def router_node(state: AgentState) -> AgentState:
    extracted = state["extracted"]
    intent = extracted.patient_intent
    category = state.get("message_category", extracted.message_category)

    logger.debug(
        "router: intent=%s, category=%s, prev_intent=%s, doctor_name=%s",
        intent, category, state.get("prev_intent"), state.get("pending_doctor_name"),
    )

    # Step 1: Handle signals (operator, goodbye) — highest priority
    if _handle_signals(state, intent):
        return state

    # Step 1.5: Force clinic questions to FAQ regardless of pending state.
    # LLM frequently misclassifies "Где вы находитесь?" as slot_not_suitable
    # during booking flow, which triggers unwanted alt-doctor switching.
    if _is_clinic_question(state.get("user_message", "")) and not extracted.patient_slots:
        if state.get("pending_action"):
            state["_booking_cta"] = _get_booking_cta(state)
        # Also fix intent so downstream compound-handling doesn't re-misroute.
        state["extracted"].patient_intent = "question_about_clinic"
        state["final_route"] = "faq"
        return state

    # Step 1.6: Force doctor-quality questions to doctor_info during booking.
    # LLM often classifies "А он опытный?" as slot_not_suitable / doctor_not_ok.
    # Keep the current doctor and respond with info.
    if (
        _is_doctor_quality_question(state.get("user_message", ""))
        and state.get("pending_doctor_name")
        and not extracted.patient_slots
    ):
        if state.get("pending_action"):
            state["_booking_cta"] = _get_booking_cta(state)
        state["extracted"].patient_intent = "question_about_doctor"
        state["final_route"] = "doctor_info"
        return state

    # Step 2: Check pending state (data collection in progress)
    pending_route = _resolve_pending_route(state, intent, extracted)
    if pending_route:
        # Re-read intent — _resolve_pending_route may have updated it
        current_intent = extracted.patient_intent
        # When routing away from appointment to answer a side question,
        # attach a booking CTA so the bot reminds what data it still needs
        question_intents = ("question_about_doctor", "question_about_clinic")
        if pending_route != "appointment" and current_intent in question_intents and state.get("pending_action"):
            state["_booking_cta"] = _get_booking_cta(state)
        state["final_route"] = pending_route
        return state

    # Step 3: Pure intent + category routing
    state["final_route"] = _resolve_intent_route(state, intent, category)
    return state
