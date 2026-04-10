# Ноды графа

Файлы: `nodes/extract.py`, `nodes/router.py`, `nodes/appointment.py`, `nodes/doctor_info.py`, `nodes/faq.py`, `nodes/fallback.py`

---

## 1. Extract (`nodes/extract.py`)

**Назначение:** Извлечение интента и сущностей из сообщения пациента через LLM.

**Логика:**
1. Формирует массив сообщений: `[system_prompt] + history[-2:] + user_message`
2. Вызывает `llm.extract_structured()` с Pydantic-схемой `ExtractedMessage`
3. Сохраняет результат в `state["extracted"]`
4. Если извлечены `patient_name` или `birth_date` - сохраняет в state (персистентно)

**Контекстное окно:** Берет только **2 последних** сообщения из истории для экономии токенов.

```python
messages = [{"role": "system", "content": EXTRACTION_SYSTEM_PROMPT}]
messages += state.get("history", [])[-2:]
messages.append({"role": "user", "content": state["user_message"]})

extracted = llm.extract_structured(messages=messages, schema=ExtractedMessage)
```

---

## 2. Router (`nodes/router.py`)

**Назначение:** Определение маршрута (какая терминальная нода обработает запрос).

**Правила маршрутизации (в порядке приоритета):**

```python
# 1. Безусловные сигналы (обрабатываются в router, маршрут → fallback):
wants_operator   -> call_signal="transfer_operator", template="Переключаю вас на оператора..." -> "fallback"
end_conversation -> call_signal="end_call", template="Спасибо за звонок! Будьте здоровы..." -> "fallback"

# 2. Активный pending_action (приоритет над интентом):
pending_action in ("ask_patient_name", "ask_birth_date", "confirm_slot", "confirm_alt_doctor") -> "appointment"

# 2a. Мультиинтент при confirm_slot (кейс 44):
#     "Да, подходит. Расскажите про него." → chosen_slot + question_about_doctor
#     Слот подтверждается (pending → ask_patient_name), вопрос маршрутизируется в doctor_info
intent == "chosen_slot" + secondary_intent == "question_about_doctor" -> pending="ask_patient_name", route="doctor_info"

# 3. Прямая маршрутизация по интенту:
make_appoint, asks_about_slot, chosen_slot, slot_not_suitable -> "appointment"
want_procedure, question_about_doctor                         -> "doctor_info"
question_about_clinic                                         -> "faq"
* (все остальное)                                             -> "fallback"

# 4. Специальное правило контекстного продолжения:
prev_intent == "chosen_slot" AND intent == "arbitrary_message" -> "appointment"
# (пациент отвечает на вопрос в рамках процесса записи, напр. называет ФИО)
```

**Мутация интента:** При контекстном продолжении router переписывает intent на `"chosen_slot"`:
```python
state["extracted"].patient_intent = "chosen_slot"
```

---

## 3. Appointment (`nodes/appointment.py`)

**Назначение:** Многоходовой процесс записи на прием.

### Дерево решений

```
appointment_node
 |
 +-- Нет имени врача
 |     -> "Подскажите, к какому врачу?"
 |
 +-- Врач не найден
 |     -> "Не удалось найти врача {name}"
 |
 +-- intent: make_appoint / asks_about_slot
 |     |
 |     +-- Есть patient_slots (пациент указал время)
 |     |     |
 |     |     +-- Совпадение найдено -> "Свободно в {slot}, делаем запись?"
 |     |     +-- Нет совпадения, есть альтернатива -> "На {requested} занято, предлагаем {alt}"
 |     |     +-- Нет слотов -> "Нет свободных слотов"
 |     |
 |     +-- Нет patient_slots (без указания времени)
 |           |
 |           +-- Есть слоты -> "Свободно в {first_slot}, делаем запись?"
 |           +-- Нет слотов -> "Нет свободных слотов"
 |
 +-- intent: slot_not_suitable
 |     +-- Есть альтернатива -> "Предлагаем {next_slot}"
 |     +-- Нет альтернатив -> "Других слотов нет"
 |
 +-- intent: chosen_slot
       |
       +-- Нет patient_name -> "Подскажите ваши ФИО"
       +-- Нет birth_date   -> "Подскажите дату рождения"
       +-- Все данные есть  -> "Вы записаны на {slot} к {doctor}"
```

