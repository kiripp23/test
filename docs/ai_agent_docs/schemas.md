# Схемы данных

## Интенты пациента (PatientIntent)

12 возможных интентов:

| Интент | Описание | Маршрут |
|--------|----------|---------|
| `make_appoint` | Хочет записаться к врачу | appointment |
| `asks_about_slot` | Спрашивает, есть ли окно | appointment |
| `chosen_slot` | Согласен на предложенный слот | appointment |
| `slot_not_suitable` | Предложенное время не подходит | appointment |
| `doctor_ok` | Согласен с врачом | (не используется) |
| `doctor_not_ok` | Не согласен с врачом | (не используется) |
| `want_procedure` | Ищет врача по процедуре/заболеванию | doctor_info |
| `question_about_doctor` | Вопрос про врача (часы, стаж, стоимость) | doctor_info |
| `question_about_clinic` | Вопрос о клинике | faq |
| `wants_operator` | Просит оператора/человека | router → transfer + hangup |
| `end_conversation` | Прощается | router → goodbye + hangup |
| `arbitrary_message` | Ничего не подошло | fallback |

## DoctorInfoType

`"visiting_hours" | "experience" | "specialization" | "cost"`

## ExtractedMessage

```python
class ExtractedMessage(BaseModel):
    patient_intent: PatientIntent = "arbitrary_message"
    secondary_intent: Optional[PatientIntent] = None  # второй интент, если в сообщении два намерения
    message_category: MessageCategory = "clinic_related"
    doctor_name: Optional[str] = None
    patient_slots: List[str] = []
    procedure: Optional[str] = None
    diseases: List[str] = []
    patient_name: Optional[str] = None
    birth_date: Optional[str] = None
    doctor_info: Optional[DoctorInfoType] = None
```

## DoctorRecord

```python
class DoctorRecord(BaseModel):
    name: str
    visiting_hours: str
    experience: str
    procedures: List[str]
    diseases: List[str]
    info: str
    cost: str = ""
```

## AgentState

```python
class AgentState(TypedDict, total=False):
    user_message: str
    messages: List[Any]             # совместимость с LangGraph Studio
    history: List[MessageItem]
    extracted: ExtractedMessage
    prev_intent: str
    message_category: str           # clinic_related | off_topic | emotional | greeting | gratitude

    matched_doctor: Optional[Dict]
    faq_match: Optional[Dict]

    available_slots: List[str]
    selected_slot: Optional[str]
    rejected_slots: List[str]       # слоты, отклонённые пациентом

    pending_action: Optional[str]   # ask_patient_name | ask_birth_date | confirm_slot | confirm_alt_doctor | None
    pending_doctor_name: Optional[str]
    tried_doctors: List[str]        # врачи, уже предложенные пациенту

    patient_name: Optional[str]
    birth_date: Optional[str]

    response_mode: str              # "template" | "llm" | "template+llm"
    template_response: Optional[str]
    need_llm_stream: bool
    llm_prompt: Optional[str]
    final_route: Optional[str]
    call_signal: Optional[str]      # end_call | transfer_operator | None
    booking_completed: bool         # True после успешной записи — меняет CTA в промптах
    fallback_messages: Optional[list]
```

## MessageCategory

`"clinic_related" | "off_topic" | "emotional" | "greeting" | "gratitude"`

Двухуровневая классификация: `patient_intent` определяет маршрут, `message_category` — стратегию ответа для `arbitrary_message` (шаблон/LLM/эмпатия).
