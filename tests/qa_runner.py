"""
QA Agent — automated scenario testing for Psy Family chatbot.

Runs all scenarios from the QA document, logs every Q&A pair,
and writes a detailed report to qa_report_YYYY-MM-DD_HH-MM-SS.txt

Usage:
    python -m tests.qa_runner
    python -m tests.qa_runner --scenario 1      # run only scenario 1
    python -m tests.qa_runner --checklist        # run only checklist
    python -m tests.qa_runner --output report.txt

Requires LLM_API_KEY env var.
"""
import argparse
import os
import sys
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_agent.agent import PsyFamilyAgent


# ── Data structures ──────────────────────────────────────────

@dataclass
class StepResult:
    step_id: str
    user_message: str
    bot_response: str
    call_signal: Optional[str]
    checks: list  # list of (description, passed, detail)
    passed: bool = True

    def __post_init__(self):
        self.passed = all(c[1] for c in self.checks)


@dataclass
class ScenarioResult:
    name: str
    description: str
    steps: list = field(default_factory=list)

    @property
    def passed(self):
        return all(s.passed for s in self.steps)

    @property
    def total_checks(self):
        return sum(len(s.checks) for s in self.steps)

    @property
    def passed_checks(self):
        return sum(1 for s in self.steps for c in s.checks if c[1])


# ── Helpers ──────────────────────────────────────────────────

def chat(agent: PsyFamilyAgent, message: str, session_id: str) -> str:
    chunks = agent(message, session_id=session_id)
    return "".join(chunk.text for chunk in chunks)


def signal(agent: PsyFamilyAgent, session_id: str) -> Optional[str]:
    return agent.get_call_signal(session_id)


def reset(agent: PsyFamilyAgent, session_id: str):
    agent.drop_session(session_id)


def check_contains(response: str, words: list, description: str) -> tuple:
    """Check that response contains at least one of the words."""
    resp_lower = response.lower()
    found = [w for w in words if w.lower() in resp_lower]
    passed = len(found) > 0
    detail = f"found: {found}" if passed else f"none of {words} found"
    return (description, passed, detail)


def check_not_contains(response: str, words: list, description: str) -> tuple:
    """Check that response does NOT contain any of the words."""
    resp_lower = response.lower()
    found = [w for w in words if w.lower() in resp_lower]
    passed = len(found) == 0
    detail = "OK" if passed else f"unwanted words found: {found}"
    return (description, passed, detail)


def check_no_encoding_issues(response: str) -> tuple:
    """Check for encoding artifacts."""
    bad_chars = ["��", "□", "\ufffd"]
    found = [c for c in bad_chars if c in response]
    passed = len(found) == 0
    detail = "OK" if passed else f"encoding issues: {found}"
    return ("No encoding artifacts", passed, detail)


def check_not_empty(response: str) -> tuple:
    """Check response is not empty."""
    passed = len(response.strip()) > 0
    return ("Response is not empty", passed, f"length={len(response)}")


def check_signal_none(sig: Optional[str]) -> tuple:
    passed = sig is None
    return ("No premature call signal", passed, f"signal={sig}")


def check_signal_equals(sig: Optional[str], expected: str) -> tuple:
    passed = sig == expected
    return (f"Signal equals '{expected}'", passed, f"signal={sig}")


# ── Scenario 1: Full booking cycle ──────────────────────────