### Управление pending_action

| Значение | Когда устанавливается | Что ожидаем |
|----------|----------------------|-------------|
| `"confirm_slot"` | Предложили слот | Согласие/отказ пациента |
| `"ask_patient_name"` | Слот подтвержден, нет ФИО | ФИО пациента |
| `"ask_birth_date"` | Есть ФИО, нет даты рождения | Дата рождения |
| `None` | Запись завершена | - |

**Все ответы appointment - только template** (`need_llm_stream = False`). LLM не используется для генерации текста записи.

---

## 4. Doctor Info (`nodes/doctor_info.py`)

**Назначение:** Информация о врачах и подбор врача по процедуре/заболеванию.

### intent: want_procedure

1. Поиск врача по `extracted.procedure`
2. Если не найден - поиск по `extracted.diseases`
3. Если найден:
   - Template: `"{procedure} можно пройти у {doctor.name}. "`
   - LLM stream: краткое описание врача по `DOCTOR_INFO_SUMMARY_PROMPT`
4. Если не найден:
   - Template: "Не удалось подобрать врача"

**Единственная нода, использующая комбинацию template + LLM stream.**

### intent: question_about_doctor

Ответ зависит от `extracted.doctor_info`:

| doctor_info | Ответ |
|-------------|-------|
| `visiting_hours` | "Часы приема: {hours}" |
| `experience` | "Стаж: {experience}" |
| `specialization` | LLM stream по `DOCTOR_INFO_SUMMARY_PROMPT` |
| `cost` | "К сожалению, я не располагаю такой информацией. Уточните у администратора." |
| `None` / другое | LLM stream по `DOCTOR_INFO_SUMMARY_PROMPT` |

CTA в конце ответа зависит от `pending_action` — динамический через `_cta(state)`.

### Чередование CTA (кейс 45)

Фраза «Записать вас на приём?» не повторяется дословно. Список `_BOOKING_CTA_VARIANTS`:
- «Записать вас на приём?»
- «Мне забронировать для вас окошко?»
- «Желаете закрепить за собой дату и время?»
- «Когда вам удобно подойти?»

Выбирается случайно через `random.choice`. При `confirm_slot` с выбранным слотом CTA содержит конкретное время.

---

## 5. FAQ (`nodes/faq.py`)

**Назначение:** Ответы на вопросы о клинике.

**Логика:**
1. `faq_service.match(user_message)` - keyword matching
2. Если совпадение не найдено - перенаправляет на fallback (`state["final_route"] = "fallback"`)
3. Если найдено - формирует `llm_prompt` из `FAQ_ANSWER_PROMPT` для естественной переформулировки ответа

```python
state["llm_prompt"] = FAQ_ANSWER_PROMPT.format(
    user_question=state["user_message"],
    faq_question=match.question,
    faq_answer=match.answer,
)
state["need_llm_stream"] = True
```

**Ответ: только LLM stream** (template_response = "").

---

## 6. Fallback (`nodes/fallback.py`)

**Назначение:** Обработка сообщений, не попавших в другие ноды.

**Логика:**
1. Берет последние **6 сообщений** из истории
2. Формирует массив: `[FALLBACK_SYSTEM_PROMPT] + history + user_message`
3. Передает как `fallback_messages` для полноценного LLM-стриминга

```python
conversation = [{"role": "system", "content": FALLBACK_SYSTEM_PROMPT}]
for msg in history:
    conversation.append({"role": msg["role"], "content": msg["content"]})
conversation.append({"role": "user", "content": state["user_message"]})
```

**Ответ: только LLM stream** (template_response = None).

Использует больший контекст (6 сообщений vs 2 у extract) для более связных ответов.
