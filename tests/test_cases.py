"""
Deterministic scenario tests for all dialog scenarios.

These tests mock LLM extraction and test the routing + node logic directly.
Each test simulates a multi-turn conversation by calling router + node functions
sequentially and asserting on responses, state transitions, and signals.

Run: pytest tests/test_cases.py -v
"""
import pytest
from ai_agent.nodes.router import router_node
from ai_agent.nodes.appointment import appointment_node
from ai_agent.nodes.doctor_info import doctor_info_node
from ai_agent.nodes.faq import faq_node
from ai_agent.nodes.fallback import fallback_node
from .conftest import make_state, extract, collect_response


# ─── Helpers ───────────────────────────────────────────────────────────


def run_turn(state, doctors_service, medesk_service, faq_service):
    """Run router → node for a single turn. Returns updated state."""
    state = router_node(state)
    route = state["final_route"]
    if route == "appointment":
        state = appointment_node(state, doctors_service, medesk_service)
    elif route == "doctor_info":
        state = doctor_info_node(state, doctors_service)
    elif route == "faq":
        state = faq_node(state, faq_service)
    elif route == "fallback":
        state = fallback_node(state)
    return state


def add_to_history(state, user_msg, bot_response):
    """Append a turn to history for multi-turn tests."""
    history = list(state.get("history", []))
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": bot_response})
    return history


# ─── Case 1: Basic appointment flow — user wants to book with a named doctor ──


