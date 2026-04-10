import random
import re

from ..state import AgentState
from ..services.doctors_service import DoctorsService
from ..services.disease_matcher import DiseaseMatcher
from ..utils.prompts import DOCTOR_INFO_SUMMARY_PROMPT
from ..utils.format import decline_name, decline_phrase_genitive, format_slot

# Question about current doctor's quality, not a rejection.
# e.g. "А он хороший доктор?", "Он опытный?", "Нормальный врач?"
_DOCTOR_QUESTION_RE = re.compile(
    r"(хорош|опытн|нормальн|подход|компетентн|квалифицир|профессионал|знающ|толков)",
    re.IGNORECASE,
)

_BOOKING_CTA_VARIANTS = [
    "Записать вас на приём?",
    "Мне забронировать для вас окошко?",
    "Желаете закрепить за собой дату и время?",
    "Когда вам удобно подойти?",
]


def _booking_cta() -> str:
    """Pick a random booking CTA for variety."""
    return random.choice(_BOOKING_CTA_VARIANTS)


def _cta(state: AgentState) -> str:
    """Return a call-to-action suffix based on current pending_action."""
    # If router already set _booking_cta, skip node-level CTA to avoid duplication
    if state.get("_booking_cta"):
        return ""
    pending = state.get("pending_action")
    if pending == "ask_patient_name":
        return " Как вас зовут?"
    if pending == "ask_birth_date":
        return " Подскажите, пожалуйста, дату рождения."
    if pending == "confirm_slot":
        slot = state.get("selected_slot")
        if slot:
            return f" Вам подходит время {format_slot(slot)}?"
        return f" {_booking_cta()}"
    if pending == "confirm_alt_doctor":
        return f" {_booking_cta()}"
    return f" {_booking_cta()}"


