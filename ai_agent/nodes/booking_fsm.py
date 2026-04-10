"""Explicit state machine for the booking flow.

Replaces ad-hoc if-branches in appointment_node with a clean state-dispatch
pattern: BookingState enum + one handler method per state.

States (mirror pending_action):
    IDLE                — no active booking
    CONFIRM_SLOT        — slot offered, waiting for user to accept/reject/clarify
    ASK_NAME            — collecting patient ФИО
    ASK_DOB             — collecting birth date
    CONFIRM_ALT_DOCTOR  — alternative doctor suggested, waiting for agreement
"""
from __future__ import annotations

import random
import re
from enum import Enum
from typing import List, Optional

from ..schemas import DoctorRecord, ExtractedMessage
from ..services.bookings_store import BookingsStore, BookingValidationError
from ..services.doctors_service import DoctorsService
from ..services.medesk_service import MedeskService
from ..state import AgentState
from ..utils.format import decline_name, format_slot
from ..utils.prompts import DOCTOR_INFO_SUMMARY_PROMPT
from ..utils.trace_logger import record_fsm_handler

# ─────────────────────────────────────────────────────────────
# Constants & regexes
# ─────────────────────────────────────────────────────────────

_NON_RELATIVE_KEYWORDS = (
    "сосед", "друг", "подруг", "коллег", "знаком", "сослужив", "начальник",
)

_WHY_DOB_RE = re.compile(r"(зачем|почему|для чего|а что)", re.IGNORECASE)

_CLARIFY_DATE_RE = re.compile(
    r"(какую дату|какая дата|какое число|какого числа|когда именно|"
    r"во сколько|какое время|в какое время|на когда)",
    re.IGNORECASE,
)

# Question-words that indicate a side question rather than a slot rejection.
_SIDE_QUESTION_RE = re.compile(
    r"\b(где|какой|какая|какие|сколько|когда|куда|как(?:\s|ой)|почему|зачем)\b",
    re.IGNORECASE,
)

_SLOT_OFFER_TEMPLATES = [
    "У {gn} свободно {slot}, делаем запись?",
    "У {gn} есть окно {slot}. Записать вас?",
    "У {gn} доступно {slot}. Хотите записаться?",
    "{slot} у {gn} свободно. Вам подходит?",
]


# ─────────────────────────────────────────────────────────────
# State enum
# ─────────────────────────────────────────────────────────────


class BookingState(str, Enum):
    IDLE = "idle"
    CONFIRM_SLOT = "confirm_slot"
    ASK_NAME = "ask_patient_name"
    ASK_DOB = "ask_birth_date"
    CONFIRM_ALT_DOCTOR = "confirm_alt_doctor"

    @classmethod
    def from_pending(cls, pending: Optional[str]) -> "BookingState":
        if pending is None or pending == "":
            return cls.IDLE
        for s in cls:
            if s.value == pending:
                return s
        return cls.IDLE


# ─────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────


def _dn(name: str) -> str:
    return decline_name(name, "genitive")


def _dd(name: str) -> str:
    return decline_name(name, "dative")


def _fs(slot: str) -> str:
    return format_slot(slot)


def _slot_offer(gn: str, slot: str) -> str:
    return random.choice(_SLOT_OFFER_TEMPLATES).format(gn=gn, slot=slot)


def _try_find_doctor_in_text(
    text: str, doctors_service: DoctorsService
) -> Optional[DoctorRecord]:
    for word in text.split():
        if len(word) < 4:
            continue
        doctor = doctors_service.find_by_name(word)
        if doctor:
            return doctor
    return None


# ─────────────────────────────────────────────────────────────
# BookingFSM
# ─────────────────────────────────────────────────────────────


