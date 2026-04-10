from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator


PatientIntent = Literal[
    "make_appoint",
    "asks_about_slot",
    "want_procedure",
    "chosen_slot",
    "slot_not_suitable",
    "doctor_ok",
    "doctor_not_ok",
    "question_about_doctor",
    "question_about_clinic",
    "wants_operator",
    "end_conversation",
    "arbitrary_message",
]

MessageCategory = Literal[
    "clinic_related",
    "off_topic",
    "emotional",
    "greeting",
    "gratitude",
]

DoctorInfoType = Literal["visiting_hours", "experience", "specialization", "cost"]


class ExtractedMessage(BaseModel):
    patient_intent: PatientIntent = "arbitrary_message"
    secondary_intent: Optional[PatientIntent] = None
    message_category: MessageCategory = "clinic_related"
    doctor_name: Optional[str] = None
    patient_slots: List[str] = Field(default_factory=list)
    procedure: Optional[str] = None
    diseases: List[str] = Field(default_factory=list)
    patient_name: Optional[str] = None
    birth_date: Optional[str] = None
    doctor_info: Optional[DoctorInfoType] = None
    additional_questions: List[str] = Field(default_factory=list)  # up to 3 extra questions


class DoctorRecord(BaseModel):
    name: str
    experience: str = ""
    procedures: List[str] = Field(default_factory=list)
    diseases: List[str] = Field(default_factory=list)
    patients_category: List[str] = Field(default_factory=list)
    specialization: List[str] = Field(default_factory=list)
    appointment: List[str] = Field(default_factory=list)
    short_info: str = ""
    detailed_info: str = ""
    visiting_hours: str = ""
    info: str = ""
    cost: Union[str, Dict[str, Any]] = ""

    @model_validator(mode="after")
    def _fill_info(self) -> "DoctorRecord":
        if not self.info:
            self.info = self.detailed_info or self.short_info
        return self

    def cost_summary(self) -> str:
        """Human-readable cost line. Handles both str and dict forms."""
        c = self.cost
        if isinstance(c, str):
            return f"{c} руб." if c else ""
        if isinstance(c, dict):
            parts: List[str] = []
            for place, val in c.items():
                label = "в клинике" if place == "clinic" else "на дому" if place == "home" else place
                if isinstance(val, str):
                    parts.append(f"{label} — {val} руб.")
                elif isinstance(val, dict):
                    sub = ", ".join(f"{k} {v} руб." for k, v in val.items())
                    parts.append(f"{label}: {sub}")
            return "; ".join(parts)
        return ""


class FAQRecord(BaseModel):
    question: str
    answer: str


class SlotOption(BaseModel):
    doctor_name: str
    slot: str


class AgentChunk(BaseModel):
    text: str
    type: Literal["template", "llm"] = "template"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    call_signal: Optional[str] = None


class SessionResetRequest(BaseModel):
    session_id: str = "default"


class GreetingResponse(BaseModel):
    greeting: str