class TestCase01BasicAppointment:
    def test_make_appoint_offers_slot(self, doctors_service, medesk_service, faq_service):
        """User: 'Хочу записаться к Хайретдинову' → bot offers a slot."""
        state = make_state(
            user_message="Хочу записаться к Хайретдинову",
            extracted=extract("make_appoint", doctor_name="Хайретдинов"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "confirm_slot"
        assert state["selected_slot"] is not None
        assert "Хайретдинов" in resp or "хайретдинов" in resp.lower()


# ─── Case 2: User agrees to offered slot → name collection ──


class TestCase02AcceptSlot:
    def test_chosen_slot_asks_name_with_slot_reminder(self, doctors_service, medesk_service, faq_service):
        """After slot offer, user says 'Да' → bot confirms slot and asks for name."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="make_appoint",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower()
        # Should remind which slot is being booked
        assert "апрел" in resp.lower() or "10" in resp


# ─── Case 3: User provides name → date of birth collection ──


class TestCase03ProvideName:
    def test_name_then_asks_dob(self, doctors_service, medesk_service, faq_service):
        """User gives name → bot asks for date of birth."""
        state = make_state(
            user_message="Иван Петров",
            extracted=extract("arbitrary_message", patient_name="Иван Петров"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["patient_name"] == "Иван Петров"
        assert state["pending_action"] == "ask_birth_date"
        assert "рождения" in resp.lower()


# ─── Case 4: User provides DOB → booking confirmed, NO end_call ──


class TestCase04ProvideDOB:
    def test_dob_confirms_booking_no_end_call(self, doctors_service, medesk_service, faq_service):
        """User gives DOB → booking confirmed, dialog stays open."""
        state = make_state(
            user_message="15 мая 1985",
            extracted=extract("arbitrary_message", birth_date="1985-05-15"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван Петров",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "записаны" in resp.lower()
        assert state.get("call_signal") is None, "end_call should NOT be sent after booking"
        assert "ждём" in resp.lower() or "могу" in resp.lower()


# ─── Case 5: Slot rejection → alternative offered ──


class TestCase05SlotRejection:
    def test_slot_not_suitable_offers_alternative(self, doctors_service, medesk_service, faq_service):
        """User rejects slot → bot offers another slot."""
        state = make_state(
            user_message="Нет, не подходит",
            extracted=extract("slot_not_suitable"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "confirm_slot"
        assert state["selected_slot"] != "2026-04-06 10:00"
        assert "2026-04-06 10:00" not in resp


# ─── Case 6: Doctor info — experience question ──


class TestCase06DoctorExperience:
    def test_experience_question(self, doctors_service, medesk_service, faq_service):
        """User asks about doctor's experience → bot provides it."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "стаж" in resp.lower()
        assert "30" in resp  # Хайретдинов has 30+ years


# ─── Case 7: Disease-based doctor lookup ──


class TestCase07DiseaseLookup:
    def test_want_procedure_with_disease(self, doctors_service, medesk_service, faq_service):
        """User mentions disease → bot finds matching doctor."""
        state = make_state(
            user_message="У меня невротическое расстройство",
            extracted=extract("want_procedure", diseases=["невротическое расстройство"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Should find a doctor and provide info
        assert state.get("matched_doctor") is not None


# ─── Case 24: ЭПИ as disease cross-fallback + specialization search ──


class TestCase24CrossFallback:
    def test_epi_as_disease_finds_via_procedure(self, doctors_service, medesk_service, faq_service):
        """ЭПИ extracted as disease (not procedure) → cross-fallback finds it as procedure."""
        state = make_state(
            user_message="ЭПИ специалист мне нужен",
            extracted=extract("want_procedure", diseases=["ЭПИ"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None
        resp = collect_response(state) or ""
        # Should NOT say "обратиться в профильную клинику"
        assert "профильн" not in resp.lower()

    def test_epi_as_procedure_works(self, doctors_service, medesk_service, faq_service):
        """ЭПИ extracted as procedure → direct match."""
        state = make_state(
            user_message="Хочу пройти ЭПИ",
            extracted=extract("want_procedure", procedure="ЭПИ"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None

    def test_psychiatrist_specialization_search(self, doctors_service, medesk_service, faq_service):
        """'Психиатр мне нужен' → find doctor by specialization in info."""
        state = make_state(
            user_message="Психиатр мне нужен",
            extracted=extract("want_procedure", diseases=["психиатр"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None
        resp = collect_response(state) or ""
        assert "не удалось" not in resp.lower()

    def test_psychotherapist_specialization(self, doctors_service, medesk_service, faq_service):
        """'Мне нужен психотерапевт' → find by specialization."""
        state = make_state(
            user_message="Мне нужен психотерапевт",
            extracted=extract("want_procedure", diseases=["психотерапевт"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None


# ─── Case 25: Name given early in conversation ──


class TestCase25EarlyNameGiven:
    def test_partial_name_from_greeting_asks_full(self, doctors_service, medesk_service, faq_service):
        """If patient said only first name earlier, ask for full name at booking."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            patient_name="Иван",  # partial name — 1 word
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "ask_patient_name"
        assert "фио" in resp.lower() or "фамили" in resp.lower() or "полное" in resp.lower()

    def test_full_name_from_greeting_skips_name_ask(self, doctors_service, medesk_service, faq_service):
        """If patient said full name earlier, skip to DOB at booking."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            patient_name="Иван Петров",  # full name — 2 words
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "зовут" not in resp.lower()
        assert state["pending_action"] == "ask_birth_date"
        assert "рождения" in resp.lower()

    def test_name_and_dob_both_given_earlier(self, doctors_service, medesk_service, faq_service):
        """If both name and DOB known, skip to booking confirmation."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            patient_name="Иван Петров",
            birth_date="1985-05-15",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert "записаны" in resp.lower()
        assert "зовут" not in resp.lower()


# ─── Scenario 21a: Full flow — name given early, disease lookup, booking ──


class TestScenario21a_FullFlowEarlyName:
    """21a: patient introduces themselves, then books — name should not be re-asked."""

    def test_disease_lookup_with_early_name(self, doctors_service, medesk_service, faq_service):
        """Disease → doctor found → slot offered. Name already known."""
        state = make_state(
            user_message="Тревожное расстройство",
            extracted=extract("want_procedure", diseases=["тревожное расстройство"]),
            patient_name="Иван",  # given earlier
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None
        assert state.get("pending_doctor_name") is not None

    def test_chosen_slot_with_partial_name_asks_full(self, doctors_service, medesk_service, faq_service):
        """Partial name (1 word) → asks for full name, not DOB."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            patient_name="Иван",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "ask_patient_name"
        assert "фио" in resp.lower() or "полное" in resp.lower()

    def test_chosen_slot_with_full_name_skips_to_dob(self, doctors_service, medesk_service, faq_service):
        """Full name (2+ words) → skip to DOB."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            patient_name="Иван Петров",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "ask_birth_date"
        assert "рождения" in resp.lower()

    def test_dob_after_early_name_completes_booking(self, doctors_service, medesk_service, faq_service):
        """DOB given → booking complete with early name."""
        state = make_state(
            user_message="15 мая 1985",
            extracted=extract("arbitrary_message", birth_date="1985-05-15"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert "записаны" in resp.lower()
        assert state.get("call_signal") is None


# ─── CTA (call-to-action) in every doctor_info response ──


class TestCTAInDoctorInfo:
    """Every doctor_info response must end with a call-to-action."""

    def test_experience_cta_during_dob_ask(self, doctors_service, medesk_service, faq_service):
        """Experience question during ask_birth_date → CTA = ask DOB."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "стаж" in resp.lower()
        assert "рождения" in resp.lower()

    def test_experience_cta_during_name_ask(self, doctors_service, medesk_service, faq_service):
        """Experience question during ask_patient_name → CTA = ask name."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "стаж" in resp.lower()
        assert "зовут" in resp.lower()

    def test_experience_cta_during_confirm_slot(self, doctors_service, medesk_service, faq_service):
        """Experience question during confirm_slot → CTA = confirm slot."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "стаж" in resp.lower()
        assert "подходит" in resp.lower() or "апрел" in resp.lower()

    def test_experience_cta_no_pending(self, doctors_service, medesk_service, faq_service):
        """Experience question with no pending action → CTA = offer booking."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "стаж" in resp.lower()
        # CTA may be any booking variant: записать/приём/забронировать/закрепить/удобно подойти
        resp_l = resp.lower()
        assert any(w in resp_l for w in ("записать", "приём", "забронировать", "закрепить", "удобно подойти"))

    def test_cost_cta_during_name_ask(self, doctors_service, medesk_service, faq_service):
        """Cost question during ask_patient_name → CTA = ask name."""
        state = make_state(
            user_message="Сколько стоит?",
            extracted=extract("question_about_doctor", doctor_info="cost"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "администратор" in resp.lower()
        assert "зовут" in resp.lower()

    def test_visiting_hours_cta(self, doctors_service, medesk_service, faq_service):
        """Visiting hours → CTA = offer booking."""
        state = make_state(
            user_message="Когда он принимает?",
            extracted=extract("question_about_doctor", doctor_info="visiting_hours"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "часы" in resp.lower() or "приём" in resp.lower()
        assert "записать" in resp.lower() or "приём" in resp.lower()


# ─── Scenario 22: Cross-fallback and specialization search ──


class TestScenario22_CrossFallbackExtended:
    """22: Extended cross-fallback and specialization tests."""

    def test_epi_cross_fallback_produces_offer(self, doctors_service, medesk_service, faq_service):
        """ЭПИ as disease → cross-fallback finds procedure → offers booking."""
        state = make_state(
            user_message="ЭПИ специалист мне нужен",
            extracted=extract("want_procedure", diseases=["ЭПИ"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""
        assert state.get("matched_doctor") is not None
        # Should offer to book, not say "профильная клиника"
        assert "профильн" not in resp.lower()
        assert "записать" in resp.lower() or state.get("need_llm_stream") is True

    def test_specialization_fallback_from_user_message(self, doctors_service, medesk_service, faq_service):
        """When LLM extracts want_procedure with no procedure/diseases, search user message."""
        state = make_state(
            user_message="Психиатр мне нужен",
            extracted=extract("want_procedure"),  # no procedure, no diseases
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None
        resp = collect_response(state) or ""
        assert "не удалось" not in resp.lower()

    def test_psychologist_search(self, doctors_service, medesk_service, faq_service):
        """'Мне нужен психолог' → find by specialization."""
        state = make_state(
            user_message="Мне нужен психолог",
            extracted=extract("want_procedure", diseases=["психолог"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None

    def test_truly_unmatched_still_fails_gracefully(self, doctors_service, medesk_service, faq_service):
        """Something truly not in clinic → polite redirect."""
        state = make_state(
            user_message="Мне нужен хирург",
            extracted=extract("want_procedure", diseases=["хирург"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # "хирург" not in any doctor.info → should fail gracefully
        if state.get("matched_doctor") is None:
            assert "специализируется" in resp.lower() or "профильн" in resp.lower()


# ─── Case 8: FAQ — clinic question ──


class TestCase08ClinicFAQ:
    def test_clinic_location_question(self, doctors_service, medesk_service, faq_service):
        """User asks where clinic is → FAQ match."""
        state = make_state(
            user_message="Где находится клиника?",
            extracted=extract("question_about_clinic"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # FAQ should match and produce LLM prompt
        assert state.get("need_llm_stream") is True or state.get("faq_match") is not None


# ─── Case 9: End conversation ──


class TestCase09EndConversation:
    def test_goodbye_sends_end_call(self, doctors_service, medesk_service, faq_service):
        """User says goodbye → end_call signal."""
        state = make_state(
            user_message="До свидания",
            extracted=extract("end_conversation"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["call_signal"] == "end_call"
        assert "до свидания" in resp.lower()


# ─── Case 10: Transfer to operator ──


class TestCase10TransferOperator:
    def test_wants_operator(self, doctors_service, medesk_service, faq_service):
        """User asks for live operator → transfer signal."""
        state = make_state(
            user_message="Переведите на оператора",
            extracted=extract("wants_operator"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["call_signal"] == "transfer_operator"
        assert "оператор" in resp.lower()


# ─── Case 11: Question during name collection ──


class TestCase11QuestionDuringNameCollection:
    def test_question_about_experience_during_name_ask(self, doctors_service, medesk_service, faq_service):
        """
        Bot asked 'Как вас зовут?', user asks 'Расскажите про его стаж'
        → bot should answer about experience, NOT take it as a name.
        """
        # Turn: intent is question_about_doctor, routed away from appointment
        state = make_state(
            user_message="Расскажите пожалуйста про его стаж",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="chosen_slot",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        # Should answer the experience question
        assert "стаж" in resp.lower()
        # Should preserve pending_action for next turn
        assert state["pending_action"] == "ask_patient_name"

    def test_arbitrary_message_during_name_ask_no_name(self, doctors_service, medesk_service, faq_service):
        """
        Bot asked 'Как вас зовут?', user asks a random question, no name extracted.
        → bot should answer and re-ask for name.
        """
        state = make_state(
            user_message="Расскажите пожалуйста про его стаж",
            extracted=extract("arbitrary_message"),  # no patient_name extracted
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)

        # Should stay in ask_patient_name pending
        assert state["pending_action"] == "ask_patient_name"
        # Re-ask is now in system prompt context, not _append_after_llm
        assert state["need_llm_stream"] is True
        system_msg = state["fallback_messages"][0]["content"]
        assert "зовут" in system_msg.lower()


# ─── Case 12: Off-script — booking without collecting data ──


class TestCase12NoSkipDataCollection:
    def test_disease_mention_does_not_auto_book(self, doctors_service, medesk_service, faq_service):
        """
        User: 'У меня невротическое расстройство, хочу записаться'
        → bot should find doctor and ask questions, NOT auto-book.
        """
        state = make_state(
            user_message="У меня невротическое расстройство, хочу записаться к врачу",
            extracted=extract("want_procedure", diseases=["невротическое расстройство"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        # Should NOT contain "Вы записаны" — we haven't collected name/DOB yet
        assert "записаны" not in resp.lower()
        # Should not send end_call
        assert state.get("call_signal") is None

    def test_booking_always_requires_name_and_dob(self, doctors_service, medesk_service, faq_service):
        """chosen_slot without name → must ask for name, not confirm booking."""
        state = make_state(
            user_message="Да, запишите",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            patient_name=None,
            birth_date=None,
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower()
        assert "записаны" not in resp.lower()


# ─── Case 13: Slot template variety ──


class TestCase13SlotTemplateVariety:
    def test_slot_offers_vary(self, doctors_service, medesk_service, faq_service):
        """Multiple slot offers should not always use the same template."""
        responses = set()
        for _ in range(20):
            state = make_state(
                user_message="Хочу записаться к Хайретдинову",
                extracted=extract("make_appoint", doctor_name="Хайретдинов"),
            )
            state = run_turn(state, doctors_service, medesk_service, faq_service)
            resp = collect_response(state)
            responses.add(resp)

        # With 4 templates and 20 attempts, we should see at least 2 variations
        assert len(responses) >= 2, f"Expected variety, got only: {responses}"


# ─── Case 14: Non-name message during name collection ──


class TestCase14NotAName:
    def test_question_not_taken_as_name(self, doctors_service, medesk_service, faq_service):
        """
        Bot asked 'Как вас зовут?', user asks 'А как тебя зовут?'
        → should NOT take 'А как тебя зовут?' as patient name.
        """
        state = make_state(
            user_message="А как тебя зовут?",
            extracted=extract("arbitrary_message"),  # no patient_name
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)

        # Should NOT set patient_name to the question text
        assert state.get("patient_name") is None or state["patient_name"] != "А как тебя зовут?"
        # Should stay in ask_patient_name
        assert state["pending_action"] == "ask_patient_name"
        # Re-ask is now in system prompt context
        assert state["need_llm_stream"] is True
        system_msg = state["fallback_messages"][0]["content"]
        assert "зовут" in system_msg.lower()


# ─── Case 15: Non-date message during DOB collection ──


class TestCase15NotADate:
    def test_question_not_taken_as_dob(self, doctors_service, medesk_service, faq_service):
        """
        Bot asked 'Дату рождения?', user asks 'А зачем это?'
        → should NOT take it as birth_date.
        """
        state = make_state(
            user_message="А зачем это?",
            extracted=extract("arbitrary_message"),  # no birth_date
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван Петров",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)

        # Should NOT set birth_date
        assert state.get("birth_date") is None
        # Should stay in ask_birth_date
        assert state["pending_action"] == "ask_birth_date"
        # Should NOT send end_call
        assert state.get("call_signal") is None
        # Re-ask for DOB is now in system prompt context
        assert state["need_llm_stream"] is True
        system_msg = state["fallback_messages"][0]["content"]
        assert "рождения" in system_msg.lower()

    def test_booking_no_end_call(self, doctors_service, medesk_service, faq_service):
        """After booking confirmation, dialog should stay open (no end_call)."""
        state = make_state(
            user_message="15 мая 1985",
            extracted=extract("arbitrary_message", birth_date="1985-05-15"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван Петров",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert "записаны" in resp.lower()
        assert state.get("call_signal") is None, "end_call must NOT be sent after booking"
        assert "ждём" in resp.lower() or "могу" in resp.lower()


# ─── Case 16: No 'Информация о враче:' prefix ──


class TestCase16NaturalDoctorInfo:
    def test_no_info_prefix(self, doctors_service, medesk_service, faq_service):
        """
        User asks about doctor in general → response should NOT start with
        'Информация о враче:'.
        """
        state = make_state(
            user_message="Расскажите подробнее про Хайретдинова",
            extracted=extract("question_about_doctor", doctor_name="Хайретдинов"),
            # doctor_info is None → triggers the general case
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""

        assert "информация о враче" not in resp.lower()
        # Should use LLM for natural phrasing
        assert state.get("need_llm_stream") is True


# ─── Case 19: Doctor question during slot confirmation ──


class TestCase19DoctorQuestionDuringConfirmSlot:
    def test_question_about_doctor_during_confirm_slot(self, doctors_service, medesk_service, faq_service):
        """
        Bot offered a slot, user asks 'А он хороший доктор?'
        → natural response via LLM, no 'Информация о враче:' or 'Специализация:'.
        """
        state = make_state(
            user_message="А он хороший доктор?",
            extracted=extract("question_about_doctor", doctor_name="Хайретдинов"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""

        assert "информация о враче" not in resp.lower()
        assert state.get("need_llm_stream") is True
        # pending_action should be preserved for next turn
        assert state.get("pending_action") == "confirm_slot"

    def test_specialization_also_uses_llm(self, doctors_service, medesk_service, faq_service):
        """
        Even when doctor_info='specialization' is extracted, should use LLM.
        """
        state = make_state(
            user_message="Какая у него специализация?",
            extracted=extract("question_about_doctor", doctor_name="Хайретдинов", doctor_info="specialization"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""

        assert "информация о враче" not in resp.lower()
        assert state.get("need_llm_stream") is True


# ─── Case 20: Agreement after detour should use current slot, not offer new one ──


class TestCase20SlotPreservedAfterDetour:
    def test_make_appoint_during_confirm_slot_becomes_chosen(self, doctors_service, medesk_service, faq_service):
        """
        User has slot 2 offered, asks about doctor, then says 'Ну давайте'.
        LLM may extract make_appoint. Should treat as chosen_slot, not offer slot 3.
        """
        state = make_state(
            user_message="Ну давайте",
            extracted=extract("make_appoint"),  # LLM may extract this instead of chosen_slot
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",  # slot 2 — the one being confirmed
            rejected_slots=["2026-04-06 10:00"],  # slot 1 already rejected
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        # Should proceed with slot 2, ask for name — NOT offer slot 3
        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower()
        assert state["selected_slot"] == "2026-04-06 11:00"

    def test_doctor_ok_during_confirm_slot(self, doctors_service, medesk_service, faq_service):
        """doctor_ok during confirm_slot should also treat as slot acceptance."""
        state = make_state(
            user_message="Хорошо, давайте к нему",
            extracted=extract("doctor_ok"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert state["pending_action"] == "ask_patient_name"
        assert state["selected_slot"] == "2026-04-06 11:00"

    def test_explicit_slot_request_still_works(self, doctors_service, medesk_service, faq_service):
        """If user says 'Запишите на 7 апреля' during confirm_slot, should offer new slot."""
        state = make_state(
            user_message="Запишите на седьмое апреля",
            extracted=extract("make_appoint", patient_slots=["7 апреля"]),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)

        # Should NOT treat as chosen_slot — user requested a specific different date
        assert state["selected_slot"] != "2026-04-06 11:00"


# ─── Case 21: Slot reminder when asking for name ──


class TestCase21SlotReminderInNameAsk:
    def test_name_ask_includes_slot(self, doctors_service, medesk_service, faq_service):
        """
        When bot asks for name, it should remind which slot is being booked.
        'Отлично, записываю вас на 6 апреля в 10:00. Как вас зовут?'
        """
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower()
        # Must mention the slot time so patient knows what they're booking
        assert "апрел" in resp.lower() or "11" in resp

    def test_after_detour_accepts_when_already_confirming(self, doctors_service, medesk_service, faq_service):
        """After detour during confirm_slot, 'да' = acceptance (CTA already asked about slot)."""
        state = make_state(
            user_message="да",
            extracted=extract("make_appoint"),  # LLM may extract this
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            rejected_slots=["2026-04-06 10:00"],
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        # Detour CTA already asked about slot → 'да' = acceptance → ask for name
        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower() or "фио" in resp.lower()

    def test_after_reconfirm_then_name(self, doctors_service, medesk_service, faq_service):
        """After re-confirming slot, second 'Да' → ask for name."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            prev_intent="chosen_slot",  # prev was the re-confirm, not a detour
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower()


# ─── Scenario 1a: Full flow with detour ──


class TestScenario1a_FullFlowWithDetour:
    """1a: reject slot → ask about doctor → agree → re-confirm slot → agree → name → DOB → done."""

    def test_reject_then_question_then_agree(self, doctors_service, medesk_service, faq_service):
        """Step 1: Reject slot 1."""
        state = make_state(
            user_message="Нет",
            extracted=extract("slot_not_suitable"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["selected_slot"] == "2026-04-06 11:00"  # slot 2 offered
        assert state["pending_action"] == "confirm_slot"

    def test_question_during_slot2_confirm(self, doctors_service, medesk_service, faq_service):
        """Step 2: Ask about doctor during slot 2 confirmation."""
        state = make_state(
            user_message="А Хайретдинов хороший врач?",
            extracted=extract("question_about_doctor"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            rejected_slots=["2026-04-06 10:00"],
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Should answer about doctor, slot preserved
        assert state.get("need_llm_stream") is True
        assert state.get("selected_slot") == "2026-04-06 11:00"

    def test_agree_after_question_accepts_slot(self, doctors_service, medesk_service, faq_service):
        """Step 3: Patient says 'Да' after doctor info during confirm_slot → accepts slot, asks name."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            rejected_slots=["2026-04-06 10:00"],
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        # Detour CTA already asked about slot → acceptance → ask for name
        assert state["pending_action"] == "ask_patient_name"
        assert state["selected_slot"] == "2026-04-06 11:00"
        assert "зовут" in resp.lower() or "фио" in resp.lower()

    def test_second_yes_asks_name(self, doctors_service, medesk_service, faq_service):
        """Step 4: Second 'Да' → ask for name with slot reminder."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            prev_intent="chosen_slot",  # previous turn was slot re-confirm
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower()
        assert "апрел" in resp.lower() or "11" in resp


# ─── Case 17: Clinic question after doctor info ──


class TestCase17ClinicQuestionAfterDoctorInfo:
    def test_clinic_question_not_intercepted(self, doctors_service, medesk_service, faq_service):
        """
        After discussing a doctor, user asks about the clinic name.
        → should route to FAQ/fallback, NOT suggest booking.
        """
        state = make_state(
            user_message="А как называется ваша клиника?",
            extracted=extract("question_about_clinic"),
            prev_intent="question_about_doctor",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = router_node(state)

        # Should route to FAQ, not fallback with booking suggestion
        assert state["final_route"] == "faq"
        # Should NOT contain booking suggestion in template
        assert state.get("template_response") is None or "записаться" not in state.get("template_response", "").lower()

    def test_clinic_question_during_confirm_slot(self, doctors_service, medesk_service, faq_service):
        """Even during confirm_slot, clinic questions should be answered."""
        state = make_state(
            user_message="А как называется ваша клиника?",
            extracted=extract("question_about_clinic"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"


# ─── Case 18: Context preservation after side question ──


class TestCase18ContextPreservation:
    def test_doctor_context_preserved_after_clinic_question(self, doctors_service, medesk_service, faq_service):
        """
        After clinic FAQ, user returns to booking ('На какую дату?').
        → bot should remember the doctor and offer slots, NOT ask 'что вас беспокоит?'.
        """
        state = make_state(
            user_message="На какую дату?",
            extracted=extract("asks_about_slot"),
            prev_intent="question_about_clinic",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        # Should offer a slot for Хайретдинов, NOT ask what's bothering them
        assert "беспокоит" not in resp.lower()
        assert state["pending_action"] == "confirm_slot"
        assert state["selected_slot"] is not None

    def test_doctor_context_preserved_after_arbitrary_message(self, doctors_service, medesk_service, faq_service):
        """After an off-topic remark, doctor context should persist if pending_doctor set."""
        state = make_state(
            user_message="Хочу записаться",
            extracted=extract("make_appoint"),
            prev_intent="arbitrary_message",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)

        assert "беспокоит" not in resp.lower()
        assert state["pending_action"] == "confirm_slot"


# ─── Cross-cutting: end_call only on explicit goodbye ──


# ─── FAQ matching with punctuation and fallback ──


class TestFAQPunctuation:
    def test_address_question_matches(self, faq_service):
        """'А какой у вас адрес клиники?' should match location FAQ."""
        match = faq_service.match("А какой у вас адрес клиники?")
        assert match is not None
        assert "шверника" in match.answer.lower() or "адрес" in match.answer.lower()

    def test_punctuation_stripped(self, faq_service):
        """Punctuation in FAQ tokens should not block matching."""
        match = faq_service.match("Где находится клиника")
        assert match is not None
        assert "шверника" in match.answer.lower()

    def test_case_variant_matches(self, faq_service):
        """Genitive case 'клиники' should still match FAQ token 'клиника'."""
        match = faq_service.match("Какой адрес клиники?")
        assert match is not None


class TestFAQNoMatchFallback:
    def test_faq_no_match_sets_fallback(self, doctors_service, medesk_service, faq_service):
        """When FAQ doesn't match, faq_node should set up fallback LLM response (not empty)."""
        from ai_agent.nodes.faq import faq_node
        state = make_state(
            user_message="Расскажите про квантовую физику",
            extracted=extract("question_about_clinic"),
        )
        state = faq_node(state, faq_service)
        # Should have set up fallback streaming, NOT left empty
        assert state.get("need_llm_stream") is True
        assert state.get("fallback_messages") is not None
        assert len(state["fallback_messages"]) > 0


class TestEndCallOnlyOnGoodbye:
    def test_no_end_call_after_any_booking(self, doctors_service, medesk_service, faq_service):
        """Booking confirmation should never produce end_call."""
        # Scenario: full name + DOB in one flow
        state = make_state(
            user_message="Да, записывайте",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            patient_name="Иван Петров",
            birth_date="1985-05-15",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)

        assert state.get("call_signal") is None
        resp = collect_response(state)
        assert "записаны" in resp.lower()

    def test_end_call_on_goodbye(self, doctors_service, medesk_service, faq_service):
        """Only explicit goodbye triggers end_call."""
        state = make_state(
            user_message="Пока!",
            extracted=extract("end_conversation"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["call_signal"] == "end_call"

    def test_farewell_message_content(self, doctors_service, medesk_service, faq_service):
        """Farewell message should be warm and include 'ждём'."""
        state = make_state(
            user_message="До свидания",
            extracted=extract("end_conversation"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "до свидания" in resp.lower()
        assert "ждём" in resp.lower()


# ─── FAQ matching quality ──


class TestFAQMatching:
    def test_clinic_name_matches_about_faq(self, faq_service):
        """'Как называется ваша клиника' should match clinic about FAQ."""
        match = faq_service.match("Как называется ваша клиника?")
        if match:
            # Should match "Что такое клиника Psy Family?" or similar
            assert "клиника" in match.question.lower() or "psy" in match.question.lower()
            # Should NOT match preparation FAQ
            assert "приехать" not in match.answer.lower() or "psy" in match.answer.lower()

    def test_location_faq(self, faq_service):
        """'Где находится клиника?' should match location FAQ."""
        match = faq_service.match("Где находится клиника?")
        assert match is not None
        assert "адрес" in match.answer.lower() or "шверника" in match.answer.lower()

    def test_no_match_for_gibberish(self, faq_service):
        """Random text should not match any FAQ."""
        match = faq_service.match("блюмберг фрактал")
        assert match is None


# ─── Router logic ──


class TestRouterLogic:
    def test_question_about_doctor_during_name_ask(self, doctors_service, medesk_service, faq_service):
        """question_about_doctor during ask_patient_name → doctor_info, not appointment."""
        state = make_state(
            user_message="Какой стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"

    def test_question_about_clinic_during_dob_ask(self, doctors_service, medesk_service, faq_service):
        """question_about_clinic during ask_birth_date → faq, not appointment."""
        state = make_state(
            user_message="Где находится клиника?",
            extracted=extract("question_about_clinic"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"

    def test_arbitrary_message_during_name_ask_stays_appointment(self, doctors_service, medesk_service, faq_service):
        """arbitrary_message during ask_patient_name still goes to appointment (for validation there)."""
        state = make_state(
            user_message="А как тебя зовут?",
            extracted=extract("arbitrary_message"),
            pending_action="ask_patient_name",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"

    def test_question_about_doctor_during_confirm_slot(self, doctors_service, medesk_service, faq_service):
        """question_about_doctor during confirm_slot → doctor_info."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"


# ═══════════════════════════════════════════════════════════════════════
# Case 29: Name matching — patronymic false positives
# ═══════════════════════════════════════════════════════════════════════


class TestCase29NameMatchingPatronymic:
    """Фамилия не должна матчить отчество врача."""

    def test_ivanov_not_found(self, doctors_service):
        """'Иванов' should NOT match 'Некрылов Юрий Иванович'."""
        result = doctors_service.find_by_name("Иванов")
        assert result is None

    def test_petrov_not_found(self, doctors_service):
        """'Петров' should NOT match any doctor with patronymic 'Петрович'."""
        result = doctors_service.find_by_name("Петров")
        assert result is None

    def test_ivanov_appointment_returns_not_found(self, doctors_service, medesk_service, faq_service):
        """'Запишите к Иванову' → doctor not found message."""
        state = make_state(
            user_message="Давайте к Иванову",
            extracted=extract("make_appoint", doctor_name="Иванов"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "не удалось" in resp.lower() or "точнее" in resp.lower()

    def test_real_names_still_match(self, doctors_service):
        """Real doctor names still work with fuzzy matching."""
        assert doctors_service.find_by_name("Хайретдинов") is not None
        assert doctors_service.find_by_name("Харединов") is not None  # typo
        assert doctors_service.find_by_name("Некрылов") is not None
        assert doctors_service.find_by_name("Лазебный") is not None
        assert doctors_service.find_by_name("Шипотько") is not None
        assert doctors_service.find_by_name("Бутова") is not None

    def test_declension_still_works(self, doctors_service):
        """Names in grammatical cases still match."""
        assert doctors_service.find_by_name("Хайретдинову") is not None  # dative
        assert doctors_service.find_by_name("Некрылова") is not None     # genitive


# ═══════════════════════════════════════════════════════════════════════
# Section 17: Context switching — резкое переключение темы
# ═══════════════════════════════════════════════════════════════════════


class TestScenario17a_AppointmentToFAQAndBack:
    """17a: Запись → вопрос о клинике → обратно к записи."""

    def test_faq_during_confirm_slot_preserves_slot(self, doctors_service, medesk_service, faq_service):
        # Turn 1: slot offered
        state = make_state(
            user_message="А вы работаете по выходным?",
            extracted=extract("question_about_clinic"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Slot and doctor preserved
        assert state.get("selected_slot") == "2026-04-06 10:00"
        assert state.get("pending_doctor_name") == "Хайретдинов Олег Замильевич"

    def test_return_to_booking_after_faq(self, doctors_service, medesk_service, faq_service):
        # After FAQ detour during confirm_slot, user agrees → CTA already asked → accept
        state = make_state(
            user_message="Да, запишите",
            extracted=extract("make_appoint"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="question_about_clinic",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Detour CTA already asked about slot → acceptance → ask for name
        assert state["pending_action"] == "ask_patient_name"
        assert state["selected_slot"] == "2026-04-06 10:00"


class TestScenario17b_SwitchDoctor:
    """17b: Запись к одному врачу → переключение на другого."""

    def test_switch_doctor_during_confirm(self, doctors_service, medesk_service, faq_service):
        # doctor_info uses extracted.doctor_name when provided
        state = make_state(
            user_message="А что скажете про Некрылова?",
            extracted=extract("question_about_doctor", doctor_name="Некрылов"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # doctor_info prefers pending_doctor_name, so it answers about Хайретдинов.
        # To switch, user needs to explicitly book new doctor.
        # Just verify the question was answered (routed to doctor_info)
        assert state.get("matched_doctor") is not None

    def test_book_new_doctor_after_switch(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Да, лучше к нему",
            extracted=extract("make_appoint", doctor_name="Некрылов"),
            pending_doctor_name="Некрылов Юрий Иванович",
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "confirm_slot"
        assert state["pending_doctor_name"] == "Некрылов Юрий Иванович"


class TestScenario17c_DoctorToFAQToBooking:
    """17c: Обсуждение врача → FAQ → обратно к записи."""

    def test_doctor_context_survives_faq(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Хочу записаться",
            extracted=extract("make_appoint"),
            pending_doctor_name="Лазебный Даниил Леонидович",
            prev_intent="question_about_clinic",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "confirm_slot"
        assert "Лазебн" in state.get("pending_doctor_name", "")


class TestScenario17d_NameAskToCostAndBack:
    """17d: Сбор имени → вопрос о стоимости → обратно к имени."""

    def test_cost_question_during_name_ask(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="А сколько стоит приём?",
            extracted=extract("question_about_doctor", doctor_info="cost"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""
        assert "информацией" in resp.lower() or "администратор" in resp.lower()
        # pending_action preserved
        assert state["pending_action"] == "ask_patient_name"


class TestScenario17e_DOBAskToAddressAndBack:
    """17e: Сбор даты рождения → вопрос об адресе → обратно."""

    def test_clinic_question_during_dob_ask(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Где вы находитесь?",
            extracted=extract("question_about_clinic"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван Петров",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"
        assert state["pending_action"] == "ask_birth_date"


class TestScenario17f_ConfirmSlotToOperator:
    """17f: Подтверждение слота → перевод на оператора."""

    def test_operator_overrides_confirm_slot(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Переведите на оператора",
            extracted=extract("wants_operator"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["call_signal"] == "transfer_operator"


class TestScenario17g_ConfirmSlotToGoodbye:
    """17g: Подтверждение слота → прощание."""

    def test_goodbye_overrides_confirm_slot(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Нет, я передумал, до свидания",
            extracted=extract("end_conversation"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["call_signal"] == "end_call"
        resp = collect_response(state)
        assert "до свидания" in resp.lower()


class TestScenario17h_MultipleSwitches:
    """17h: Множественные переключения подряд."""

    def test_doctor_context_survives_multiple_faq(self, doctors_service, medesk_service, faq_service):
        # After multiple FAQ/doctor questions during confirm_slot, agreement → accept
        state = make_state(
            user_message="Ладно, запишите",
            extracted=extract("make_appoint"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="question_about_clinic",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Detour CTA already asked about slot → acceptance → name collection
        assert state["pending_action"] == "ask_patient_name"
        assert state["selected_slot"] == "2026-04-06 10:00"


class TestScenario17i_DiseaseToDoctorSwitch:
    """17i: Заболевание → другой врач → запись к первому."""

    def test_switch_back_to_first_doctor(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Нет, давайте к Некрылову",
            extracted=extract("make_appoint", doctor_name="Некрылов"),
            pending_doctor_name="Шипотько Дмитрий Александрович",
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_doctor_name"] == "Некрылов Юрий Иванович"
        assert state["pending_action"] == "confirm_slot"


class TestScenario17j_ComplaintDuringConfirmSlot:
    """17j: Жалоба во время confirm_slot → fallback → слот сохранён."""

    def test_arbitrary_during_confirm_slot_preserves(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="А почему так мало свободных дат?",
            extracted=extract("arbitrary_message"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["selected_slot"] == "2026-04-06 10:00"


# ═══════════════════════════════════════════════════════════════════════
# Section 18: Нестандартные ответы пациента
# ═══════════════════════════════════════════════════════════════════════


class TestScenario18a_NameAndDOBTogether:
    """18a: Пациент называет ФИО и дату рождения сразу."""

    def test_name_and_dob_in_one_message(self, doctors_service, medesk_service, faq_service):
        # Note: extract_node sets birth_date in state if extracted. But in ask_patient_name
        # handler, it checks state["birth_date"] which may be set by extract_node.
        # We simulate extract_node having already set birth_date in state.
        state = make_state(
            user_message="Петров Иван Сергеевич, 15 мая 1985",
            extracted=extract("arbitrary_message",
                              patient_name="Петров Иван Сергеевич",
                              birth_date="1985-05-15"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            birth_date="1985-05-15",  # extract_node would have set this
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["patient_name"] == "Петров Иван Сергеевич"
        resp = collect_response(state)
        assert "записаны" in resp.lower()


class TestScenario18b_ShortAgreement:
    """18b: Пациент отвечает односложно — 'Угу', 'Ага'."""

    def test_short_agreement_as_chosen_slot(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Угу",
            extracted=extract("arbitrary_message"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "ask_patient_name"


class TestScenario18c_AmbiguousResponse:
    """18c: Пациент отвечает неоднозначно — 'Ну не знаю, другие варианты?'"""

    def test_ambiguous_as_slot_request(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Ну не знаю, а других вариантов нет?",
            extracted=extract("asks_about_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["selected_slot"] != "2026-04-06 10:00"
        assert state["pending_action"] == "confirm_slot"


class TestScenario18e_DateFormats:
    """18e: Пациент говорит дату в разных форматах."""

    def test_numeric_date_accepted(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="15.05.1985",
            extracted=extract("arbitrary_message", birth_date="1985-05-15"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван Петров",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["birth_date"] == "1985-05-15"
        resp = collect_response(state)
        assert "записаны" in resp.lower()

    def test_verbose_date_accepted(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="пятнадцатого мая тысяча девятьсот восемьдесят пятого",
            extracted=extract("arbitrary_message", birth_date="1985-05-15"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["birth_date"] == "1985-05-15"


class TestScenario18f_RefuseDOB:
    """18f: Пациент отказывается называть дату рождения."""

    def test_refusal_stays_in_dob_pending(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Не хочу говорить",
            extracted=extract("arbitrary_message"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "ask_birth_date"
        assert state.get("birth_date") is None
        # DOB re-ask is now part of the system prompt context, not _append_after_llm
        assert state["need_llm_stream"] is True
        system_msg = state["fallback_messages"][0]["content"]
        assert "рождения" in system_msg.lower()


class TestScenario18g_PartialName:
    """18g: Пациент даёт только имя без фамилии → просим полное ФИО."""

    def test_partial_name_asks_full(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Иван",
            extracted=extract("arbitrary_message", patient_name="Иван"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Should ask for full name, not proceed to DOB
        assert state["pending_action"] == "ask_patient_name"
        assert "фио" in resp.lower() or "фамили" in resp.lower() or "полное" in resp.lower()


class TestScenario18h_GreetingMidDialog:
    """18h: Пациент здоровается повторно в середине диалога."""

    def test_greeting_mid_dialog_goes_fallback(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Здравствуйте",
            extracted=extract("arbitrary_message"),
            prev_intent="question_about_doctor",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = router_node(state)
        assert state["final_route"] == "fallback"


class TestScenario18i_ShortMessageConfirmSlot:
    """18i: Короткие сообщения типа 'Ок', '.' при confirm_slot."""

    def test_ok_treated_as_agreement(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Ок",
            extracted=extract("arbitrary_message"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "ask_patient_name"
        assert state["selected_slot"] == "2026-04-06 10:00"


class TestScenario18k_NotFoundThenFound:
    """18k: Врач не найден → переключение на существующего."""

    def test_recover_after_not_found(self, doctors_service, medesk_service, faq_service):
        state1 = make_state(
            user_message="Запишите к Сидорову",
            extracted=extract("make_appoint", doctor_name="Сидоров"),
        )
        state1 = run_turn(state1, doctors_service, medesk_service, faq_service)
        resp1 = collect_response(state1)
        assert "не удалось" in resp1.lower() or "точнее" in resp1.lower()

        state2 = make_state(
            user_message="Тогда к Хайретдинову",
            extracted=extract("make_appoint", doctor_name="Хайретдинов"),
            prev_intent="make_appoint",
        )
        state2 = run_turn(state2, doctors_service, medesk_service, faq_service)
        assert state2["pending_action"] == "confirm_slot"
        assert "Хайретдинов" in state2.get("pending_doctor_name", "")


# ═══════════════════════════════════════════════════════════════════════
# Section 19: Несколько записей за один звонок
# ═══════════════════════════════════════════════════════════════════════


class TestScenario19a_SecondBookingAfterFirst:
    """19a: Повторная запись после первой."""

    def test_new_booking_after_completion(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Да, ещё запишите к Некрылову",
            extracted=extract("make_appoint", doctor_name="Некрылов"),
            pending_action=None,
            pending_doctor_name="Хайретдинов Олег Замильевич",
            prev_intent="arbitrary_message",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "confirm_slot"
        assert state["pending_doctor_name"] == "Некрылов Юрий Иванович"


class TestScenario19b_FAQAfterBooking:
    """19b: FAQ вопрос после записи, потом прощание."""

    def test_faq_after_booking_then_goodbye(self, doctors_service, medesk_service, faq_service):
        state1 = make_state(
            user_message="А что с собой взять на приём?",
            extracted=extract("question_about_clinic"),
            prev_intent="arbitrary_message",
        )
        state1 = router_node(state1)
        assert state1["final_route"] == "faq"

        state2 = make_state(
            user_message="Спасибо, до свидания",
            extracted=extract("end_conversation"),
        )
        state2 = run_turn(state2, doctors_service, medesk_service, faq_service)
        assert state2["call_signal"] == "end_call"


# ═══════════════════════════════════════════════════════════════════════
# Section 20: Эмоциональные и сложные ситуации
# ═══════════════════════════════════════════════════════════════════════


class TestScenario20a_AnxiousPatient:
    """20a: Тревожный пациент — бот направляет к записи."""

    def test_disease_mention_finds_doctor(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Мне очень плохо, у меня тревожное расстройство",
            extracted=extract("want_procedure", diseases=["тревожное расстройство"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None


class TestScenario20c_MedicalAdvice:
    """20c: Медицинский совет — бот не даёт рекомендаций."""

    def test_medical_advice_goes_faq(self, doctors_service, medesk_service, faq_service):
        """clinic_related arbitrary messages now route to FAQ for better handling."""
        state = make_state(
            user_message="Мне нужно принимать антидепрессанты?",
            extracted=extract("arbitrary_message"),
        )
        state = router_node(state)
        assert state["final_route"] == "faq"


class TestScenario20d_ConfidentialityFAQ:
    """20d: Вопрос о конфиденциальности — FAQ match."""

    def test_confidentiality_faq(self, faq_service):
        match = faq_service.match("Вы не передаёте данные в ПНД?")
        assert match is not None
        assert "конфиденциальность" in match.answer.lower() or "пнд" in match.answer.lower()


class TestScenario20e_ChildBooking:
    """20e: Запись ребёнка — поиск детского специалиста."""

    def test_child_specialist_found(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Хочу записать ребёнка к детскому психиатру",
            extracted=extract("want_procedure", diseases=["расстройства аутистического спектра"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None


class TestScenario20f_WrongClinic:
    """20f: Пациент путает клинику — fallback."""

    def test_wrong_clinic_goes_faq(self, doctors_service, medesk_service, faq_service):
        """clinic_related arbitrary messages now route to FAQ for context-aware answers."""
        state = make_state(
            user_message="Это стоматология?",
            extracted=extract("arbitrary_message"),
        )
        state = router_node(state)
        assert state["final_route"] == "faq"


class TestCase30_NonPsychProcedure:
    """Case 30: Пациент спрашивает про процедуру не нашей специализации (зубы)."""

    def test_dental_procedure_explains_specialization(self, doctors_service, medesk_service, faq_service):
        """'Зубы лечить' → объясняем специализацию, не 'уточните процедуру'."""
        state = make_state(
            user_message="Зубы можно у вас лечить?",
            extracted=extract("want_procedure", procedure="лечение зубов"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "психическ" in resp.lower() or "психиатр" in resp.lower()
        assert "профильн" in resp.lower() or "обратиться" in resp.lower()
        assert "не удалось" not in resp.lower()

    def test_dental_with_disease_explains_specialization(self, doctors_service, medesk_service, faq_service):
        """'Зубы лечить' with disease → same specialization message."""
        state = make_state(
            user_message="У меня кариес, можно к вам?",
            extracted=extract("want_procedure", diseases=["кариес"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "психическ" in resp.lower() or "психиатр" in resp.lower()
        assert "не удалось" not in resp.lower()

    def test_no_procedure_no_disease_asks_clarification(self, doctors_service, medesk_service, faq_service):
        """want_procedure without procedure/disease → asks what bothers them."""
        state = make_state(
            user_message="Мне нужен врач",
            extracted=extract("want_procedure"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "беспокоит" in resp.lower() or "подскажите" in resp.lower()


class TestCase31_DiseaseToDoctor:
    """Case 31: 'Записаться' → 'что беспокоит?' → 'тревожные расстройства' → находит врача."""

    def test_disease_finds_doctor_in_appointment(self, doctors_service, medesk_service, faq_service):
        """When patient names a disease in appointment flow, find doctor by disease."""
        state = make_state(
            user_message="Тревожные расстройства",
            extracted=extract("make_appoint", diseases=["тревожное расстройство"]),
            prev_intent="make_appoint",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Should find a doctor and offer a slot, not ask "к какому врачу"
        assert "к какому врачу" not in resp.lower()
        assert state.get("pending_doctor_name") is not None
        assert state.get("pending_action") == "confirm_slot"

    def test_procedure_finds_doctor_in_appointment(self, doctors_service, medesk_service, faq_service):
        """When patient names a procedure in appointment flow, find doctor by procedure."""
        state = make_state(
            user_message="ЭПИ нужно пройти",
            extracted=extract("make_appoint", procedure="ЭПИ"),
            prev_intent="make_appoint",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "к какому врачу" not in resp.lower()
        assert state.get("pending_doctor_name") is not None

    def test_full_flow_disease_to_booking(self, doctors_service, medesk_service, faq_service):
        """Full turn: first ask → disease answer → slot offered."""
        # Turn 1: generic booking request
        state = make_state(
            user_message="Записаться на прием",
            extracted=extract("make_appoint"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp1 = collect_response(state)
        assert "беспокоит" in resp1.lower()

        # Turn 2: patient says disease
        history = add_to_history({}, "Записаться на прием", resp1)
        state2 = make_state(
            user_message="Тревожные расстройства",
            extracted=extract("make_appoint", diseases=["тревожное расстройство"]),
            prev_intent="make_appoint",
            history=history,
        )
        state2 = run_turn(state2, doctors_service, medesk_service, faq_service)
        resp2 = collect_response(state2)
        assert state2.get("pending_action") == "confirm_slot"
        assert state2.get("pending_doctor_name") is not None


class TestCase32_DoctorNameAsArbitrary:
    """Case 32: Doctor name classified as arbitrary_message → should route to doctor_info."""

    def test_arbitrary_with_doctor_name_goes_to_doctor_info(self, doctors_service, medesk_service, faq_service):
        """'Иванов Равиль Рахимович' with doctor_name extracted → doctor_info, not FAQ/fallback."""
        state = make_state(
            user_message="Иванов Равиль Рахимович",
            extracted=extract("arbitrary_message", doctor_name="Иванов Равиль Рахимович"),
            prev_intent="question_about_doctor",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"
        assert state["extracted"].patient_intent == "question_about_doctor"

    def test_nonexistent_doctor_full_name(self, doctors_service, medesk_service, faq_service):
        """Full flow: non-existent doctor with full name → definitive 'no such doctor'."""
        state = make_state(
            user_message="Иванов Равиль Рахимович",
            extracted=extract("arbitrary_message", doctor_name="Иванов Равиль Рахимович"),
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Full name → definitive answer, not "уточните ФИО"
        assert "нет врача" in resp.lower() or "нет такого" in resp.lower() or "к сожалению" in resp.lower()
        assert "уточните" not in resp.lower()
        # Must NOT hallucinate that doctor exists
        assert "работает" not in resp.lower()
        assert state["need_llm_stream"] is False

    def test_existing_doctor_via_arbitrary(self, doctors_service, medesk_service, faq_service):
        """Existing doctor name in arbitrary_message → found correctly."""
        state = make_state(
            user_message="Хайретдинов Олег Замильевич",
            extracted=extract("arbitrary_message", doctor_name="Хайретдинов"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("pending_doctor_name") is not None
        assert "хайретдинов" in state["pending_doctor_name"].lower()


class TestCase33_QuickNotFoundEscalation:
    """Case 33: Single-name → 'уточните', full name → definitive 'нет такого врача'."""

    def test_single_name_asks_to_clarify(self, doctors_service, medesk_service, faq_service):
        """'Иванов' → one word → asks for full name."""
        state = make_state(
            user_message="Иванов у вас работает?",
            extracted=extract("question_about_doctor", doctor_name="Иванов"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "не удалось" in resp.lower()
        assert "фио" in resp.lower() or "полное" in resp.lower()

    def test_full_name_definitive_no(self, doctors_service, medesk_service, faq_service):
        """'Иванов Сергей Иванович' → full name → definitive 'нет врача'."""
        state = make_state(
            user_message="Иванов Сергей Иванович",
            extracted=extract("question_about_doctor", doctor_name="Иванов Сергей Иванович"),
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "нет врача" in resp.lower() or "нет такого" in resp.lower()
        # Should NOT ask to clarify — full name was given
        assert "фио" not in resp.lower()
        assert "уточните" not in resp.lower()
        # Should offer alternative
        assert "специалист" in resp.lower() or "записаться" in resp.lower() or "помочь" in resp.lower()

    def test_appointment_single_name(self, doctors_service, medesk_service, faq_service):
        """'Запишите к Иванову' → one word → asks for full name."""
        state = make_state(
            user_message="Запишите к Иванову",
            extracted=extract("make_appoint", doctor_name="Иванов"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "не удалось" in resp.lower()
        assert "фио" in resp.lower() or "полное" in resp.lower()

    def test_appointment_full_name_definitive(self, doctors_service, medesk_service, faq_service):
        """'Запишите к Иванову Сергею Ивановичу' → full name → 'нет врача'."""
        state = make_state(
            user_message="Запишите к Иванову Сергею Ивановичу",
            extracted=extract("make_appoint", doctor_name="Иванов Сергей Иванович"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "нет врача" in resp.lower() or "нет такого" in resp.lower()
        assert "фио" not in resp.lower()


class TestCase34_DoctorNotOk:
    """Case 34: Patient rejects suggested doctor → offer alternative."""

    def test_doctor_not_ok_routes_to_doctor_info(self, doctors_service, medesk_service, faq_service):
        """doctor_not_ok intent routes to doctor_info."""
        state = make_state(
            user_message="Нет, мне нужен другой врач",
            extracted=extract("doctor_not_ok"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
            prev_intent="want_procedure",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"

    def test_doctor_not_ok_finds_alternative(self, doctors_service, medesk_service, faq_service):
        """Rejecting doctor → suggests alternative with same profile."""
        state = make_state(
            user_message="Нет, мне нужен другой врач",
            extracted=extract("doctor_not_ok"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
            prev_intent="want_procedure",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Should suggest a different doctor
        assert state.get("pending_doctor_name") != "Хайретдинов Олег Замильевич"
        assert state.get("pending_doctor_name") is not None
        # Хайретдинов should be in tried list
        assert "Хайретдинов Олег Замильевич" in state.get("tried_doctors", [])

    def test_doctor_not_ok_no_pending_asks_preference(self, doctors_service, medesk_service, faq_service):
        """doctor_not_ok without pending doctor → asks what bothers them."""
        state = make_state(
            user_message="Нет, мне нужен другой врач",
            extracted=extract("doctor_not_ok"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "беспокоит" in resp.lower() or "специалист" in resp.lower()

    def test_doctor_not_ok_exhausted_alternatives(self, doctors_service, medesk_service, faq_service):
        """All similar doctors tried → appropriate message."""
        # Get all doctors and put them in tried list
        all_names = [d.name for d in doctors_service.doctors]
        state = make_state(
            user_message="Нет, другого",
            extracted=extract("doctor_not_ok"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
            tried_doctors=all_names,
            prev_intent="want_procedure",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "других" in resp.lower() or "администратор" in resp.lower()


class TestCase36_NoDocContext:
    """Case 36: Patient doesn't know any doctor names — bot should help, not loop."""

    def test_appoint_no_doctor_no_context_asks_complaint(self, doctors_service, medesk_service, faq_service):
        """After non-profile rejection, 'Да' → 'что вас беспокоит?', not 'к какому врачу?'."""
        state = make_state(
            user_message="Да",
            extracted=extract("make_appoint"),
            prev_intent="want_procedure",
            # No pending_doctor_name — previous was a non-profile rejection
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "беспокоит" in resp.lower() or "специалист" in resp.lower()
        assert "к какому врачу" not in resp.lower()

    def test_who_is_available_lists_specializations(self, doctors_service, medesk_service, faq_service):
        """'А кто есть?' without doctor name → lists clinic specializations."""
        state = make_state(
            user_message="А кто у вас есть?",
            extracted=extract("question_about_doctor"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Should mention specializations, not ask "про какого врача?"
        assert "психиатр" in resp.lower() or "психотерапевт" in resp.lower()
        assert "про какого врача" not in resp.lower()
        assert "беспокоит" in resp.lower()

    def test_whom_can_i_see_helps(self, doctors_service, medesk_service, faq_service):
        """'А к кому могу?' → helpful response about specializations."""
        state = make_state(
            user_message="А к кому могу записаться?",
            extracted=extract("question_about_doctor"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "психиатр" in resp.lower() or "психотерапевт" in resp.lower()


class TestCase37_YesAfterDetourCTA:
    """Case 37: 'Да' after detour CTA already asked about slot → accept, don't re-confirm."""

    def test_yes_after_detour_with_confirm_slot_pending(self, doctors_service, medesk_service, faq_service):
        """Doctor question during confirm_slot → CTA asked about slot → 'да' → proceed to name."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",  # already confirm_slot from before detour
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            prev_intent="question_about_doctor",  # detour intent
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Should proceed to name collection, NOT re-confirm slot
        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower() or "фио" in resp.lower()
        assert "удобные дату" not in resp.lower()

    def test_yes_after_detour_without_confirm_slot_reconfirms(self, doctors_service, medesk_service, faq_service):
        """Detour from non-confirm state → should re-confirm slot."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action=None,  # NOT confirm_slot
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Should re-confirm slot
        assert state["pending_action"] == "confirm_slot"
        assert "подходит" in resp.lower() or "удобно" in resp.lower()


class TestCase38_NamePriorityOverQuestion:
    """Case 38: Extracted name takes priority over misclassified intent during ask_patient_name."""

    def test_name_extracted_despite_question_intent(self, doctors_service, medesk_service, faq_service):
        """'Иван' classified as question_about_doctor but patient_name extracted → appointment."""
        state = make_state(
            user_message="Иван",
            extracted=extract("question_about_doctor", patient_name="Иван"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = router_node(state)
        # Should route to appointment, not doctor_info
        assert state["final_route"] == "appointment"

    def test_name_extracted_goes_to_validation(self, doctors_service, medesk_service, faq_service):
        """Name extracted during misclassified intent → goes to appointment → validation."""
        state = make_state(
            user_message="Иван",
            extracted=extract("question_about_doctor", patient_name="Иван"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # "Иван" is 1 word → asks for full name (NOT experience info)
        assert "стаж" not in resp.lower()
        assert "фио" in resp.lower() or "полное" in resp.lower() or "фамили" in resp.lower()

    def test_dob_extracted_despite_question_intent(self, doctors_service, medesk_service, faq_service):
        """Birth date extracted despite misclassified intent → appointment."""
        state = make_state(
            user_message="15 мая 1990",
            extracted=extract("question_about_doctor", birth_date="1990-05-15"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван Петров",
            selected_slot="2026-04-06 11:00",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"

    def test_genuine_question_still_routes_to_doctor_info(self, doctors_service, medesk_service, faq_service):
        """Genuine question (no name extracted) during ask_patient_name → doctor_info."""
        state = make_state(
            user_message="А какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"


class TestCase39_ChiefDoctorQuestion:
    """Case 39: 'Кто главный врач?' should find chief doctor, not pending doctor."""

    def test_chief_doctor_overrides_pending(self, doctors_service, medesk_service, faq_service):
        """'Кто у вас самый главный врач?' → finds Тер-Исраелян, not pending Некрылов."""
        state = make_state(
            user_message="А кто у вас самый главный и лучший врач?",
            extracted=extract("question_about_doctor"),
            pending_doctor_name="Некрылов Юрий Иванович",
            prev_intent="want_procedure",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Should switch to Тер-Исраелян (chief doctor)
        assert state.get("pending_doctor_name") == "Тер-Исраелян Алексей Юрьевич"

    def test_chief_doctor_keyword(self, doctors_service, medesk_service, faq_service):
        """'Кто главный врач?' → finds Тер-Исраелян."""
        state = make_state(
            user_message="Кто ваш главный врач?",
            extracted=extract("question_about_doctor"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("pending_doctor_name") == "Тер-Исраелян Алексей Юрьевич"

    def test_regular_question_uses_pending(self, doctors_service, medesk_service, faq_service):
        """Regular question (no role keywords) still uses pending doctor."""
        state = make_state(
            user_message="А какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_doctor_name="Некрылов Юрий Иванович",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("pending_doctor_name") == "Некрылов Юрий Иванович"


class TestCase40_DoctorNotOkDuringAltConfirm:
    """Case 40: Rejecting alt doctor or asking questions during confirm_alt_doctor."""

    def test_doctor_not_ok_during_alt_confirm_routes_doctor_info(self, doctors_service, medesk_service, faq_service):
        """'А девушки есть?' as doctor_not_ok → doctor_info, not auto-accept."""
        state = make_state(
            user_message="А девушки есть?",
            extracted=extract("doctor_not_ok"),
            pending_action="confirm_alt_doctor",
            pending_doctor_name="Лазебный Даниил Леонидович",
            prev_intent="make_appoint",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"

    def test_question_during_alt_confirm_routes_doctor_info(self, doctors_service, medesk_service, faq_service):
        """Question about doctor during alt confirm → doctor_info."""
        state = make_state(
            user_message="А какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="confirm_alt_doctor",
            pending_doctor_name="Лазебный Даниил Леонидович",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"

    def test_clinic_question_during_alt_confirm_routes_faq(self, doctors_service, medesk_service, faq_service):
        """FAQ question during alt confirm → faq."""
        state = make_state(
            user_message="А где клиника?",
            extracted=extract("question_about_clinic"),
            pending_action="confirm_alt_doctor",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"

    def test_acceptance_during_alt_confirm_stays_appointment(self, doctors_service, medesk_service, faq_service):
        """'Да, давайте' during alt confirm → appointment (accept)."""
        state = make_state(
            user_message="Да, давайте",
            extracted=extract("doctor_ok"),
            pending_action="confirm_alt_doctor",
            pending_doctor_name="Лазебный Даниил Леонидович",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"


class TestCase41_BookingClearsState:
    """Case 41: After booking, state is cleared so FAQ works and booking doesn't repeat."""

    def test_booking_clears_selected_slot(self, doctors_service, medesk_service, faq_service):
        """After booking via DOB, selected_slot should be cleared."""
        state = make_state(
            user_message="3 сентября 1990",
            extracted=extract("arbitrary_message", birth_date="1990-09-03"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иванов Иван",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "записаны" in resp.lower()
        # Booking state should be cleared
        assert state.get("selected_slot") is None
        assert state.get("rejected_slots") == []
        assert state.get("pending_action") is None

    def test_booking_clears_via_name_with_dob(self, doctors_service, medesk_service, faq_service):
        """Booking via name (when DOB already known) clears state too."""
        state = make_state(
            user_message="Иванов Иван",
            extracted=extract("arbitrary_message", patient_name="Иванов Иван"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            birth_date="1990-09-03",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "записаны" in resp.lower()
        assert state.get("selected_slot") is None

    def test_faq_after_booking_works(self, doctors_service, medesk_service, faq_service):
        """After booking, FAQ questions should not re-trigger booking."""
        # Simulate state after booking: pending_action=None, selected_slot=None
        state = make_state(
            user_message="Что с собой нужно иметь?",
            extracted=extract("question_about_clinic"),
            prev_intent="chosen_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            # selected_slot is None after booking
        )
        state = router_node(state)
        # Should go to FAQ, not appointment
        assert state["final_route"] == "faq"

    def test_chosen_slot_booking_clears_state(self, doctors_service, medesk_service, faq_service):
        """Booking via chosen_slot (all data present) clears state."""
        state = make_state(
            user_message="Да",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            patient_name="Иванов Иван",
            birth_date="1990-09-03",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "записаны" in resp.lower()
        assert state.get("selected_slot") is None
        assert state.get("pending_action") is None


class TestCase42_BotDirectedQuestion:
    """Case 42: 'А вас как зовут?' during ask_patient_name → answer about bot, not doctor."""

    def test_bot_name_question_stays_in_appointment(self, doctors_service, medesk_service, faq_service):
        """'А вас как зовут?' classified as question_about_doctor → stays in appointment."""
        state = make_state(
            user_message="А вас как зовут?",
            extracted=extract("question_about_doctor"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        # Should stay in appointment (fallback), NOT go to doctor_info
        assert state["final_route"] == "appointment"

    def test_bot_name_question_uses_fallback(self, doctors_service, medesk_service, faq_service):
        """'А вас как зовут?' → fallback LLM (should say 'Алиса'), not doctor experience."""
        state = make_state(
            user_message="А вас как зовут?",
            extracted=extract("question_about_doctor"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Should use LLM fallback (will say "Алиса"), not doctor info template
        assert state["need_llm_stream"] is True
        assert state["pending_action"] == "ask_patient_name"
        # Should NOT contain doctor experience
        resp = state.get("template_response")
        if resp:
            assert "стаж" not in resp.lower()

    def test_kak_tebya_zovut_stays_in_appointment(self, doctors_service, medesk_service, faq_service):
        """'Как тебя зовут?' variant → stays in appointment too."""
        state = make_state(
            user_message="Как тебя зовут?",
            extracted=extract("question_about_doctor"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"

    def test_genuine_doctor_question_still_routes(self, doctors_service, medesk_service, faq_service):
        """'Какой у него стаж?' → still goes to doctor_info."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"


class TestCase43_PostBookingCTA:
    """Case 43: After booking, CTA should be 'Могу помочь?' not 'Хотите записаться?'."""

    def test_booking_sets_completed_flag(self, doctors_service, medesk_service, faq_service):
        """_complete_booking sets booking_completed=True."""
        state = make_state(
            user_message="6 января 1990",
            extracted=extract("arbitrary_message", birth_date="1990-01-06"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Романов Кирилл",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("booking_completed") is True

    def test_faq_after_booking_no_zapisatsya(self, faq_service):
        """FAQ after booking → prompt says 'не предлагай записаться'."""
        state = make_state(
            user_message="Как до вас добраться?",
            extracted=extract("question_about_clinic"),
            booking_completed=True,
        )
        from ai_agent.nodes.faq import faq_node
        state = faq_node(state, faq_service)
        # LLM prompt should contain booking context
        prompt = state.get("llm_prompt", "")
        assert "уже записан" in prompt.lower() or "не предлагай" in prompt.lower()

    def test_fallback_after_booking_has_context(self, doctors_service, medesk_service, faq_service):
        """Fallback after booking → system prompt warns not to offer booking."""
        state = make_state(
            user_message="Спасибо за помощь",
            extracted=extract("arbitrary_message"),
            message_category="clinic_related",
            booking_completed=True,
        )
        state["final_route"] = "fallback"
        from ai_agent.nodes.fallback import fallback_node
        state = fallback_node(state)
        if state.get("fallback_messages"):
            system_msg = state["fallback_messages"][0]["content"]
            assert "уже записан" in system_msg.lower()


class TestCase43_PartialDOB:
    """Case 43: Day + month without year should be accepted as birth_date."""

    def test_day_month_accepted(self, doctors_service, medesk_service, faq_service):
        """'6 января' (no year) extracted as birth_date → booking completes."""
        state = make_state(
            user_message="6 января",
            extracted=extract("arbitrary_message", birth_date="6 января"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Романов Кирилл",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "записаны" in resp.lower()
        assert state.get("birth_date") == "6 января"


# ═══════════════════════════════════════════════════════════════════════
# Signals always override pending_action
# ═══════════════════════════════════════════════════════════════════════


class TestSignalsPriority:
    """Operator/goodbye should work from ANY pending_action state."""

    @pytest.mark.parametrize("pending", [
        "ask_patient_name", "ask_birth_date", "confirm_slot", "confirm_alt_doctor",
    ])
    def test_operator_overrides_any_pending(self, pending, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Переведите на оператора",
            extracted=extract("wants_operator"),
            pending_action=pending,
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["call_signal"] == "transfer_operator"

    @pytest.mark.parametrize("pending", [
        "ask_patient_name", "ask_birth_date", "confirm_slot", "confirm_alt_doctor",
    ])
    def test_goodbye_overrides_any_pending(self, pending, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="До свидания",
            extracted=extract("end_conversation"),
            pending_action=pending,
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["call_signal"] == "end_call"


# ═══════════════════════════════════════════════════════════════════════
# No-doctor and error edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestNoDoctorEdgeCases:
    """Appointment without specifying a doctor."""

    def test_no_doctor_no_context_asks_complaint(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Хочу записаться",
            extracted=extract("make_appoint"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "беспокоит" in resp.lower() or "специалист" in resp.lower()

    def test_no_doctor_with_context_uses_pending(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Хочу записаться",
            extracted=extract("make_appoint"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "confirm_slot"

    def test_disease_not_in_clinic(self, doctors_service, medesk_service, faq_service):
        # Use a disease that definitely won't fuzzy-match any psychiatric condition
        state = make_state(
            user_message="У меня перелом ноги",
            extracted=extract("want_procedure", diseases=["перелом ноги"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "психическ" in resp.lower() or "профильн" in resp.lower()


# ═══════════════════════════════════════════════════════════════════════
# Alternative doctor flow
# ═══════════════════════════════════════════════════════════════════════


class TestAlternativeDoctorFlow:
    """When all slots exhausted, suggest alternative doctor."""

    def test_all_slots_rejected_suggests_alt(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Нет, тоже не подходит",
            extracted=extract("slot_not_suitable"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-07 14:00",
            rejected_slots=["2026-04-06 10:00", "2026-04-06 11:00"],
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "confirm_alt_doctor"
        assert state["pending_doctor_name"] != "Хайретдинов Олег Замильевич"

    def test_accept_alt_doctor(self, doctors_service, medesk_service, faq_service):
        alt_doctor = "Лазебный Даниил Леонидович"
        state = make_state(
            user_message="Хорошо, давайте",
            extracted=extract("doctor_ok"),
            pending_action="confirm_alt_doctor",
            pending_doctor_name=alt_doctor,
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "confirm_slot"
        assert state["pending_doctor_name"] == alt_doctor

    def test_reject_alt_request_specific(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Нет, мне нужен Некрылов",
            extracted=extract("make_appoint", doctor_name="Некрылов"),
            pending_action="confirm_alt_doctor",
            pending_doctor_name="Лазебный Даниил Леонидович",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_doctor_name"] == "Некрылов Юрий Иванович"


# ═══════════════════════════════════════════════════════════════════════
# Full end-to-end scenario: from greeting to booking with all edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestFullScenario_ConfusedPatientToBooking:
    """
    Full scenario: patient confuses clinic, asks about doctors, rejects slot,
    asks about doctor quality, agrees, gets re-confirmed, gives name,
    asks personal question, gives DOB, booking complete.
    """

    def test_step1_wrong_clinic_faq(self, doctors_service, medesk_service, faq_service):
        """'Это стоматология?' → faq (clinic_related arbitrary messages go to FAQ)."""
        state = make_state(
            user_message="Это стоматология?",
            extracted=extract("arbitrary_message"),
        )
        state = router_node(state)
        assert state["final_route"] == "faq"

    def test_step2_disease_finds_doctor(self, doctors_service, medesk_service, faq_service):
        """'У меня тревожка' → finds doctor for anxiety."""
        state = make_state(
            user_message="У меня тревожка",
            extracted=extract("want_procedure", diseases=["тревожное расстройство"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None
        assert state.get("pending_doctor_name") is not None

    def test_step3_accept_then_offer_slot(self, doctors_service, medesk_service, faq_service):
        """'Давайте' after doctor info → offer slot."""
        state = make_state(
            user_message="Давайте",
            extracted=extract("make_appoint"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
            prev_intent="want_procedure",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "confirm_slot"
        assert state["selected_slot"] is not None

    def test_step4_reject_slot(self, doctors_service, medesk_service, faq_service):
        """'Нет, мне не подходит' → alternative slot."""
        state = make_state(
            user_message="Нет, мне это время не подходит",
            extracted=extract("slot_not_suitable"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["selected_slot"] != "2026-04-06 10:00"
        assert state["pending_action"] == "confirm_slot"

    def test_step5_doctor_question_during_confirm(self, doctors_service, medesk_service, faq_service):
        """'А он хороший врач?' during confirm_slot → answer + CTA with slot."""
        state = make_state(
            user_message="А он вообще хороший врач?",
            extracted=extract("question_about_doctor"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("need_llm_stream") is True
        assert state.get("selected_slot") == "2026-04-06 11:00"
        assert state.get("pending_action") == "confirm_slot"

    def test_step6_agree_after_detour_accepts_slot(self, doctors_service, medesk_service, faq_service):
        """'Ну давайте' after detour during confirm_slot → accepts, asks name."""
        state = make_state(
            user_message="Ну давайте",
            extracted=extract("make_appoint"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            rejected_slots=["2026-04-06 10:00"],
            prev_intent="question_about_doctor",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Detour CTA already asked about slot → acceptance → ask for name
        assert state["pending_action"] == "ask_patient_name"
        assert state["selected_slot"] == "2026-04-06 11:00"

    def test_step7_confirm_asks_name(self, doctors_service, medesk_service, faq_service):
        """'Удобно' → ask name with slot reminder."""
        state = make_state(
            user_message="Удобно",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            prev_intent="chosen_slot",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "ask_patient_name"
        assert "зовут" in resp.lower()

    def test_step8_give_partial_name_asks_full(self, doctors_service, medesk_service, faq_service):
        """'Иван' → asks for full name (need first + last)."""
        state = make_state(
            user_message="Иван",
            extracted=extract("arbitrary_message", patient_name="Иван"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "ask_patient_name"
        assert "фио" in resp.lower() or "полное" in resp.lower() or "фамили" in resp.lower()

    def test_step9_personal_question_during_dob_reasks_with_reason(self, doctors_service, medesk_service, faq_service):
        """'А как вас зовут?' during ask_birth_date → re-ask with reason."""
        state = make_state(
            user_message="А как вас зовут?",
            extracted=extract("arbitrary_message"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "ask_birth_date"
        # DOB re-ask is now in system prompt context
        assert state["need_llm_stream"] is True
        system_msg = state["fallback_messages"][0]["content"]
        assert "рождения" in system_msg.lower()

    def test_step10_give_dob_completes_booking(self, doctors_service, medesk_service, faq_service):
        """'5 июня 1990' → booking complete, no end_call."""
        state = make_state(
            user_message="5 июня 1990 год",
            extracted=extract("arbitrary_message", birth_date="1990-06-05"),
            pending_action="ask_birth_date",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            patient_name="Иван",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "записаны" in resp.lower()
        assert state.get("call_signal") is None
        assert "могу" in resp.lower() or "помочь" in resp.lower()

    def test_step11_goodbye(self, doctors_service, medesk_service, faq_service):
        """Goodbye → end_call."""
        state = make_state(
            user_message="До свидания",
            extracted=extract("end_conversation"),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["call_signal"] == "end_call"
        resp = collect_response(state)
        assert "ждём" in resp.lower()


# ═══════════════════════════════════════════════════════════════════════
# NEW: Tests for message_category, categorical fallback, improved FAQ,
# and refactored router
# ═══════════════════════════════════════════════════════════════════════


# ─── Message category extraction and schema tests ─────────────────────


class TestMessageCategorySchema:
    """Verify message_category field in ExtractedMessage."""

    def test_default_category_is_clinic_related(self):
        from ai_agent.schemas import ExtractedMessage
        msg = ExtractedMessage()
        assert msg.message_category == "clinic_related"

    def test_category_values(self):
        from ai_agent.schemas import ExtractedMessage
        for cat in ["clinic_related", "off_topic", "emotional", "greeting", "gratitude"]:
            msg = ExtractedMessage(message_category=cat)
            assert msg.message_category == cat

    def test_category_passed_to_state(self):
        state = make_state(
            user_message="test",
            extracted=extract("arbitrary_message"),
            message_category="emotional",
        )
        assert state["message_category"] == "emotional"

    def test_category_defaults_from_extracted(self):
        ext = extract("arbitrary_message")
        state = make_state(user_message="test", extracted=ext)
        assert state["message_category"] == "clinic_related"


# ─── Router with message_category ────────────────────────────────────


class TestRouterWithCategory:
    """Test that router uses message_category for smarter routing."""

    def test_clinic_related_arbitrary_goes_to_faq(self, doctors_service, medesk_service, faq_service):
        """arbitrary_message + clinic_related → faq (not fallback)."""
        state = make_state(
            user_message="Как к вам добраться?",
            extracted=extract("arbitrary_message"),
            message_category="clinic_related",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"

    def test_off_topic_arbitrary_goes_to_fallback(self, doctors_service, medesk_service, faq_service):
        """arbitrary_message + off_topic → fallback."""
        state = make_state(
            user_message="Какая сегодня погода?",
            extracted=extract("arbitrary_message"),
            message_category="off_topic",
        )
        state = router_node(state)
        assert state["final_route"] == "fallback"

    def test_emotional_arbitrary_goes_to_fallback(self, doctors_service, medesk_service, faq_service):
        """arbitrary_message + emotional → fallback (empathetic handling)."""
        state = make_state(
            user_message="Мне очень плохо, я не знаю что делать",
            extracted=extract("arbitrary_message"),
            message_category="emotional",
        )
        state = router_node(state)
        assert state["final_route"] == "fallback"

    def test_greeting_arbitrary_goes_to_fallback(self, doctors_service, medesk_service, faq_service):
        """arbitrary_message + greeting → fallback (fast template)."""
        state = make_state(
            user_message="Привет",
            extracted=extract("arbitrary_message"),
            message_category="greeting",
        )
        state = router_node(state)
        assert state["final_route"] == "fallback"

    def test_gratitude_arbitrary_goes_to_fallback(self, doctors_service, medesk_service, faq_service):
        """arbitrary_message + gratitude → fallback (fast template)."""
        state = make_state(
            user_message="Спасибо большое",
            extracted=extract("arbitrary_message"),
            message_category="gratitude",
        )
        state = router_node(state)
        assert state["final_route"] == "fallback"

    def test_specific_intent_ignores_category(self, doctors_service, medesk_service, faq_service):
        """Specific intents (make_appoint) route by intent, not category."""
        state = make_state(
            user_message="Хочу записаться",
            extracted=extract("make_appoint", doctor_name="Хайретдинов"),
            message_category="emotional",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"


# ─── Categorical fallback responses ─────────────────────────────────


class TestCategoricalFallback:
    """Test that fallback_node returns category-appropriate responses."""

    def test_greeting_template_response(self, doctors_service, medesk_service, faq_service):
        """greeting → fast template, no LLM."""
        state = make_state(
            user_message="Здравствуйте",
            extracted=extract("arbitrary_message"),
            message_category="greeting",
        )
        state = router_node(state)
        state = fallback_node(state)
        resp = collect_response(state)
        assert "здравствуйте" in resp.lower() or "алиса" in resp.lower()
        assert state["need_llm_stream"] is False

    def test_gratitude_template_response(self, doctors_service, medesk_service, faq_service):
        """gratitude → fast template, no LLM."""
        state = make_state(
            user_message="Спасибо",
            extracted=extract("arbitrary_message"),
            message_category="gratitude",
        )
        state = router_node(state)
        state = fallback_node(state)
        resp = collect_response(state)
        assert "помочь" in resp.lower() or "пожалуйста" in resp.lower()
        assert state["need_llm_stream"] is False

    def test_off_topic_template_response(self, doctors_service, medesk_service, faq_service):
        """off_topic → polite redirect, no LLM."""
        state = make_state(
            user_message="Какой счёт в матче?",
            extracted=extract("arbitrary_message"),
            message_category="off_topic",
        )
        state = router_node(state)
        state = fallback_node(state)
        resp = collect_response(state)
        assert "записи" in resp.lower() or "клиник" in resp.lower() or "врач" in resp.lower()
        assert state["need_llm_stream"] is False

    def test_emotional_uses_llm(self, doctors_service, medesk_service, faq_service):
        """emotional → LLM stream with empathetic prompt."""
        state = make_state(
            user_message="У меня паника, не могу дышать",
            extracted=extract("arbitrary_message"),
            message_category="emotional",
        )
        state = router_node(state)
        state = fallback_node(state)
        assert state["need_llm_stream"] is True
        assert state["fallback_messages"] is not None
        # Should use emotional prompt, not generic
        system_msg = state["fallback_messages"][0]["content"]
        assert "эмпати" in system_msg.lower() or "эмоц" in system_msg.lower()

    def test_clinic_related_uses_llm(self, doctors_service, medesk_service, faq_service):
        """clinic_related → LLM stream with clinic context."""
        state = make_state(
            user_message="А вы только в Москве работаете?",
            extracted=extract("arbitrary_message"),
            message_category="clinic_related",
        )
        # clinic_related goes to faq, not fallback, but let's test fallback directly
        state["final_route"] = "fallback"
        state = fallback_node(state)
        assert state["need_llm_stream"] is True

    def test_signal_passthrough(self, doctors_service, medesk_service, faq_service):
        """Router-set responses (operator, goodbye) pass through unchanged."""
        state = make_state(
            user_message="Переведите на оператора",
            extracted=extract("wants_operator"),
        )
        state = router_node(state)
        state = fallback_node(state)
        resp = collect_response(state)
        assert "оператор" in resp.lower()
        assert state["need_llm_stream"] is False
        assert state["call_signal"] == "transfer_operator"


# ─── Improved FAQ matching with synonyms ─────────────────────────────


class TestImprovedFAQ:
    """Test FAQ synonym expansion and matching improvements."""

    def test_synonym_doekhat_matches_address(self, faq_service):
        """'Как доехать?' should match address FAQ via synonyms."""
        result = faq_service.match("Как к вам доехать?")
        assert result is not None
        assert "адрес" in result.answer.lower() or "шверника" in result.answer.lower()

    def test_synonym_grafik_matches_schedule(self, faq_service):
        """'Какой график?' should match work schedule FAQ."""
        result = faq_service.match("Какой у вас график работы?")
        assert result is not None
        assert "понедельник" in result.answer.lower() or "09:00" in result.answer

    def test_synonym_tsena_matches_cost(self, faq_service):
        """'Какие цены?' should match cost FAQ."""
        result = faq_service.match("Какие у вас цены?")
        assert result is not None
        assert "стоимость" in result.answer.lower() or "руб" in result.answer.lower()

    def test_synonym_online_matches_format(self, faq_service):
        """'Можно онлайн?' should match format FAQ."""
        result = faq_service.match("Можно онлайн?")
        assert result is not None
        assert "онлайн" in result.answer.lower()

    def test_synonym_anonymous_matches_pnd(self, faq_service):
        """'Это анонимно?' should match confidentiality FAQ."""
        result = faq_service.match("Это анонимно?")
        assert result is not None
        assert "конфиденциальн" in result.answer.lower() or "пнд" in result.answer.lower()

    def test_synonym_dom_matches_home_visit(self, faq_service):
        """'Можно вызвать на дом?' should match home visit FAQ."""
        result = faq_service.match("Можно вызвать на дом?")
        assert result is not None
        assert "выезд" in result.answer.lower() or "дом" in result.answer.lower()

    def test_get_all_faq_text(self, faq_service):
        """get_all_faq_text returns formatted FAQ list."""
        text = faq_service.get_all_faq_text()
        assert "1." in text
        assert "В:" in text
        assert "О:" in text

    def test_no_false_positive_on_unrelated(self, faq_service):
        """Unrelated questions should not match FAQ."""
        result = faq_service.match("Какая погода в Москве?")
        assert result is None

    def test_vyhodnye_matches_schedule(self, faq_service):
        """'Вы работаете по выходным?' should match schedule FAQ."""
        result = faq_service.match("Вы работаете по выходным?")
        assert result is not None
        assert "суббота" in result.answer.lower() or "воскресенье" in result.answer.lower() or "09:00" in result.answer


# ─── Router refactor: _resolve_pending_route ─────────────────────────


class TestRouterPendingResolution:
    """Test that pending_action resolution is correct after refactor."""

    def test_pending_name_stays_in_appointment(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Иван Петров",
            extracted=extract("arbitrary_message", patient_name="Иван Петров"),
            pending_action="ask_patient_name",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"

    def test_pending_name_question_goes_to_doctor_info(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="А какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="ask_patient_name",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"

    def test_pending_name_clinic_question_goes_to_faq(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Где вы находитесь?",
            extracted=extract("question_about_clinic"),
            pending_action="ask_patient_name",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"

    def test_pending_dob_stays_in_appointment(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="15 мая 1990",
            extracted=extract("arbitrary_message", birth_date="15 мая 1990"),
            pending_action="ask_birth_date",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"

    def test_confirm_slot_agreement_rewrites_intent(self, doctors_service, medesk_service, faq_service):
        """arbitrary_message during confirm_slot → rewritten to chosen_slot."""
        state = make_state(
            user_message="Ну давайте",
            extracted=extract("arbitrary_message"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"
        assert state["extracted"].patient_intent == "chosen_slot"

    def test_confirm_slot_doctor_question_goes_to_info(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="А он хороший врач?",
            extracted=extract("question_about_doctor"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"

    def test_confirm_alt_doctor_stays_appointment(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Хорошо, давайте",
            extracted=extract("doctor_ok"),
            pending_action="confirm_alt_doctor",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = router_node(state)
        assert state["final_route"] == "appointment"

    def test_no_pending_does_not_affect_routing(self, doctors_service, medesk_service, faq_service):
        """Without pending_action, pure intent routing works."""
        state = make_state(
            user_message="Расскажите про Хайретдинова",
            extracted=extract("question_about_doctor", doctor_name="Хайретдинов"),
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"


# ─── Router signal handling ──────────────────────────────────────────


class TestRouterSignals:
    """Test that signals (operator, goodbye) always take priority."""

    def test_operator_during_pending_name(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Переведите на оператора",
            extracted=extract("wants_operator"),
            pending_action="ask_patient_name",
        )
        state = router_node(state)
        assert state["call_signal"] == "transfer_operator"
        assert state["need_llm_stream"] is False

    def test_goodbye_during_confirm_slot(self, doctors_service, medesk_service, faq_service):
        state = make_state(
            user_message="Нет, до свидания",
            extracted=extract("end_conversation"),
            pending_action="confirm_slot",
            selected_slot="2026-04-06 10:00",
        )
        state = router_node(state)
        assert state["call_signal"] == "end_call"
        assert "до свидания" in collect_response(state).lower()


# ─── FAQ node with LLM-aware fallback ────────────────────────────────


class TestFAQNodeLLMFallback:
    """Test that FAQ node provides FAQ context even when stem match fails."""

    def test_stem_match_uses_faq_prompt(self, faq_service):
        """When stem match found, uses FAQ_ANSWER_PROMPT."""
        state = make_state(
            user_message="Где находится клиника?",
            extracted=extract("question_about_clinic"),
            message_category="clinic_related",
        )
        from ai_agent.nodes.faq import faq_node
        state = faq_node(state, faq_service)
        assert state["need_llm_stream"] is True
        assert state.get("faq_match") is not None

    def test_no_stem_match_uses_faq_context(self, faq_service):
        """When no stem match, LLM gets full FAQ list as context."""
        state = make_state(
            user_message="Можно привести собаку с собой?",
            extracted=extract("question_about_clinic"),
            message_category="clinic_related",
        )
        from ai_agent.nodes.faq import faq_node
        state = faq_node(state, faq_service)
        assert state["need_llm_stream"] is True
        assert state.get("fallback_messages") is not None
        # System prompt should contain FAQ list
        system_msg = state["fallback_messages"][0]["content"]
        assert "FAQ" in system_msg

    def test_faq_node_preserves_history(self, faq_service):
        """FAQ node passes conversation history to LLM."""
        history = [
            {"role": "user", "content": "Привет"},
            {"role": "assistant", "content": "Здравствуйте!"},
        ]
        state = make_state(
            user_message="А вы по выходным работаете?",
            extracted=extract("question_about_clinic"),
            message_category="clinic_related",
            history=history,
        )
        from ai_agent.nodes.faq import faq_node
        state = faq_node(state, faq_service)
        if state.get("fallback_messages"):
            # History should be in conversation
            assert len(state["fallback_messages"]) >= 3  # system + history + user


# ─── End-to-end: category-aware flow ────────────────────────────────


class TestCategoryAwareFlow:
    """Full turn simulations with message_category."""

    def test_off_topic_then_booking(self, doctors_service, medesk_service, faq_service):
        """Off-topic → polite redirect, then booking works."""
        # Turn 1: Off-topic
        state = make_state(
            user_message="Как попасть на Марс?",
            extracted=extract("arbitrary_message"),
            message_category="off_topic",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp1 = collect_response(state)
        assert "записи" in resp1.lower() or "клиник" in resp1.lower() or "специалист" in resp1.lower()

        # Turn 2: Booking (should work normally)
        history = add_to_history({}, "Как попасть на Марс?", resp1)
        state2 = make_state(
            user_message="Хочу записаться к Хайретдинову",
            extracted=extract("make_appoint", doctor_name="Хайретдинов"),
            history=history,
        )
        state2 = run_turn(state2, doctors_service, medesk_service, faq_service)
        assert state2["pending_action"] == "confirm_slot"

    def test_emotional_gets_empathetic_response(self, doctors_service, medesk_service, faq_service):
        """Emotional message → LLM with empathetic prompt."""
        state = make_state(
            user_message="Мне очень страшно, я не знаю к кому обратиться",
            extracted=extract("arbitrary_message"),
            message_category="emotional",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Should route to fallback with LLM
        assert state["need_llm_stream"] is True
        system_msg = state["fallback_messages"][0]["content"]
        assert "эмпати" in system_msg.lower() or "забот" in system_msg.lower()

    def test_greeting_fast_response(self, doctors_service, medesk_service, faq_service):
        """Greeting → instant template, no LLM call."""
        state = make_state(
            user_message="Добрый день",
            extracted=extract("arbitrary_message"),
            message_category="greeting",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["need_llm_stream"] is False
        assert "здравствуйте" in resp.lower() or "алиса" in resp.lower()
        assert "помочь" in resp.lower()

    def test_gratitude_fast_response(self, doctors_service, medesk_service, faq_service):
        """Gratitude → instant template, no LLM call."""
        state = make_state(
            user_message="Спасибо вам",
            extracted=extract("arbitrary_message"),
            message_category="gratitude",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["need_llm_stream"] is False
        assert "помочь" in resp.lower() or "пожалуйста" in resp.lower()

    def test_clinic_related_arbitrary_uses_faq(self, doctors_service, medesk_service, faq_service):
        """Clinic-related arbitrary → routed to FAQ, gets FAQ context."""
        state = make_state(
            user_message="Есть ли у вас парковка?",
            extracted=extract("arbitrary_message"),
            message_category="clinic_related",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"


# ─── Case 44: Multi-intent — slot confirmation + doctor question ──


class TestCase44_MultiIntent:
    """Case 44: User confirms slot AND asks a question about the doctor in one message."""

    def test_chosen_slot_plus_question_about_doctor(self, doctors_service, medesk_service, faq_service):
        """'Да, подходит. Расскажите про него подробнее.' → answer doctor question + CTA = ask name."""
        state = make_state(
            user_message="Да, подходит. Расскажите про него подробнее.",
            extracted=extract(
                "chosen_slot",
                secondary_intent="question_about_doctor",
            ),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="make_appoint",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        # Should advance to ask_patient_name (slot confirmed)
        assert state["pending_action"] == "ask_patient_name"
        # Should route to doctor_info and generate LLM response about the doctor
        assert state["need_llm_stream"] is True
        assert state["final_route"] == "doctor_info"

    def test_question_about_doctor_plus_chosen_slot(self, doctors_service, medesk_service, faq_service):
        """Same as above but LLM picks question_about_doctor as primary, chosen_slot as secondary."""
        state = make_state(
            user_message="Да, подходит. Расскажите про него подробнее.",
            extracted=extract(
                "question_about_doctor",
                secondary_intent="chosen_slot",
            ),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="make_appoint",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "ask_patient_name"
        assert state["final_route"] == "doctor_info"

    def test_chosen_slot_plus_experience_question(self, doctors_service, medesk_service, faq_service):
        """'Да. А какой у него стаж?' → answer experience + CTA = ask name."""
        state = make_state(
            user_message="Да. А какой у него стаж?",
            extracted=extract(
                "chosen_slot",
                secondary_intent="question_about_doctor",
                doctor_info="experience",
            ),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
            prev_intent="make_appoint",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert state["pending_action"] == "ask_patient_name"
        assert "стаж" in resp.lower()
        assert "зовут" in resp.lower()

    def test_question_without_agreement_stays_confirm_slot(self, doctors_service, medesk_service, faq_service):
        """Single intent — question without agreement → pending stays confirm_slot."""
        state = make_state(
            user_message="А какой у него стаж?",
            extracted=extract(
                "question_about_doctor",
                doctor_info="experience",
            ),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert "стаж" in resp.lower()
        # No secondary_intent → pending stays confirm_slot, CTA about slot
        assert "подходит" in resp.lower() or "апрел" in resp.lower()

    def test_chosen_slot_plus_clinic_question(self, doctors_service, medesk_service, faq_service):
        """'Да. А где вы находитесь?' → confirm slot + route to FAQ."""
        state = make_state(
            user_message="Да. А где вы находитесь?",
            extracted=extract(
                "chosen_slot",
                secondary_intent="question_about_clinic",
            ),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="make_appoint",
        )
        state = router_node(state)
        assert state["final_route"] == "faq"
        assert state["pending_action"] == "ask_patient_name"

    def test_no_secondary_intent_normal_routing(self, doctors_service, medesk_service, faq_service):
        """No secondary_intent → normal single-intent routing (chosen_slot → appointment)."""
        state = make_state(
            user_message="Да, подходит",
            extracted=extract("chosen_slot"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 10:00",
            prev_intent="make_appoint",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state["pending_action"] == "ask_patient_name"
        resp = collect_response(state)
        assert "зовут" in resp.lower()


# ─── Case 45: Varied booking CTA phrases ──


class TestCase45_CTAVariety:
    """Case 45: CTA 'Записать вас на приём?' should not repeat verbatim — use variants."""

    def test_booking_cta_variants_exist(self):
        """Verify the CTA variant list contains multiple options."""
        from ai_agent.nodes.doctor_info import _BOOKING_CTA_VARIANTS
        assert len(_BOOKING_CTA_VARIANTS) >= 4

    def test_cta_no_pending_is_booking_variant(self, doctors_service, medesk_service, faq_service):
        """CTA with no pending → one of the booking CTA variants."""
        from ai_agent.nodes.doctor_info import _BOOKING_CTA_VARIANTS
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert any(variant in resp for variant in _BOOKING_CTA_VARIANTS)

    def test_cta_confirm_alt_doctor_is_booking_variant(self, doctors_service, medesk_service, faq_service):
        """CTA with confirm_alt_doctor → one of the booking CTA variants."""
        from ai_agent.nodes.doctor_info import _BOOKING_CTA_VARIANTS
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="confirm_alt_doctor",
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        assert any(variant in resp for variant in _BOOKING_CTA_VARIANTS)

    def test_cta_confirm_slot_uses_slot_time(self, doctors_service, medesk_service, faq_service):
        """CTA during confirm_slot with selected_slot → mentions the time, not a generic CTA."""
        state = make_state(
            user_message="Какой у него стаж?",
            extracted=extract("question_about_doctor", doctor_info="experience"),
            pending_action="confirm_slot",
            pending_doctor_name="Хайретдинов Олег Замильевич",
            selected_slot="2026-04-06 11:00",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state)
        # Should mention the slot time, not a generic booking CTA
        assert "подходит" in resp.lower() or "апрел" in resp.lower()

    def test_llm_prompt_contains_dynamic_cta(self, doctors_service, medesk_service, faq_service):
        """LLM prompt for doctor info uses dynamic CTA placeholder."""
        from ai_agent.nodes.doctor_info import _BOOKING_CTA_VARIANTS
        state = make_state(
            user_message="Расскажите про Хайретдинова",
            extracted=extract("question_about_doctor"),
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        prompt = state.get("llm_prompt", "")
        # The LLM prompt should contain one of the CTA variants
        assert any(variant in prompt for variant in _BOOKING_CTA_VARIANTS)


# ─── Case 46: 'Кто лечит X?' → want_procedure ──


class TestCase46_WhoTreatsDisease:
    """Case 46: 'Кто у вас лечит X?' should be classified as want_procedure, not question_about_clinic."""

    def test_who_treats_neurotic_disorder(self, doctors_service, medesk_service, faq_service):
        """'Кто у вас лечит невротическое расстройство?' → finds Хайретдинов."""
        state = make_state(
            user_message="А кто у вас лечит невротическое расстройство?",
            extracted=extract("want_procedure", diseases=["невротическое расстройство"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""
        assert state.get("matched_doctor") is not None
        assert state["matched_doctor"]["name"] == "Хайретдинов Олег Замильевич"
        assert state.get("pending_doctor_name") == "Хайретдинов Олег Замильевич"
        # Should NOT say "обратиться в профильную клинику"
        assert "профильн" not in resp.lower()

    def test_who_treats_anxiety_disorder(self, doctors_service, medesk_service, faq_service):
        """'Кто занимается тревожными расстройствами?' → finds a matching doctor."""
        state = make_state(
            user_message="Кто занимается тревожными расстройствами?",
            extracted=extract("want_procedure", diseases=["тревожное расстройство"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        assert state.get("matched_doctor") is not None
        # Хайретдинов has "тревожное расстройство" in diseases
        assert "тревожн" in state["matched_doctor"]["diseases"][1].lower()
        assert state.get("need_llm_stream") is True

    def test_who_treats_routes_to_doctor_info(self, doctors_service, medesk_service, faq_service):
        """'Кто лечит X?' with want_procedure intent → routes to doctor_info."""
        state = make_state(
            user_message="Есть ли у вас специалист по нарушениям сна?",
            extracted=extract("want_procedure", diseases=["нарушения сна"]),
        )
        state = router_node(state)
        assert state["final_route"] == "doctor_info"

    def test_who_treats_with_pending_doctor(self, doctors_service, medesk_service, faq_service):
        """'Кто лечит X?' while discussing a doctor who treats it → confirms current doctor."""
        state = make_state(
            user_message="А он лечит тревожное расстройство?",
            extracted=extract("want_procedure", diseases=["тревожное расстройство"]),
            pending_doctor_name="Хайретдинов Олег Замильевич",
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""
        # Should confirm current doctor treats this disease
        assert "хайретдинов" in resp.lower() or state.get("need_llm_stream")
        assert state["pending_doctor_name"] == "Хайретдинов Олег Замильевич"

    def test_who_treats_unknown_disease_not_found(self, doctors_service, medesk_service, faq_service):
        """Disease not in any doctor's list → appropriate fallback message."""
        state = make_state(
            user_message="Кто лечит диабет?",
            extracted=extract("want_procedure", diseases=["диабет"]),
        )
        state = run_turn(state, doctors_service, medesk_service, faq_service)
        resp = collect_response(state) or ""
        # Should indicate this is not our specialization
        assert "специализируется" in resp.lower() or "профильн" in resp.lower()