def run_scenario_1(agent: PsyFamilyAgent) -> ScenarioResult:
    result = ScenarioResult(
        name="Scenario 1",
        description="Full booking cycle with a specific doctor",
    )
    sid = "qa_s1"
    reset(agent, sid)

    # 1.1 — Greeting
    greeting = agent.get_greeting()
    step = StepResult(
        step_id="1.1",
        user_message="[greeting request]",
        bot_response=greeting,
        call_signal=None,
        checks=[
            check_contains(greeting, ["psy family", "клиника", "помочь"], "Greeting mentions clinic"),
            check_not_empty(greeting),
        ],
    )
    result.steps.append(step)

    # 1.2 — Request appointment with specific doctor
    r = chat(agent, "Хочу записаться к Хайретдинову", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.2",
        user_message="Хочу записаться к Хайретдинову",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_contains(r, ["хайретдинов", "олег"], "Mentions doctor name"),
            check_contains(r, ["свободно", "доступно", "окно", "записать", "подходит", "запись"], "Offers a slot"),
            check_no_encoding_issues(r),
            check_signal_none(sig),
        ],
    )
    result.steps.append(step)

    # 1.3 — Ask for other dates
    r = chat(agent, "А какие ещё есть свободные даты?", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.3",
        user_message="А какие ещё есть свободные даты?",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_not_empty(r),
            check_signal_none(sig),
        ],
    )
    result.steps.append(step)

    # 1.4 — Reject slot, ask for alternatives
    r = chat(agent, "Этот мне не подходит, других нет?", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.4",
        user_message="Этот мне не подходит, других нет?",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_not_empty(r),
            # Should offer alternative (slot, doctor, or callback)
            check_contains(r, [
                "предлож", "другой", "альтернатив", "врач", "слот",
                "перезвон", "ожидан", "свободн", "подходит", "удобно",
            ], "Offers alternative"),
            check_signal_none(sig),
        ],
    )
    result.steps.append(step)

    # 1.5 — Accept a slot
    r = chat(agent, "Да, запишите", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.5",
        user_message="Да, запишите",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_no_encoding_issues(r),
            check_signal_none(sig),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 1.6 — Ask about experience instead of answering bot's question
    r = chat(agent, "Расскажите про его стаж", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.6",
        user_message="Расскажите про его стаж",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_contains(r, ["стаж", "лет", "опыт", "30"], "Answers about experience"),
            # Should return to data collection
            check_contains(r, ["зовут", "имя", "фио", "рождения"], "Returns to data collection"),
            check_signal_none(sig),
        ],
    )
    result.steps.append(step)

    # 1.7 — Give name
    r = chat(agent, "Иван Петров", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.7",
        user_message="Иван Петров",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_contains(r, ["рождения", "дат"], "Asks for DOB"),
            check_signal_none(sig),
        ],
    )
    result.steps.append(step)

    # 1.8 — Ask "why do you need DOB?" instead of answering
    r = chat(agent, "А зачем это?", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.8",
        user_message="А зачем это?",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_not_contains(r, ["записаны"], "Does NOT complete booking without DOB"),
            check_contains(r, ["рождения", "дат", "оформлен", "идентификац", "карт"], "Explains or re-asks DOB"),
            check_signal_none(sig),
        ],
    )
    result.steps.append(step)

    # 1.9 — Give DOB
    r = chat(agent, "15 марта 1990", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.9",
        user_message="15 марта 1990",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_contains(r, ["записан", "приём", "ждём"], "Confirms booking"),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 1.10 — Ask "what's your name?" after booking
    r = chat(agent, "А как тебя зовут?", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="1.10",
        user_message="А как тебя зовут?",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_contains(r, ["алис", "оператор", "клиник"], "Introduces as operator"),
            check_not_contains(r, ["модель", "нейросет", "ии", "бот", "google", "openai", "ai"], "Does NOT reveal AI identity"),
        ],
    )
    result.steps.append(step)

    return result


# ── Scenario 2: Info about doctor, clinic, procedures ────────