class BookingFSM:
    """Dispatches booking-flow events by explicit state."""

    def __init__(
        self,
        state: AgentState,
        doctors_service: DoctorsService,
        medesk_service: MedeskService,
        bookings_store: Optional[BookingsStore] = None,
    ):
        self.s = state
        self.doctors = doctors_service
        self.medesk = medesk_service
        self.bookings_store = bookings_store or BookingsStore()
        self.extracted: ExtractedMessage = state["extracted"]
        self.intent = self.extracted.patient_intent
        self.state = BookingState.from_pending(state.get("pending_action"))
        self.user_message = state.get("user_message", "")
        self.prev_intent = state.get("prev_intent")
        self.rejected: List[str] = state.get("rejected_slots") or []
        self.session_id: str = state.get("session_id") or "default"

    # ═════════ Public entrypoint ═════════

    def step(self) -> AgentState:
        # Global guard: booking only for self/close family
        if self.state == BookingState.IDLE and self._msg_mentions_non_relative():
            record_fsm_handler("non_relative_guard")
            return self._respond_non_relative()

        # Dispatch by explicit state
        if self.state == BookingState.ASK_NAME:
            record_fsm_handler("on_ask_name")
            return self._on_ask_name()
        if self.state == BookingState.ASK_DOB:
            record_fsm_handler("on_ask_dob")
            return self._on_ask_dob()
        if self.state == BookingState.CONFIRM_ALT_DOCTOR:
            record_fsm_handler("on_confirm_alt_doctor")
            return self._on_confirm_alt_doctor()

        # IDLE or CONFIRM_SLOT — both go through doctor resolution + intent dispatch
        record_fsm_handler(f"on_idle_or_confirm_slot[{self.state.value}]")
        return self._on_idle_or_confirm_slot()

    # ═════════ Global guards ═════════

    def _msg_mentions_non_relative(self) -> bool:
        msg = self.user_message.lower()
        return any(kw in msg for kw in _NON_RELATIVE_KEYWORDS)

    def _respond_non_relative(self) -> AgentState:
        self.s["template_response"] = (
            "К сожалению, записать на приём можно только себя или ближайшего родственника. "
            "Но вы можете передать ему наш номер +7 (499) 302-17-10 — "
            "он позвонит, и мы его запишем. А вас самого что-нибудь беспокоит?"
        )
        self.s["need_llm_stream"] = False
        self.s["pending_action"] = None
        return self.s

    # ═════════ State: ASK_NAME ═════════

    def _on_ask_name(self) -> AgentState:
        name = self.extracted.patient_name
        if name:
            return self._accept_name(name)
        if self.s.get("_disease_confirmed_for_pending_doctor"):
            return self._disease_confirmation_template(asking="name")
        return self._fallback_collecting_data(kind="name")

    def _accept_name(self, name: str) -> AgentState:
        parts = name.strip().split()
        if len(parts) < 2:
            self.s["template_response"] = (
                "Для записи мне нужны имя и фамилия. "
                "Подскажите, пожалуйста, ваше полное ФИО."
            )
            self.s["need_llm_stream"] = False
            return self.s

        self.s["patient_name"] = name
        if not self.s.get("birth_date"):
            self.s["pending_action"] = BookingState.ASK_DOB.value
            self.s["template_response"] = "Подскажите, пожалуйста, дату рождения."
            self.s["need_llm_stream"] = False
        else:
            self._complete_booking(
                self.s.get("selected_slot"),
                self.s.get("pending_doctor_name", "врач"),
            )
        return self.s

    # ═════════ State: ASK_DOB ═════════

    def _on_ask_dob(self) -> AgentState:
        dob = self.extracted.birth_date
        if dob:
            self.s["birth_date"] = dob
            self._complete_booking(
                self.s.get("selected_slot"),
                self.s.get("pending_doctor_name", "врач"),
            )
            return self.s
        if self.s.get("_disease_confirmed_for_pending_doctor"):
            return self._disease_confirmation_template(asking="dob")
        if _WHY_DOB_RE.search(self.user_message):
            self.s["template_response"] = (
                "Дата рождения нужна для оформления медицинской карты в клинике. "
                "Подскажите, пожалуйста, вашу дату рождения."
            )
            self.s["need_llm_stream"] = False
            return self.s
        return self._fallback_collecting_data(kind="dob")

    # ═════════ Disease confirmation shared logic ═════════

    def _disease_confirmation_template(self, asking: str) -> AgentState:
        """When user mentioned a disease while we are collecting name/DOB."""
        diseases = self.s.get("_confirmed_diseases") or []
        doctor_name = self.s.get("pending_doctor_name", "")
        doctor = self.doctors.find_by_name(doctor_name) if doctor_name else None
        if not (doctor and doctor_name and diseases):
            return self._fallback_collecting_data(kind=asking)

        treats = self.doctors.doctor_treats_disease(doctor_name, diseases)
        dn = _dn(doctor_name)
        if asking == "name":
            if treats:
                self.s["template_response"] = (
                    f"Да, {doctor.name} как раз занимается лечением таких состояний. "
                    f"Вы у нас уже записаны к нему. Подскажите, как вас зовут?"
                )
            else:
                self.s["template_response"] = (
                    f"К сожалению, это не специализация {dn}, "
                    f"но мы можем подобрать другого врача после оформления записи. "
                    f"Подскажите, как вас зовут?"
                )
        else:  # dob
            if treats:
                self.s["template_response"] = (
                    f"Да, {doctor.name} как раз занимается лечением таких состояний. "
                    f"Для оформления записи подскажите, пожалуйста, дату рождения."
                )
            else:
                self.s["template_response"] = (
                    f"К сожалению, это не специализация {dn}, "
                    f"но мы можем подобрать другого врача после оформления записи. "
                    f"Подскажите, пожалуйста, дату рождения."
                )
        self.s["need_llm_stream"] = False
        return self.s

    # ═════════ Fallback for data-collection states ═════════

    def _fallback_collecting_data(self, kind: str) -> AgentState:
        """User asked a question instead of providing name/DOB — answer via LLM,
        but ALWAYS end with a re-ask for the missing data."""
        from ..utils.prompts import FALLBACK_SYSTEM_PROMPT

        if kind == "name":
            re_ask = (
                "КОНТЕКСТ: ты сейчас оформляешь запись и ждёшь от пациента ФИО "
                "(имя и фамилию). Ответь на вопрос пациента и ОБЯЗАТЕЛЬНО заверши ответ "
                "вопросом: «Как вас зовут?» Не дублируй этот вопрос — задай его ровно "
                "один раз в конце."
            )
        else:  # dob
            re_ask = (
                "КОНТЕКСТ: ты сейчас оформляешь запись и ждёшь от пациента дату "
                "рождения. Ответь на вопрос пациента и ОБЯЗАТЕЛЬНО заверши ответ "
                "просьбой: «Для оформления записи подскажите, пожалуйста, дату "
                "рождения.» Не дублируй эту просьбу — задай её ровно один раз в конце."
            )
        system_with_context = f"{FALLBACK_SYSTEM_PROMPT}\n\n{re_ask}"

        history = self.s.get("history", [])
        conversation = [{"role": "system", "content": system_with_context}]
        for msg in history:
            conversation.append({"role": msg["role"], "content": msg["content"]})
        conversation.append({"role": "user", "content": self.user_message})

        self.s["template_response"] = None
        self.s["need_llm_stream"] = True
        self.s["llm_prompt"] = None
        self.s["fallback_messages"] = conversation
        return self.s

    # ═════════ State: CONFIRM_ALT_DOCTOR ═════════

    def _on_confirm_alt_doctor(self) -> AgentState:
        # Case 1: client named a different specific doctor
        if self.extracted.doctor_name:
            requested = self.doctors.find_by_name(self.extracted.doctor_name)
            if requested and requested.name != self.s.get("pending_doctor_name"):
                return self._switch_to_requested_doctor(requested)

        # Case 2: client agreed (or didn't name anyone) — offer slots for current alt
        doctor_name = self.s.get("pending_doctor_name")
        doctor = self.doctors.find_by_name(doctor_name) if doctor_name else None
        if not doctor:
            self.s["pending_action"] = None
            self.s["template_response"] = (
                "Подскажите, пожалуйста, к какому врачу вы хотите записаться?"
            )
            self.s["need_llm_stream"] = False
            return self.s

        gn = _dn(doctor.name)
        available = self.medesk.get_available_slots(doctor.name)
        if available:
            slot = available[0]
            self.s["selected_slot"] = slot
            self.s["pending_action"] = BookingState.CONFIRM_SLOT.value
            self.s["template_response"] = _slot_offer(gn, _fs(slot))
            self.s["need_llm_stream"] = False
            return self.s
        return self._suggest_similar_doctor(
            doctor.name, gn, f"К сожалению, у {gn} сейчас нет свободных слотов.",
        )

    def _switch_to_requested_doctor(self, requested: DoctorRecord) -> AgentState:
        gn = _dn(requested.name)
        all_slots = self.medesk.get_available_slots(requested.name)
        self.s["pending_doctor_name"] = requested.name
        self.s["matched_doctor"] = requested.model_dump()
        self.s["rejected_slots"] = []
        self.s["tried_doctors"] = []
        if all_slots:
            slots_formatted = ", ".join(_fs(s) for s in all_slots)
            self.s["selected_slot"] = all_slots[0]
            self.s["pending_action"] = BookingState.CONFIRM_SLOT.value
            self.s["template_response"] = (
                f"Понимаю. По данным CRM-системы, у {gn} доступны следующие слоты: "
                f"{slots_formatted}. Возможно, какой-то из них вам подойдёт?"
            )
        else:
            self.s["pending_action"] = None
            self.s["template_response"] = (
                f"К сожалению, по данным CRM-системы у {gn} сейчас нет свободных слотов."
            )
        self.s["need_llm_stream"] = False
        return self.s

    # ═════════ IDLE / CONFIRM_SLOT (normal intent routing) ═════════

    def _on_idle_or_confirm_slot(self) -> AgentState:
        doctor_name = self._resolve_doctor_name()

        doctor_found_by_disease = False
        if not doctor_name:
            doctor, doctor_found_by_disease = self._find_doctor_by_message()
            if doctor:
                doctor_name = doctor.name
            else:
                return self._no_doctor_context_response()

        doctor = self.doctors.find_by_name(doctor_name)
        if not doctor:
            doctor = _try_find_doctor_in_text(self.user_message, self.doctors)
        if not doctor:
            return self._doctor_not_found_response(doctor_name)

        # Commit doctor to state
        self.s["matched_doctor"] = doctor.model_dump()
        self.s["pending_doctor_name"] = doctor.name
        gn = _dn(doctor.name)

        # If found via disease, intro the doctor before offering slot
        if doctor_found_by_disease:
            self.s["template_response"] = f"Рекомендую {doctor.name}. "
            self.s["llm_prompt"] = DOCTOR_INFO_SUMMARY_PROMPT.format(
                info=doctor.info, cta="Записать вас на приём?"
            )
            self.s["need_llm_stream"] = True
            return self.s

        # Intent-based dispatch
        if self.intent in ("make_appoint", "asks_about_slot"):
            return self._handle_slot_request(doctor, gn)
        if self.intent == "slot_not_suitable":
            return self._handle_slot_reject(doctor, gn)
        if self.intent == "chosen_slot":
            return self._handle_slot_accept(doctor)

        # Fallback inside doctor context
        self.s["template_response"] = (
            "Подскажите, пожалуйста, удобные дату и время для записи."
        )
        self.s["need_llm_stream"] = False
        return self.s

    # ─── doctor resolution helpers ───

    def _resolve_doctor_name(self) -> Optional[str]:
        # After general context (FAQ, fallback) without booking progress, drop old doctor
        general_prev = (
            self.prev_intent in ("question_about_clinic", "arbitrary_message", None)
            and not self.s.get("pending_action")
            and not self.s.get("pending_doctor_name")
        )
        if general_prev:
            return self.extracted.doctor_name
        return self.extracted.doctor_name or self.s.get("pending_doctor_name")

    def _find_doctor_by_message(self) -> tuple[Optional[DoctorRecord], bool]:
        """Returns (doctor, found_by_disease). Falls back through name/disease/procedure."""
        doctor = _try_find_doctor_in_text(self.user_message, self.doctors)
        if doctor:
            return doctor, False
        if self.extracted.diseases:
            doctor = self.doctors.find_by_disease(self.extracted.diseases)
            if doctor:
                return doctor, True
        if self.extracted.procedure:
            doctor = self.doctors.find_by_procedure(self.extracted.procedure)
            if doctor:
                return doctor, False
        return None, False

    def _no_doctor_context_response(self) -> AgentState:
        """No doctor found AND no pending doctor — acknowledge intent & ask what bothers."""
        if self.s.get("pending_doctor_name"):
            self.s["template_response"] = (
                "Подскажите, пожалуйста, к какому врачу вы хотите записаться?"
            )
            self.s["need_llm_stream"] = False
            return self.s

        wants_booking = self.intent in ("make_appoint", "asks_about_slot", "chosen_slot")
        slot_hint = ""
        if self.extracted.patient_slots:
            slot_hint = f" на {self.extracted.patient_slots[0]}"

        if wants_booking:
            exp_note = self._build_experience_hint()
            self.s["template_response"] = (
                f"Конечно, оформляю запись{slot_hint}.{exp_note} "
                f"Подскажите, пожалуйста, что вас беспокоит или к какому "
                f"специалисту вы хотите попасть — я подберу подходящего врача."
            )
        else:
            self.s["template_response"] = (
                "Подскажите, пожалуйста, что вас беспокоит? "
                "Так я смогу подобрать подходящего специалиста."
            )
        self.s["need_llm_stream"] = False
        return self.s

    def _build_experience_hint(self) -> str:
        """Build the 'опыт от X до Y лет (напр., Иванов — 32 года)' fragment."""
        def _exp_years(d: DoctorRecord) -> int:
            m = re.search(r"(\d+)", d.experience or "")
            return int(m.group(1)) if m else 0

        ranked = sorted(self.doctors.doctors, key=_exp_years, reverse=True)
        examples = ranked[:2]
        if not examples:
            return ""
        ex_line = ", ".join(
            f"{d.name.split()[0]} — стаж {d.experience}" for d in examples
        )
        return f" Опыт наших психиатров — от 2 до 32 лет (например, {ex_line})."

    def _doctor_not_found_response(self, doctor_name: str) -> AgentState:
        if len(doctor_name.split()) >= 2:
            self.s["template_response"] = (
                "К сожалению, в нашей клинике нет врача с таким именем. "
                "Хотите записаться к другому специалисту?"
            )
        else:
            self.s["template_response"] = (
                f"Не удалось найти врача по имени {doctor_name}. "
                "Подскажите, пожалуйста, полное ФИО."
            )
        self.s["need_llm_stream"] = False
        return self.s

    # ─── slot handlers ───

    def _handle_slot_request(
        self, doctor: DoctorRecord, gn: str,
    ) -> AgentState:
        """make_appoint / asks_about_slot."""
        current = self.s.get("selected_slot")

        # Clarifying question about the already-offered slot — restate, don't rotate
        if (
            self.state == BookingState.CONFIRM_SLOT
            and current
            and not self.extracted.patient_slots
            and _CLARIFY_DATE_RE.search(self.user_message)
        ):
            self.s["template_response"] = f"Предлагаю {_fs(current)} у {gn}. Вам подходит?"
            self.s["need_llm_stream"] = False
            return self.s

        # Asking for new slots implicitly rejects the current one
        if current and current not in self.rejected:
            self.rejected = self.rejected + [current]
            self.s["rejected_slots"] = self.rejected

        if self.extracted.patient_slots:
            return self._offer_or_alternate_slot(doctor, gn)
        return self._offer_first_available_slot(doctor, gn)

    def _offer_or_alternate_slot(
        self, doctor: DoctorRecord, gn: str,
    ) -> AgentState:
        matched, alternatives = self.medesk.find_matching_or_next(
            doctor.name, self.extracted.patient_slots
        )
        if matched:
            slot = matched[0]
            self.s["selected_slot"] = slot
            self.s["pending_action"] = BookingState.CONFIRM_SLOT.value
            self.s["template_response"] = f"Да, {_slot_offer(gn, _fs(slot))}"
            self.s["need_llm_stream"] = False
            return self.s

        alternatives = [s for s in alternatives if s not in self.rejected]
        alternative = alternatives[0] if alternatives else None
        requested_text = ", ".join(self.extracted.patient_slots)
        if alternative:
            self.s["selected_slot"] = alternative
            self.s["pending_action"] = BookingState.CONFIRM_SLOT.value
            self.s["template_response"] = (
                f"К сожалению, у {gn} на {requested_text} уже занято, "
                f"предлагаем вам запись на {_fs(alternative)}. Вам будет удобно?"
            )
            self.s["need_llm_stream"] = False
            return self.s

        return self._suggest_similar_doctor(
            doctor.name, gn,
            f"К сожалению, у {gn} сейчас нет свободных слотов на запрошенное время.",
        )

    def _offer_first_available_slot(
        self, doctor: DoctorRecord, gn: str,
    ) -> AgentState:
        available = self.medesk.get_available_slots(doctor.name)
        available = [s for s in available if s not in self.rejected]
        if not available:
            return self._suggest_similar_doctor(
                doctor.name, gn, f"К сожалению, у {gn} сейчас нет свободных слотов.",
            )
        slot = available[0]
        self.s["selected_slot"] = slot
        self.s["pending_action"] = BookingState.CONFIRM_SLOT.value
        self.s["template_response"] = _slot_offer(gn, _fs(slot))
        self.s["need_llm_stream"] = False
        return self.s

    def _handle_slot_reject(
        self, doctor: DoctorRecord, gn: str,
    ) -> AgentState:
        # Guard: any "?" in the message means it's a question, not a rejection.
        # Rejection would be a plain "Нет" / "неудобно" without a question mark.
        if "?" in self.user_message:
            current_slot = self.s.get("selected_slot")
            if current_slot:
                self.s["template_response"] = (
                    f"Предлагаю {_fs(current_slot)} у {gn}. Вам подходит?"
                )
                self.s["need_llm_stream"] = False
                return self.s

        current = self.s.get("selected_slot")
        if current and current not in self.rejected:
            self.rejected = self.rejected + [current]
            self.s["rejected_slots"] = self.rejected

        available = self.medesk.get_available_slots(doctor.name)
        alternatives = [s for s in available if s not in self.rejected]
        if not alternatives:
            return self._suggest_similar_doctor(
                doctor.name, gn,
                f"К сожалению, других свободных слотов у {gn} сейчас нет.",
            )
        new_slot = alternatives[0]
        self.s["selected_slot"] = new_slot
        self.s["pending_action"] = BookingState.CONFIRM_SLOT.value
        self.s["template_response"] = (
            f"Тогда можем предложить {_fs(new_slot)} у {gn}. Вам будет удобно?"
        )
        self.s["need_llm_stream"] = False
        return self.s

    def _handle_slot_accept(self, doctor: DoctorRecord) -> AgentState:
        # Allow late slot commit from extracted
        if not self.s.get("selected_slot") and self.extracted.patient_slots:
            self.s["selected_slot"] = self.extracted.patient_slots[0]

        # User agreed to book ("да") but no concrete slot was offered yet —
        # offer the first available slot first, then the next turn will collect data.
        if not self.s.get("selected_slot"):
            gn = _dn(doctor.name)
            return self._offer_first_available_slot(doctor, gn)

        # If we just returned from a detour (doctor/clinic question), re-confirm slot
        detour_intents = ("question_about_doctor", "question_about_clinic")
        if (
            self.prev_intent in detour_intents
            and self.s.get("selected_slot")
            and self.state != BookingState.CONFIRM_SLOT
        ):
            slot_text = _fs(self.s["selected_slot"])
            self.s["pending_action"] = BookingState.CONFIRM_SLOT.value
            if self.rejected:
                rejected_text = ", ".join(_fs(s) for s in self.rejected[-1:])
                self.s["template_response"] = (
                    f"Хорошо, смотрю расписание. {rejected_text} вы сказали не подходит, "
                    f"поэтому предлагаю {slot_text}. Вам будет удобно?"
                )
            else:
                self.s["template_response"] = (
                    f"Отлично, вам подходит время {slot_text}?"
                )
            self.s["need_llm_stream"] = False
            return self.s

        # Move toward booking completion: name → DOB → commit
        patient_name = self.s.get("patient_name")
        if not patient_name or len(patient_name.strip().split()) < 2:
            self.s["pending_action"] = BookingState.ASK_NAME.value
            slot_text = (
                _fs(self.s["selected_slot"]) if self.s.get("selected_slot") else ""
            )
            if patient_name:
                self.s["template_response"] = (
                    "Для записи мне нужны имя и фамилия. "
                    "Подскажите, пожалуйста, ваше полное ФИО."
                )
            elif slot_text:
                self.s["template_response"] = (
                    f"Отлично, записываю вас на {slot_text}. Как вас зовут?"
                )
            else:
                self.s["template_response"] = "Отлично, записываю. Как вас зовут?"
            self.s["need_llm_stream"] = False
            return self.s

        if not self.s.get("birth_date"):
            self.s["pending_action"] = BookingState.ASK_DOB.value
            self.s["template_response"] = "Подскажите, пожалуйста, дату рождения."
            self.s["need_llm_stream"] = False
            return self.s

        self._complete_booking(self.s["selected_slot"], doctor.name)
        return self.s

    # ═════════ Shared actions ═════════

    def _complete_booking(self, slot: Optional[str], doctor_name: str) -> None:
        """Finalize booking. HARD GUARD: validates all fields and persists
        to BookingsStore. If any required field missing — does NOT set
        booking_completed; instead reverts to the state that collects the
        missing data.
        """
        patient_name = self.s.get("patient_name")
        birth_date = self.s.get("birth_date")

        if not slot and doctor_name:
            available = self.medesk.get_available_slots(doctor_name)
            if available:
                slot = available[0]
                self.s["selected_slot"] = slot

        # Fallback recovery: missing fields → reroute to collect them.
        if not slot or not doctor_name:
            self.s["pending_action"] = None
            self.s["template_response"] = (
                "Подскажите, пожалуйста, к какому врачу вы хотите записаться "
                "и на какое время."
            )
            self.s["need_llm_stream"] = False
            return
        if not patient_name or len(patient_name.strip().split()) < 2:
            self.s["pending_action"] = BookingState.ASK_NAME.value
            self.s["template_response"] = (
                "Для оформления записи подскажите, пожалуйста, ваше имя и фамилию."
            )
            self.s["need_llm_stream"] = False
            return
        if not birth_date:
            self.s["pending_action"] = BookingState.ASK_DOB.value
            self.s["template_response"] = "Подскажите, пожалуйста, дату рождения."
            self.s["need_llm_stream"] = False
            return

        # All fields present → persist to store
        try:
            self.bookings_store.append(
                session_id=self.session_id,
                patient_name=patient_name,
                birth_date=birth_date,
                doctor_name=doctor_name,
                slot=slot,
            )
        except BookingValidationError as exc:
            # Defensive — we just validated, but if store rejects, surface it
            self.s["pending_action"] = None
            self.s["template_response"] = (
                f"Извините, не получилось оформить запись: {exc}. "
                f"Попробуйте, пожалуйста, ещё раз."
            )
            self.s["need_llm_stream"] = False
            return

        # Success — set confirmation template AND mark completed
        slot_text = _fs(slot)
        self.s["pending_action"] = None
        self.s["template_response"] = (
            f"Вы записаны на {slot_text} к врачу {_dd(doctor_name)}, "
            f"уведомление придёт вам по номеру. "
            f"Ждём вас на приёме! Могу ещё чем-то помочь?"
        )
        self.s["need_llm_stream"] = False
        self.s["selected_slot"] = None
        self.s["rejected_slots"] = []
        self.s["matched_doctor"] = None
        self.s["booking_completed"] = True

    def _suggest_similar_doctor(
        self, doctor_name: str, gn: str, no_slots_message: str,
    ) -> AgentState:
        tried = list(self.s.get("tried_doctors") or [])
        if doctor_name not in tried:
            tried.append(doctor_name)

        original = tried[0] if tried else doctor_name
        alt = self.doctors.find_similar_doctor(original, exclude=tried)
        if alt:
            alt_slots = self.medesk.get_available_slots(alt.name)
            if not alt_slots:
                alt = None

        if not alt:
            self.s["tried_doctors"] = tried
            self.s["template_response"] = (
                f"{no_slots_message} "
                f"К сожалению, сейчас нет свободных специалистов по данному направлению. "
                f"Оставьте, пожалуйста, ваш номер телефона — мы перезвоним, "
                f"как только появится запись."
            )
            self.s["need_llm_stream"] = False
            self.s["pending_action"] = None
            return self.s

        tried.append(alt.name)
        self.s["tried_doctors"] = tried
        self.s["pending_doctor_name"] = alt.name
        self.s["matched_doctor"] = alt.model_dump()
        self.s["rejected_slots"] = []
        self.s["selected_slot"] = None
        self.s["pending_action"] = BookingState.CONFIRM_ALT_DOCTOR.value
        self.s["template_response"] = (
            f"{no_slots_message} Могу предложить вам запись к {_dd(alt.name)}. "
        )
        self.s["llm_prompt"] = DOCTOR_INFO_SUMMARY_PROMPT.format(
            info=f"{alt.name}: {alt.info}", cta="Записать вас на приём?"
        )
        self.s["need_llm_stream"] = True
        return self.s


# ─────────────────────────────────────────────────────────────
# Public entrypoint used by appointment_node
# ─────────────────────────────────────────────────────────────


def run_booking_fsm(
    state: AgentState,
    doctors_service: DoctorsService,
    medesk_service: MedeskService,
    bookings_store: Optional[BookingsStore] = None,
) -> AgentState:
    return BookingFSM(
        state, doctors_service, medesk_service, bookings_store=bookings_store,
    ).step()
