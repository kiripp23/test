"""Structured JSON trace-logger for bot requests.

One JSON line per /chat request, printed to stdout with "TRACE " prefix.
Captures: input message, LLM extraction, state before/after, routed node,
FSM handler, all LLM calls (kind/model/latency/tokens), bot response,
total latency. Grep-able, parseable, zero external deps.
"""
from __future__ import annotations

import contextvars
import json
import sys
import time
from typing import Any, Dict, List, Optional

_current_trace: contextvars.ContextVar[Optional["Trace"]] = contextvars.ContextVar(
    "trace", default=None,
)


class Trace:
    def __init__(self, session_id: str, user_message: str) -> None:
        self.ts: str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.session_id: str = session_id
        self.user_message: str = user_message
        self._start_ns: int = time.monotonic_ns()
        self.extract: Optional[Dict[str, Any]] = None
        self.state_before: Optional[Dict[str, Any]] = None
        self.state_after: Optional[Dict[str, Any]] = None
        self.routed_to: Optional[str] = None
        self.fsm_handler: Optional[str] = None
        self.llm_calls: List[Dict[str, Any]] = []
        self.response_mode: Optional[str] = None
        self.bot_response: Optional[str] = None
        self.error: Optional[str] = None
        self._emitted: bool = False

    def emit(self) -> None:
        if self._emitted:
            return
        self._emitted = True
        total_latency_ms = (time.monotonic_ns() - self._start_ns) // 1_000_000
        payload = {
            "ts": self.ts,
            "session_id": self.session_id,
            "user_message": self.user_message,
            "extract": self.extract,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "routed_to": self.routed_to,
            "fsm_handler": self.fsm_handler,
            "llm_calls": self.llm_calls,
            "response_mode": self.response_mode,
            "bot_response": (self.bot_response or "")[:500],
            "total_latency_ms": total_latency_ms,
            "error": self.error,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        # stdout flush=True so docker logs pick it up immediately
        print(f"TRACE {line}", flush=True, file=sys.stdout)


def start_trace(session_id: str, user_message: str) -> Trace:
    t = Trace(session_id, user_message)
    _current_trace.set(t)
    return t


def current_trace() -> Optional[Trace]:
    return _current_trace.get()


def record_llm_call(
    kind: str,
    model: str,
    latency_ms: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    t = current_trace()
    if t is None:
        return
    t.llm_calls.append(
        {
            "kind": kind,
            "model": model,
            "latency_ms": latency_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
    )


def record_fsm_handler(name: str) -> None:
    t = current_trace()
    if t is not None and t.fsm_handler is None:
        t.fsm_handler = name


def _snapshot_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the interesting slice of AgentState for trace."""
    return {
        "pending_action": state.get("pending_action"),
        "pending_doctor_name": state.get("pending_doctor_name"),
        "selected_slot": state.get("selected_slot"),
        "rejected_slots": state.get("rejected_slots") or [],
        "patient_name": state.get("patient_name"),
        "birth_date": state.get("birth_date"),
        "booking_completed": state.get("booking_completed", False),
        "tried_doctors": state.get("tried_doctors") or [],
    }


def record_state_before(state: Dict[str, Any]) -> None:
    t = current_trace()
    if t is not None:
        t.state_before = _snapshot_state(state)


def record_state_after(state: Dict[str, Any]) -> None:
    t = current_trace()
    if t is not None:
        t.state_after = _snapshot_state(state)
        t.routed_to = state.get("final_route")
        extracted = state.get("extracted")
        if extracted is not None:
            t.extract = {
                "intent": getattr(extracted, "patient_intent", None),
                "secondary_intent": getattr(extracted, "secondary_intent", None),
                "message_category": getattr(extracted, "message_category", None),
                "doctor_name": getattr(extracted, "doctor_name", None),
                "patient_slots": list(getattr(extracted, "patient_slots", []) or []),
                "procedure": getattr(extracted, "procedure", None),
                "diseases": list(getattr(extracted, "diseases", []) or []),
                "patient_name": getattr(extracted, "patient_name", None),
                "birth_date": getattr(extracted, "birth_date", None),
                "doctor_info": getattr(extracted, "doctor_info", None),
                "additional_questions": list(
                    getattr(extracted, "additional_questions", []) or []
                ),
            }


def record_response(mode: str, text: str) -> None:
    t = current_trace()
    if t is not None:
        t.response_mode = mode
        t.bot_response = text


def record_error(msg: str) -> None:
    t = current_trace()
    if t is not None:
        t.error = msg