def run_scenario_2(agent: PsyFamilyAgent) -> ScenarioResult:
    result = ScenarioResult(
        name="Scenario 2",
        description="Doctor/clinic/procedure info requests",
    )
    sid = "qa_s2"
    reset(agent, sid)

    # 2.1 — Tell about clinic
    r = chat(agent, "Расскажите про вашу клинику", sid)
    step = StepResult(
        step_id="2.1",
        user_message="Расскажите про вашу клинику",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_empty(r),
            check_contains(r, ["psy family", "пси фэмили", "психиатр", "психотерап", "шверника", "москв"], "Contains clinic info"),
        ],
    )
    result.steps.append(step)

    # 2.2 — Clinic name
    reset(agent, sid)
    r = chat(agent, "А как называется ваша клиника?", sid)
    step = StepResult(
        step_id="2.2",
        user_message="А как называется ваша клиника?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["psy family", "psy-family", "пси фэмили", "пси-фэмили"], "Answers with clinic name"),
            check_not_contains(r, ["15 минут", "приехать за"], "No irrelevant info"),
        ],
    )
    result.steps.append(step)

    # 2.3 — EPI procedure
    reset(agent, sid)
    r = chat(agent, "Я хочу пройти процедуру ЭПИ", sid)
    step = StepResult(
        step_id="2.3",
        user_message="Я хочу пройти процедуру ЭПИ",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "хайретдинов", "нисанова", "врач", "специалист",
                "эпи", "исследование", "диагностик",
            ], "Finds doctor or describes EPI procedure"),
            check_contains(r, [
                "записать", "записаться", "приём", "запишем", "время",
                "забронировать", "окошко", "закрепить",
                "удобно подойти", "когда вам удобно",
            ], "Offers to book"),
        ],
    )
    result.steps.append(step)

    # 2.4 — "Ok" should not reset context
    r = chat(agent, "Ок", sid)
    step = StepResult(
        step_id="2.4",
        user_message="Ок",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_contains(r, ["здравствуйте", "чем могу помочь"], "Does NOT reset to greeting"),
        ],
    )
    result.steps.append(step)

    # 2.5 — Neurotic disorder → specific doctor
    reset(agent, sid)
    r = chat(agent, "Кто лечит невротическое расстройство?", sid)
    step = StepResult(
        step_id="2.5",
        user_message="Кто лечит невротическое расстройство?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "хайретдинов", "жданов", "тер-исраелян", "лазебный",
                "шипотько", "бутова", "некрылов",
            ], "Recommends specific doctor by name"),
            check_not_contains(r, ["опытные специалисты"], "Not vague 'experienced specialists'"),
        ],
    )
    result.steps.append(step)

    # 2.6 — Out-of-profile complaint
    reset(agent, sid)
    r = chat(agent, "У меня насморк, что делать?", sid)
    step = StepResult(
        step_id="2.6",
        user_message="У меня насморк, что делать?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "специализируемся", "профиль", "психич", "терапевт",
                "профильн", "стоматолог", "другую клинику",
            ], "Indicates out-of-profile or redirects"),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 2.7 — "Tell about yourself"
    reset(agent, sid)
    r = chat(agent, "Расскажи про себя", sid)
    step = StepResult(
        step_id="2.7",
        user_message="Расскажи про себя",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["алис", "оператор", "клиник", "psy family"], "Introduces as clinic operator"),
        ],
    )
    result.steps.append(step)

    # 2.8 — "Where do you live?" provocation
    reset(agent, sid)
    r = chat(agent, "Где ты живешь?", sid)
    step = StepResult(
        step_id="2.8",
        user_message="Где ты живешь?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_contains(r, ["модель", "нейросет", "google", "openai", "языковая модель"], "Does NOT reveal AI"),
        ],
    )
    result.steps.append(step)

    # 2.9 — Doctor experience
    reset(agent, sid)
    r = chat(agent, "А какой у Хайретдинова стаж?", sid)
    step = StepResult(
        step_id="2.9",
        user_message="А какой у Хайретдинова стаж?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["30", "лет", "стаж"], "Answers about experience"),
        ],
    )
    result.steps.append(step)

    # 2.10 — "Want to book" after discussing doctor
    r = chat(agent, "Хочу записаться", sid)
    step = StepResult(
        step_id="2.10",
        user_message="Хочу записаться",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "хайретдинов", "свободно", "доступно", "окно",
                "записать", "слот", "время", "дату",
            ], "Offers slot for discussed doctor"),
        ],
    )
    result.steps.append(step)

    return result


