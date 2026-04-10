from __future__ import annotations
import re
from typing import Dict, Generator, List, Optional

from .graph import build_graph
from .schemas import AgentChunk
from .services.llm_service import LLMService
from .services.bookings_store import BookingsStore
from .services.doctors_service import DoctorsService
from .services.medesk_service import MedeskService
from .services.faq_service import FAQService
from .services.disease_matcher import DiseaseMatcher
from .utils.streaming import prepend_chunk_and_stream, single_chunk_stream
from .utils.prompts import build_full_system_prompt
from .utils.booking_guard import strip_fake_booking_claims
from .utils.trace_logger import (
    record_error,
    record_response,
    record_state_after,
    record_state_before,
    start_trace,
)

GREETING_TEXT = "Здравствуйте! Клиника Psy Family, чем могу помочь?"


class PsyFamilyAgent:
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
    ):
        self.llm = LLMService(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
        self.doctors_service = DoctorsService()
        self.medesk_service = MedeskService()
        self.faq_service = FAQService()
        self.disease_matcher = DiseaseMatcher()
        self.bookings_store = BookingsStore()

        self.graph = build_graph(
            llm=self.llm,
            doctors_service=self.doctors_service,
            medesk_service=self.medesk_service,
            faq_service=self.faq_service,
            disease_matcher=self.disease_matcher,
            bookings_store=self.bookings_store,
        )

        # Build full system prompt once with all clinic knowledge
        self.full_system_prompt = build_full_system_prompt(
            doctors_json=[d.model_dump() for d in self.doctors_service.doctors],
            faq_items=[{"question": f.question, "answer": f.answer} for f in self.faq_service.items],
        )

        self.sessions: Dict[str, Dict] = {}

    def _get_session_state(self, session_id: str) -> Dict:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "history": [],
                "pending_action": None,
                "pending_doctor_name": None,
                "patient_name": None,
                "birth_date": None,
                "selected_slot": None,
                "rejected_slots": [],
                "tried_doctors": [],
                "call_signal": None,
                "booking_completed": False,
            }
        return self.sessions[session_id]

    def get_greeting(self) -> str:
        return GREETING_TEXT

    def get_call_signal(self, session_id: str = "default") -> Optional[str]:
        """Return last call signal: 'end_call', 'transfer_operator', or None."""
        return self.sessions.get(session_id, {}).get("call_signal")

    def drop_session(self, session_id: str) -> None:
        """Clean up session when call ends."""
        self.sessions.pop(session_id, None)

    def __call__(self, message: str, session_id: str = "default") -> Generator[AgentChunk, None, None]:
        session = self._get_session_state(session_id)
        trace = start_trace(session_id, message)

        input_state = {
            "user_message": message,
            "session_id": session_id,
            "history": session.get("history", []),
            "pending_action": session.get("pending_action"),
            "pending_doctor_name": session.get("pending_doctor_name"),
            "patient_name": session.get("patient_name"),
            "birth_date": session.get("birth_date"),
            "selected_slot": session.get("selected_slot"),
            "rejected_slots": session.get("rejected_slots", []),
            "tried_doctors": session.get("tried_doctors", []),
            "prev_intent": session.get("prev_intent"),
            "booking_completed": session.get("booking_completed", False),
            "full_system_prompt": self.full_system_prompt,
        }

        record_state_before(input_state)

        try:
            result = self.graph.invoke(input_state)
        except Exception as exc:
            record_error(f"{type(exc).__name__}: {exc}")
            trace.emit()
            raise

        record_state_after(result)

        # Save previous pending_action BEFORE updating session (needed for additional_questions logic)
        _prev_pending = session.get("pending_action")

        session["pending_action"] = result.get("pending_action")
        session["pending_doctor_name"] = result.get("pending_doctor_name")
        session["patient_name"] = result.get("patient_name")
        session["birth_date"] = result.get("birth_date")
        session["selected_slot"] = result.get("selected_slot")
        session["rejected_slots"] = result.get("rejected_slots", session.get("rejected_slots", []))
        session["tried_doctors"] = result.get("tried_doctors", session.get("tried_doctors", []))
        session["prev_intent"] = result["extracted"].patient_intent
        session["call_signal"] = result.get("call_signal")
        if result.get("booking_completed"):
            session["booking_completed"] = True

        session["history"].append({"role": "user", "content": message})

        template_response = result.get("template_response")
        need_llm_stream = result.get("need_llm_stream", False)

        # Append booking CTA when answering side questions during booking flow
        booking_cta = result.get("_booking_cta")
        if booking_cta:
            if need_llm_stream:
                result["_append_after_llm"] = (result.get("_append_after_llm") or "") + booking_cta
            elif template_response is not None:
                template_response = template_response.rstrip() + booking_cta

        # Handle additional questions from compound messages
        # Skip when bot is collecting data and user provided it (name, DOB)
        extracted = result.get("extracted")
        pending = result.get("pending_action")
        data_just_collected = (
            (_prev_pending == "ask_patient_name" and pending != "ask_patient_name")
            or (_prev_pending == "ask_birth_date" and pending != "ask_birth_date")
        )
        additional_qs = []
        if not data_just_collected:
            additional_qs = getattr(extracted, "additional_questions", []) if extracted else []

        # Dedupe paraphrases: LLM extraction sometimes restates the primary
        # question as an "additional" one. Only active when primary handler is
        # FAQ — in that case the answer is self-contained and any additional
        # matching the same topic is a duplicate. For booking/doctor_info
        # handlers, the additional is typically a genuine secondary request.
        if additional_qs and result.get("final_route") == "faq":
            def _stems(s: str) -> set[str]:
                return {w[:5] for w in re.findall(r"\w+", s.lower()) if len(w) > 2}
            def _is_paraphrase(primary: str, additional: str) -> bool:
                p, a = _stems(primary), _stems(additional)
                if not a:
                    return True
                return (len(p & a) / len(a)) >= 0.6
            additional_qs = [q for q in additional_qs if not _is_paraphrase(message, q)]

        # Fallback: if message has multiple questions but extraction missed them
        if not additional_qs and message.count("?") >= 2:
            # Split by "?" and take everything after the first question as additional
            parts = [p.strip() for p in message.split("?") if p.strip()]
            if len(parts) >= 2:
                additional_qs = [p + "?" for p in parts[1:]]

        # Detect compound patterns: "Да, подходит. А сколько длится...", "Запишите, и расскажите..."
        # BUT skip when the first part is a single-word ack ("Нет", "Да", "Ок")
        # answering the bot's previous question — that's not a compound, it's
        # an ack + new message.
        _ACK_WORDS = {"да", "нет", "ок", "окей", "хорошо", "понятно", "угу", "ага", "нет.", "да."}
        if not additional_qs:
            compound = re.split(
                r'[.!](?:\s+(?:а|и|А|И|Также|также|Ещё|ещё|Еще|еще)\s+|\s+(?=[А-ЯA-Z]))'
                r'|,\s*(?:а|и)\s+',
                message, maxsplit=2,
            )
            if len(compound) >= 2:
                first = compound[0].strip().strip(",.!?").lower()
                if first not in _ACK_WORDS:
                    extra = [p.strip() for p in compound[1:] if len(p.strip()) > 8]
                    if extra:
                        additional_qs = extra

        _aq_suffix = (
            "ПРАВИЛА: пиши ТОЛЬКО текст для пациента, без преамбул, пояснений, "
            "без фраз «можно добавить», «далее ответ», без кавычек-ёлочек вокруг ответа. "
            "Не повторяй уже сказанное. Не предлагай запись повторно. "
            "Говори от лица оператора Алисы, не от лица врача. "
            "Факты об адресе, метро, телефоне клиники бери ТОЛЬКО из системного промпта — "
            "не выдумывай станции метро и не упоминай тех, которых нет в знаниях. "
            "Если вопрос про адрес/как добраться — обязательно дай полный адрес клиники "
            "(улица, дом) из базы знаний. "
            "Если вопрос про врача (стаж, опыт, специализация), а врач ещё не выбран — "
            "ответь общей фразой про клинику («наши специалисты с опытом от 10 лет»), "
            "не называй конкретных имён. "
            "Если вопрос про даты/слоты, а врач не выбран — не перечисляй даты."
        )

        # Deterministic short-circuit: compound where secondary is about booking/dates/slots
        # but there's no doctor context — always add a templated "к какому врачу?" suffix
        # instead of relying on LLM that sometimes skips this.
        _BOOKING_DATES_RE = re.compile(
            r"(на какие даты|какие даты|когда можно записаться|какие слоты|"
            r"есть\s+запись|когда\s+есть\s+(свободн|запись)|записаться)",
            re.IGNORECASE,
        )
        has_doctor_context = bool(
            (extracted and getattr(extracted, "doctor_name", None))
            or result.get("pending_doctor_name")
        )
        if (
            additional_qs
            and not has_doctor_context
            and any(_BOOKING_DATES_RE.search(q) for q in additional_qs)
        ):
            booking_cta_suffix = " Подскажите, пожалуйста, к какому врачу вы хотите записаться?"
            if template_response is not None:
                template_response = (
                    template_response.rstrip().rstrip("?.!")
                    + "." + booking_cta_suffix
                )
            else:
                # LLM-stream primary (e.g. FAQ) — append deterministic CTA after stream
                result["_append_after_llm"] = (
                    (result.get("_append_after_llm") or "") + booking_cta_suffix
                )
            additional_qs = [q for q in additional_qs if not _BOOKING_DATES_RE.search(q)]

        if additional_qs and not need_llm_stream and template_response is not None:
            # Template response → convert to LLM mode: prefix template, LLM answers extras
            need_llm_stream = True
            qs_text = "\n".join(f"- {q}" for q in additional_qs)
            result["llm_prompt"] = (
                f"Напиши одно короткое продолжение ответа пациенту (1–2 предложения). "
                f"Начало ответа уже отправлено: «{template_response.strip()}» — "
                f"продолжай с новой строки, отвечая на доп. вопрос(ы):\n{qs_text}\n\n"
                f"ВАЖНО: в начале ответа уже есть нужный CTA/вопрос — "
                f"НЕ добавляй в конце никакого call-to-action, никаких вопросов "
                f"вроде «Как вас зовут?», «Записать вас?», «Могу чем-то помочь?». "
                f"Просто ответь на доп. вопрос и закончи. "
                f"{_aq_suffix}"
            )
            result["_prepend_template"] = template_response
        elif additional_qs and need_llm_stream:
            # LLM stream response → inject additional questions into LLM prompt
            qs_text = "\n".join(f"- {q}" for q in additional_qs)
            aq_hint = (
                f"\n\nВАЖНО: пациент также задал дополнительные вопросы, "
                f"на которые тоже нужно ответить:\n{qs_text}\n"
                f"Ответь на них кратко (1–2 предложения). {_aq_suffix}"
            )
            # Append to llm_prompt or to the last user message in fallback_messages
            if result.get("llm_prompt"):
                result["llm_prompt"] = result["llm_prompt"] + aq_hint
            elif result.get("fallback_messages"):
                result["fallback_messages"].append({"role": "system", "content": aq_hint})

        def save_assistant_message(full_text: str):
            session["history"].append({"role": "assistant", "content": full_text})

        if not need_llm_stream:
            text = strip_fake_booking_claims(template_response or "", result)
            def generator():
                acc = ""
                for chunk in single_chunk_stream(text):
                    acc += chunk.text
                    yield chunk
                save_assistant_message(acc)
                record_response("template", acc)
                trace.emit()
            return generator()

        sys_prompt = self.full_system_prompt

        if result.get("fallback_messages"):
            messages = result["fallback_messages"]
        elif result.get("llm_prompt"):
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": result["llm_prompt"]},
            ]
        else:
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": message},
            ]

        # Inject anti-repeat hint into the system prompt (not as a separate message)
        recent_replies = [
            m["content"] for m in session.get("history", [])
            if m["role"] == "assistant"
        ][-3:]
        if recent_replies:
            hint = (
                "\n\n### Антиповтор\n"
                "Не повторяй свои предыдущие ответы дословно. Перефразируй.\n"
                "Твои последние реплики:\n"
                + "\n".join(f"— {r[:120]}" for r in recent_replies)
            )
            # Append to the first system message instead of adding a new one
            for msg in messages:
                if msg["role"] == "system":
                    msg["content"] += hint
                    break

        llm_stream = self.llm.stream_text(messages)
        append_after = result.get("_append_after_llm")
        _has_history = len(session.get("history", [])) > 0

        prepend_tpl = result.get("_prepend_template")

        def generator():
            # Collect the full text first, filter, then yield as one chunk.
            # Streaming is sacrificed to enforce the booking-claim guard.
            raw = ""
            if prepend_tpl:
                for chunk in prepend_chunk_and_stream(prepend_tpl + "\n", llm_stream, strip_greeting=_has_history):
                    raw += chunk.text
            elif template_response is not None:
                for chunk in prepend_chunk_and_stream(template_response, llm_stream, strip_greeting=_has_history):
                    raw += chunk.text
            else:
                for token in llm_stream:
                    raw += token

            cleaned = strip_fake_booking_claims(raw, result)
            if cleaned:
                yield AgentChunk(text=cleaned, type="llm")
            acc = cleaned
            if append_after:
                acc += append_after
                yield AgentChunk(text=append_after, type="template")
            save_assistant_message(acc)
            record_response("llm_stream", acc)
            trace.emit()

        return generator()