def doctor_info_node(
    state: AgentState,
    doctors_service: DoctorsService,
    disease_matcher: DiseaseMatcher | None = None,
) -> AgentState:
    extracted = state["extracted"]
    intent = extracted.patient_intent

    # Check: booking only for self or close relatives
    _NON_RELATIVE_KEYWORDS = ("сосед", "друг", "подруг", "коллег", "знаком", "сослужив", "начальник")
    msg_lower = state.get("user_message", "").lower()
    if any(kw in msg_lower for kw in _NON_RELATIVE_KEYWORDS):
        state["template_response"] = (
            "К сожалению, записать на приём можно только себя или ближайшего родственника. "
            "Но вы можете передать ему наш номер +7 (499) 302-17-10 — "
            "он позвонит, и мы его запишем. А вас самого что-нибудь беспокоит?"
        )
        state["need_llm_stream"] = False
        return state

    if intent == "want_procedure":
        doctor = None
        template_parts = []
        pending_doctor = state.get("pending_doctor_name")

        # If we're already discussing a specific doctor, check them first
        if pending_doctor and extracted.diseases:
            if doctors_service.doctor_treats_disease(pending_doctor, extracted.diseases):
                doctor = doctors_service.find_by_name(pending_doctor)
                if doctor:
                    disease_gen = decline_phrase_genitive(extracted.diseases[0])
                    gn = decline_name(doctor.name, "genitive")
                    template_parts.append(
                        f"Да, {doctor.name} занимается лечением {disease_gen}."
                    )
        if pending_doctor and extracted.procedure and not doctor:
            doc = doctors_service.find_by_name(pending_doctor)
            if doc and extracted.procedure.lower() in [p.lower() for p in doc.procedures]:
                doctor = doc
                gn = decline_name(doctor.name, "genitive")
                template_parts.append(f"{extracted.procedure} можно пройти у {gn}.")

        # ── Disease matcher: match disease → ranked doctors ──
        if not doctor and disease_matcher and (extracted.diseases or extracted.procedure):
            query_text = " ".join(extracted.diseases or [])
            if extracted.procedure:
                query_text = f"{query_text} {extracted.procedure}".strip()
            dm_results = disease_matcher.match(query_text)
            tried = set(state.get("tried_doctors", []))
            for dm in dm_results:
                for doc_name in dm.doctors:
                    if doc_name in tried:
                        continue
                    doc = doctors_service.find_by_name(doc_name)
                    if doc:
                        doctor = doc
                        disease_gen = decline_phrase_genitive(dm.canonical)
                        template_parts.append(f"{doctor.name} занимается лечением {disease_gen}.")
                        break
                if doctor:
                    break

        # ── Fallback: old DoctorsService search ──
        if not doctor and extracted.procedure:
            doctor = doctors_service.find_by_procedure(extracted.procedure)
            if doctor:
                gn = decline_name(doctor.name, "genitive")
                template_parts.append(f"{extracted.procedure} можно пройти у {gn}.")
        if not doctor and extracted.diseases:
            doctor = doctors_service.find_by_disease(extracted.diseases)
            if doctor:
                disease_gen = decline_phrase_genitive(extracted.diseases[0])
                template_parts.append(f"{doctor.name} занимается лечением {disease_gen}.")

        if not doctor:
            query = extracted.procedure or (extracted.diseases[0] if extracted.diseases else None)
            if not query:
                for word in state["user_message"].split():
                    if len(word) >= 5:
                        query = word
                        break
            if query:
                doctor = doctors_service.find_by_specialization(query)
                if doctor:
                    template_parts.append(f"Рекомендуем обратиться к {decline_name(doctor.name, 'dative')}.")

        if not doctor:
            # If we have a pending doctor but couldn't match disease/procedure
            if pending_doctor:
                doc = doctors_service.find_by_name(pending_doctor)
                if doc:
                    state["template_response"] = (
                        f"К сожалению, в карточке {decline_name(doc.name, 'genitive')} "
                        f"нет информации по этому направлению. "
                        f"Уточните, пожалуйста, у администратора."
                    )
                    state["need_llm_stream"] = False
                    return state
            if extracted.diseases or extracted.procedure:
                state["template_response"] = (
                    "Наша клиника специализируется на психическом здоровье. "
                    "По вашему вопросу рекомендуем обратиться в профильную клинику. "
                    "Если вас интересует помощь психиатра или психотерапевта — хотите записаться?"
                )
            else:
                state["template_response"] = "Подскажите, что вас беспокоит? Так я смогу подобрать подходящего специалиста."
            state["need_llm_stream"] = False
            return state

        state["matched_doctor"] = doctor.model_dump()
        state["pending_doctor_name"] = doctor.name
        state["template_response"] = " ".join(template_parts) + " "
        state["llm_prompt"] = DOCTOR_INFO_SUMMARY_PROMPT.format(info=doctor.info, cta=_booking_cta())
        state["need_llm_stream"] = True
        return state

    if intent == "question_about_doctor":
        # Check if user asks about a specific role/quality (e.g. "главный врач", "лучший")
        # rather than the currently discussed doctor
        _ROLE_KEYWORDS = ("главный", "лучший", "ведущий", "старший", "заведующий")
        msg_lower = state["user_message"].lower()
        role_match = None
        if any(kw in msg_lower for kw in _ROLE_KEYWORDS):
            for kw in _ROLE_KEYWORDS:
                if kw in msg_lower:
                    role_match = doctors_service.find_by_specialization(kw)
                    if role_match:
                        break

        if role_match:
            doctor_name = role_match.name
        else:
            doctor_name = state.get("pending_doctor_name") or extracted.doctor_name

        if not doctor_name:
            state["template_response"] = (
                "В нашей клинике работают психиатры, психотерапевты и клинические психологи. "
                "Мы помогаем с тревожными расстройствами, депрессией, паническими атаками, "
                "нарушениями сна и другими вопросами психического здоровья. "
                "Подскажите, что вас беспокоит? Так я подберу подходящего специалиста."
            )
            state["need_llm_stream"] = False
            return state

        doctor = doctors_service.find_by_name(doctor_name)
        if not doctor:
            if len(doctor_name.split()) >= 2:
                state["template_response"] = (
                    "К сожалению, в нашей клинике нет врача с таким именем. "
                    "Хотите записаться к другому специалисту?"
                )
            else:
                state["template_response"] = (
                    f"Не удалось найти врача по имени {doctor_name}. "
                    "Подскажите, пожалуйста, полное ФИО."
                )
            state["need_llm_stream"] = False
            return state

        state["matched_doctor"] = doctor.model_dump()
        state["pending_doctor_name"] = doctor.name
        gn = decline_name(doctor.name, "genitive")

        cta = _cta(state)
        if extracted.doctor_info == "visiting_hours":
            hours = doctor.visiting_hours or "уточняется у администратора"
            state["template_response"] = f"Часы приёма {gn}: {hours}.{cta}"
            state["need_llm_stream"] = False
        elif extracted.doctor_info == "experience":
            state["template_response"] = f"У {gn} стаж {doctor.experience}.{cta}"
            state["need_llm_stream"] = False
        elif extracted.doctor_info == "cost":
            # If already in booking flow, don't offer "записать или перевести" — use booking CTA
            in_booking = state.get("pending_action") in (
                "ask_patient_name", "ask_birth_date", "confirm_slot", "confirm_alt_doctor",
            )
            if in_booking:
                transfer_hint = (
                    "Чтобы узнать более подробную информацию, я могу перевести вас "
                    "к старшему администратору клиники."
                )
            else:
                transfer_hint = (
                    "Чтобы узнать более подробную информацию, я могу перевести вас "
                    "к старшему администратору клиники. "
                    "Записать вас на приём или перевести звонок?"
                )
            cost_text = doctor.cost_summary()
            if cost_text:
                state["template_response"] = f"Стоимость приёма у {gn}: {cost_text}. {transfer_hint}"
            else:
                state["template_response"] = (
                    f"Стоимость приёма уточните у администратора. {transfer_hint}"
                )
            state["need_llm_stream"] = False
        else:
            # specialization, None, or any other — natural LLM response
            state["template_response"] = ""
            cta_text = _cta(state).strip()
            state["llm_prompt"] = DOCTOR_INFO_SUMMARY_PROMPT.format(info=f"{doctor.name}: {doctor.info}", cta=cta_text)
            state["need_llm_stream"] = True
        return state

    if intent == "doctor_not_ok":
        # Guard: "А он хороший доктор?" is a QUESTION about the current doctor,
        # not a rejection. Don't swap doctor — re-route to question_about_doctor.
        msg = state.get("user_message", "")
        if "?" in msg and _DOCTOR_QUESTION_RE.search(msg):
            state["extracted"].patient_intent = "question_about_doctor"
            intent = "question_about_doctor"
            # Fall through — the question_about_doctor branch is above; we need
            # to handle inline since Python doesn't support goto. Duplicate the
            # minimal path: resolve current doctor and produce info response.
            doctor_name = state.get("pending_doctor_name") or extracted.doctor_name
            if doctor_name:
                doctor = doctors_service.find_by_name(doctor_name)
                if doctor:
                    state["matched_doctor"] = doctor.model_dump()
                    state["pending_doctor_name"] = doctor.name
                    state["template_response"] = ""
                    state["llm_prompt"] = DOCTOR_INFO_SUMMARY_PROMPT.format(
                        info=f"{doctor.name}: {doctor.info}",
                        cta=_cta(state).strip(),
                    )
                    state["need_llm_stream"] = True
                    return state

        # Patient rejected suggested doctor — find alternative with same profile
        rejected_name = state.get("pending_doctor_name")
        if rejected_name:
            tried = list(state.get("tried_doctors", []))
            if rejected_name not in tried:
                tried.append(rejected_name)
            alt = doctors_service.find_similar_doctor(rejected_name, exclude=tried)
            if alt:
                tried.append(alt.name)
                state["tried_doctors"] = tried
                state["matched_doctor"] = alt.model_dump()
                state["pending_doctor_name"] = alt.name
                state["template_response"] = ""
                state["llm_prompt"] = DOCTOR_INFO_SUMMARY_PROMPT.format(
                    info=f"{alt.name}: {alt.info}", cta=_booking_cta()
                )
                state["need_llm_stream"] = True
                return state
            else:
                state["tried_doctors"] = tried
                state["template_response"] = (
                    "К сожалению, других специалистов по данному направлению "
                    "сейчас нет. Хотите записаться к другому врачу или уточнить у администратора?"
                )
                state["need_llm_stream"] = False
                return state
        state["template_response"] = (
            "Подскажите, что вас беспокоит? Я подберу другого специалиста."
        )
        state["need_llm_stream"] = False
        return state

    state["template_response"] = "Уточните, пожалуйста, что именно вы хотите узнать."
    state["need_llm_stream"] = False
    return state