# ── Scenario 3: Booking by symptom + edge cases ─────────────

def run_scenario_3(agent: PsyFamilyAgent) -> ScenarioResult:
    result = ScenarioResult(
        name="Scenario 3",
        description="Booking by symptom, no doctor specified + edge cases",
    )
    sid = "qa_s3"
    reset(agent, sid)

    # 3.1 — Disease mention → specific doctor, no auto-booking
    r = chat(agent, "У меня невротическое расстройство, хочу записаться к врачу", sid)
    step = StepResult(
        step_id="3.1",
        user_message="У меня невротическое расстройство, хочу записаться к врачу",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "хайретдинов", "жданов", "тер-исраелян", "лазебный",
                "шипотько", "некрылов", "нисанова", "бутова",
                "вишнивецкая", "майорова",
            ], "Recommends specific doctor by name"),
            check_not_contains(r, ["записаны"], "Does NOT auto-book without data collection"),
            check_signal_none(signal(agent, sid)),
        ],
    )
    result.steps.append(step)

    # 3.2 — Confirm + ask for more info (multi-intent)
    r = chat(agent, "Да, подходит. Расскажите про него подробнее", sid)
    step = StepResult(
        step_id="3.2",
        user_message="Да, подходит. Расскажите про него подробнее",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_empty(r),
            check_contains(r, [
                "стаж", "категори", "психиатр", "к.м.н", "кандидат",
                "детский", "московский врач", "зовут", "имя", "рождения",
            ], "Provides info and/or continues booking"),
        ],
    )
    result.steps.append(step)

    # 3.3 — Give name
    r = chat(agent, "Анна Сидорова", sid)
    step = StepResult(
        step_id="3.3",
        user_message="Анна Сидорова",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["рождения", "дат"], "Asks for DOB"),
        ],
    )
    result.steps.append(step)

    # 3.4 — Give DOB → booking complete
    r = chat(agent, "20 июня 1985", sid)
    step = StepResult(
        step_id="3.4",
        user_message="20 июня 1985",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["записан", "приём", "ждём"], "Confirms booking"),
        ],
    )
    result.steps.append(step)

    # 3.5 — New dialog: book by name
    reset(agent, sid)
    r = chat(agent, "Хочу записаться к Хайретдинову", sid)
    step = StepResult(
        step_id="3.5",
        user_message="Хочу записаться к Хайретдинову (new session)",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["хайретдинов", "свободно", "доступно", "окно", "записать", "подходит"], "Offers slot"),
        ],
    )
    result.steps.append(step)

    # 3.6 — Ask "which date?" instead of yes/no
    r = chat(agent, "На какую дату?", sid)
    step = StepResult(
        step_id="3.6",
        user_message="На какую дату?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_contains(r, ["беспокоит", "что вас"], "Does NOT ask 'what bothers you'"),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 3.7 — New dialog: full name, check no-slots handling
    reset(agent, sid)
    r = chat(agent, "Хочу записаться к Хайретдинову Олегу Замильевичу", sid)
    step = StepResult(
        step_id="3.7",
        user_message="Хочу записаться к Хайретдинову Олегу Замильевичу (new session)",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_empty(r),
            # Should either offer slot or if no slots — offer alternative
            check_contains(r, [
                "свободно", "доступно", "окно", "записать", "подходит",
                "нет свободных", "другой", "предлож", "перезвон",
            ], "Handles slot availability"),
        ],
    )
    result.steps.append(step)

    # 3.8 — Does doctor treat neurotic disorder?
    r = chat(agent, "Занимается ли он лечением невротического расстройства?", sid)
    step = StepResult(
        step_id="3.8",
        user_message="Занимается ли он лечением невротического расстройства?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_empty(r),
            check_not_contains(r, ["ОБЯЗАТЕЛЬНО поможет"], "No excessive emotionality"),
        ],
    )
    result.steps.append(step)

    # 3.9 — New dialog: book + info combined
    reset(agent, sid)
    r = chat(agent, "Я хочу записаться к Хайретдинову, расскажите пожалуйста про него подробнее", sid)
    step = StepResult(
        step_id="3.9",
        user_message="Я хочу записаться к Хайретдинову, расскажите про него подробнее (new session)",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["хайретдинов", "психиатр", "категори", "к.м.н", "стаж", "записать", "приём"], "Provides info AND offers booking"),
        ],
    )
    result.steps.append(step)

    # 3.10 — Ask clinic name mid-flow
    r = chat(agent, "А как называется ваша клиника?", sid)
    step = StepResult(
        step_id="3.10",
        user_message="А как называется ваша клиника?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["psy family", "psy-family", "пси фэмили", "пси-фэмили"], "Answers with clinic name"),
        ],
    )
    result.steps.append(step)

    return result


# ── Scenario 4: Compound messages (two questions in one) ─────

def run_scenario_4(agent: PsyFamilyAgent) -> ScenarioResult:
    result = ScenarioResult(
        name="Scenario 4",
        description="Compound messages — two questions/requests in one message",
    )

    # 4.1 — Book + ask about doctor in one message
    sid = "qa_s4"
    reset(agent, sid)
    r = chat(agent, "Я хочу записаться к Хайретдинову, расскажите пожалуйста про него подробнее", sid)
    step = StepResult(
        step_id="4.1",
        user_message="Я хочу записаться к Хайретдинову, расскажите про него подробнее",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["хайретдинов", "олег"], "Mentions doctor"),
            check_contains(r, [
                "записать", "записаться", "слот", "свободно", "доступно",
                "окно", "забронировать", "закрепить", "удобно подойти",
                "психиатр", "к.м.н", "категори", "стаж", "московский",
            ], "Provides info or offers slot"),
        ],
    )
    result.steps.append(step)

    # 4.2 — Two info questions: experience + location
    reset(agent, sid)
    chat(agent, "Расскажите про Хайретдинова", sid)
    r = chat(agent, "Опытный ли он врач? Где вы находитесь?", sid)
    step = StepResult(
        step_id="4.2",
        user_message="Опытный ли он врач? Где вы находитесь?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "стаж", "лет", "опыт", "категори", "к.м.н", "квалификац",
            ], "Answers about experience"),
            # Location might not always be in the same response due to single-topic LLM
            # But at minimum the bot should not ignore both
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 4.3 — Confirm slot + ask for more info
    reset(agent, sid)
    chat(agent, "Хочу записаться к Хайретдинову", sid)
    r = chat(agent, "Да, подходит. Расскажите про него подробнее", sid)
    step = StepResult(
        step_id="4.3",
        user_message="Да, подходит. Расскажите про него подробнее",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            # Should continue booking (ask name) or provide info — NOT re-ask slot
            check_not_contains(r, ["на какую дату", "какое время"], "Does NOT re-ask for date/time"),
            check_contains(r, [
                "зовут", "имя", "психиатр", "к.м.н", "категори",
                "стаж", "московский", "записываю",
            ], "Continues booking or gives info"),
        ],
    )
    result.steps.append(step)

    # 4.4 — Want to book + ask about experience
    reset(agent, sid)
    chat(agent, "Расскажите про Хайретдинова", sid)
    r = chat(agent, "Хочу записаться. А какой у него стаж?", sid)
    step = StepResult(
        step_id="4.4",
        user_message="Хочу записаться. А какой у него стаж?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "свободно", "доступно", "окно", "записать", "слот",
                "забронировать", "закрепить", "стаж", "лет",
            ], "Offers slot or mentions experience"),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 4.5 — Clinic name + available dates
    reset(agent, sid)
    chat(agent, "Хочу записаться к Хайретдинову", sid)
    r = chat(agent, "Как называется ваша клиника? А на какие даты есть запись?", sid)
    step = StepResult(
        step_id="4.5",
        user_message="Как называется ваша клиника? А на какие даты есть запись?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_empty(r),
            # Should answer at least one of the two questions
            check_contains(r, [
                "psy family", "psy-family", "пси фэмили",
                "свободно", "доступно", "окно", "апреля",
                "клиника", "называется",
            ], "Answers clinic name or slot info"),
        ],
    )
    result.steps.append(step)

    # 4.6 — Give name + ask about appointment duration (during data collection)
    reset(agent, sid)
    chat(agent, "Хочу записаться к Хайретдинову", sid)
    chat(agent, "Да", sid)  # accept slot → asks name
    r = chat(agent, "Иван Петров. А сколько длится приём?", sid)
    step = StepResult(
        step_id="4.6",
        user_message="Иван Петров. А сколько длится приём?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            # Bot should accept the name (transition to DOB or answer question)
            # and not lose the booking flow
            check_not_contains(r, ["зовут", "имя", "фио"], "Accepted the name (not re-asking)"),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 4.7 — Disease + cost in one message
    reset(agent, sid)
    r = chat(agent, "У меня невротическое расстройство, кто у вас это лечит и сколько стоит приём?", sid)
    step = StepResult(
        step_id="4.7",
        user_message="У меня невротическое расстройство, кто это лечит и сколько стоит?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            # Should name a specific doctor
            check_contains(r, [
                "хайретдинов", "жданов", "тер-исраелян", "лазебный",
                "шипотько", "некрылов",
            ], "Names a specific doctor"),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # 4.8 — Specific date + how to get there
    reset(agent, sid)
    chat(agent, "Хочу записаться к Хайретдинову", sid)
    r = chat(agent, "Запишите меня на шестое апреля. И расскажите, как до вас добраться", sid)
    step = StepResult(
        step_id="4.8",
        user_message="Запишите на шестое апреля. Как до вас добраться?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_empty(r),
            # Should handle at least the booking part
            check_contains(r, [
                "шест", "апрел", "записыва", "подходит", "удобно",
                "свободно", "доступно", "окно", "зовут",
                "шверника", "москв", "адрес", "добраться",
            ], "Handles booking date or location"),
        ],
    )
    result.steps.append(step)

    return result


# ── Checklist tests ──────────────────────────────────────────

def run_checklist(agent: PsyFamilyAgent) -> ScenarioResult:
    result = ScenarioResult(
        name="Checklist",
        description="Individual checks from QA checklist (A-H)",
    )

    # === E2/E3: Side questions during booking don't break flow ===
    sid = "qa_cl_e2"
    reset(agent, sid)
    chat(agent, "Хочу записаться к Хайретдинову", sid)
    chat(agent, "Да", sid)  # accept slot → asks name

    r = chat(agent, "А вы далеко от центра находитесь?", sid)
    step = StepResult(
        step_id="E2/E3",
        user_message="FAQ during name collection: 'А вы далеко от центра?'",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, ["шверника", "москв", "адрес"], "Answers location question"),
            check_contains(r, ["зовут", "имя"], "Returns to asking name"),
        ],
    )
    result.steps.append(step)

    # === E1: "Ok" does not reset context ===
    sid = "qa_cl_e1"
    reset(agent, sid)
    chat(agent, "Кто лечит депрессию?", sid)
    r = chat(agent, "Ок", sid)
    step = StepResult(
        step_id="E1",
        user_message="'Ок' after doctor recommendation",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_contains(r, ["здравствуйте", "чем могу помочь"], "Does NOT reset to greeting"),
        ],
    )
    result.steps.append(step)

    # === C3/C4: Must collect name AND DOB before booking ===
    sid = "qa_cl_c3"
    reset(agent, sid)
    chat(agent, "Хочу записаться к Хайретдинову", sid)
    chat(agent, "Да", sid)
    r_booking = chat(agent, "Да давайте записывайте", sid)
    step = StepResult(
        step_id="C3/C4",
        user_message="'Да давайте записывайте' without providing name/DOB",
        bot_response=r_booking,
        call_signal=signal(agent, sid),
        checks=[
            # Should NOT confirm booking without data
            check_not_contains(r_booking, ["записаны", "ждём вас на приёме"], "No premature booking"),
            check_contains(r_booking, ["зовут", "имя", "фио"], "Still asks for name"),
        ],
    )
    result.steps.append(step)

    # === F2: Provocation — "who created you?" ===
    sid = "qa_cl_f2"
    reset(agent, sid)
    r = chat(agent, "Кто тебя создал?", sid)
    step = StepResult(
        step_id="F2",
        user_message="Кто тебя создал?",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_contains(r, [
                "модель", "нейросет", "google", "openai", "языковая модель",
                "gpt", "gemini", "qwen", "anthropic", "llm",
            ], "Does NOT reveal AI identity"),
        ],
    )
    result.steps.append(step)

    # === B2: Disease → specific doctor (not vague) ===
    sid = "qa_cl_b2"
    reset(agent, sid)
    r = chat(agent, "У меня панические атаки", sid)
    step = StepResult(
        step_id="B2",
        user_message="У меня панические атаки",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            # Should name a specific doctor
            check_contains(r, [
                "жданов", "шипотько", "лазебный", "тер-исраелян",
            ], "Recommends specific doctor for panic attacks"),
        ],
    )
    result.steps.append(step)

    # === G2: No excessive emotionality ===
    sid = "qa_cl_g2"
    reset(agent, sid)
    r = chat(agent, "Мой ребенок плохо спит, хочу записать его к врачу", sid)
    step = StepResult(
        step_id="G2",
        user_message="Мой ребенок плохо спит",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_contains(r, ["ОБЯЗАТЕЛЬНО", "обязательно поможет"], "No excessive emotionality"),
            check_not_empty(r),
        ],
    )
    result.steps.append(step)

    # === Disease during name collection (case 47) ===
    sid = "qa_cl_47"
    reset(agent, sid)
    chat(agent, "Хочу записаться к Хайретдинову", sid)
    chat(agent, "Да", sid)  # accept slot → asks name
    r = chat(agent, "У меня невротическое расстройство, хочу записаться к врачу", sid)
    step = StepResult(
        step_id="Case47",
        user_message="Disease mention during name collection",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_not_contains(r, ["подобрать", "беспокоит"], "Does NOT restart doctor search"),
            check_contains(r, ["зовут", "имя"], "Re-asks for name"),
        ],
    )
    result.steps.append(step)

    # === H3: end_call signal ===
    sid = "qa_cl_h3"
    reset(agent, sid)
    chat(agent, "Здравствуйте", sid)
    r = chat(agent, "До свидания", sid)
    sig = signal(agent, sid)
    step = StepResult(
        step_id="H3",
        user_message="До свидания",
        bot_response=r,
        call_signal=sig,
        checks=[
            check_signal_equals(sig, "end_call"),
            check_contains(r, ["свидания", "здоровы", "ждём", "всего доброго"], "Farewell message"),
        ],
    )
    result.steps.append(step)

    # === Booking for neighbor (not allowed) ===
    sid = "qa_cl_neighbor"
    reset(agent, sid)
    r = chat(agent, "Хочу записать соседа к врачу, у него панические атаки", sid)
    step = StepResult(
        step_id="Neighbor",
        user_message="Хочу записать соседа к врачу",
        bot_response=r,
        call_signal=signal(agent, sid),
        checks=[
            check_contains(r, [
                "родственник", "родственн", "только себя", "невозможно",
                "передать", "номер", "позвон", "сам",
            ], "Explains booking only for self/relatives"),
        ],
    )
    result.steps.append(step)

    return result


# ── Report generation ────────────────────────────────────────

def format_report(results: list, start_time: datetime) -> str:
    lines = []
    lines.append("=" * 80)
    lines.append("  QA REPORT — Psy Family Chatbot")
    lines.append(f"  Date: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)
    lines.append("")

    total_checks = 0
    total_passed = 0
    total_failed = 0

    for scenario in results:
        sc_checks = scenario.total_checks
        sc_passed = scenario.passed_checks
        sc_failed = sc_checks - sc_passed
        total_checks += sc_checks
        total_passed += sc_passed
        total_failed += sc_failed

        status = "PASSED" if scenario.passed else "FAILED"
        lines.append(f"{'─' * 80}")
        lines.append(f"  {scenario.name}: {scenario.description}")
        lines.append(f"  Status: {status}  ({sc_passed}/{sc_checks} checks passed)")
        lines.append(f"{'─' * 80}")
        lines.append("")

        for step in scenario.steps:
            step_status = "PASS" if step.passed else "FAIL"
            lines.append(f"  [{step.step_id}] {step_status}")
            lines.append(f"  User: {step.user_message}")
            lines.append(f"  Bot:  {step.bot_response[:500]}")
            if step.call_signal:
                lines.append(f"  Signal: {step.call_signal}")
            lines.append("")
            for desc, passed, detail in step.checks:
                mark = "OK" if passed else "FAIL"
                lines.append(f"    [{mark}] {desc}")
                if not passed:
                    lines.append(f"         -> {detail}")
            lines.append("")

    lines.append("=" * 80)
    lines.append(f"  SUMMARY: {total_passed}/{total_checks} checks passed, {total_failed} failed")
    pct = (total_passed / total_checks * 100) if total_checks else 0
    lines.append(f"  Pass rate: {pct:.1f}%")
    lines.append("=" * 80)

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="QA Runner for Psy Family chatbot")
    parser.add_argument("--scenario", type=int, help="Run only scenario N (1, 2, 3, or 4)")
    parser.add_argument("--checklist", action="store_true", help="Run only checklist")
    parser.add_argument("--output", type=str, help="Output file path")
    args = parser.parse_args()

    if not os.getenv("LLM_API_KEY"):
        print("ERROR: LLM_API_KEY env var is required")
        sys.exit(1)

    start_time = datetime.now()

    print("Initializing agent...")
    agent = PsyFamilyAgent()

    scenarios = {
        1: ("Scenario 1: Full booking cycle", run_scenario_1),
        2: ("Scenario 2: Info requests", run_scenario_2),
        3: ("Scenario 3: Booking by symptom + edge cases", run_scenario_3),
        4: ("Scenario 4: Compound messages", run_scenario_4),
    }

    results = []

    if args.checklist:
        print("Running checklist...")
        results.append(run_checklist(agent))
    elif args.scenario:
        name, fn = scenarios[args.scenario]
        print(f"Running {name}...")
        results.append(fn(agent))
    else:
        # Run everything
        for num, (name, fn) in scenarios.items():
            print(f"Running {name}...")
            results.append(fn(agent))
        print("Running checklist...")
        results.append(run_checklist(agent))

    report = format_report(results, start_time)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        ts = start_time.strftime("%Y-%m-%d_%H-%M-%S")
        output_path = os.path.join(os.path.dirname(__file__), "..", f"qa_report_{ts}.txt")

    output_path = os.path.abspath(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    # Also print to stdout
    print(report)
    print(f"\nReport saved to: {output_path}")

    # Exit code
    total_failed = sum(s.total_checks - s.passed_checks for s in results)
    sys.exit(1 if total_failed > 0 else 0)


if __name__ == "__main__":
    main()
