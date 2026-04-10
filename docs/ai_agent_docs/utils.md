# Утилиты

Файлы: `utils/prompts.py`, `utils/streaming.py`, `utils/format.py`

---

## Промпты (`utils/prompts.py`)

### EXTRACTION_SYSTEM_PROMPT

Системный промпт для ноды `extract`. Инструктирует LLM извлекать structured JSON из последнего сообщения пациента.

**Ключевые правила:**
- `chosen_slot` - пациент явно соглашается на слот или сам выбирает дату/время
- `slot_not_suitable` - пациент говорит, что предложенное время не подходит
- `want_procedure` - хочет процедуру или ищет врача по заболеванию
- `question_about_doctor` - спрашивает про часы/опыт/специализацию
- `question_about_clinic` - вопрос о клинике
- `make_appoint` - хочет записаться
- `asks_about_slot` - спрашивает о наличии окна
- `arbitrary_message` - если ничего не подошло
- Неполные ФИО все равно извлекаются
- Даты/время из текста добавляются в `patient_slots`

### FALLBACK_SYSTEM_PROMPT

```
Ты — оператор клиники Psy Family.
Отвечай вежливо, кратко, по делу, на русском языке.
Если пациент спрашивает о записи, направь его к уточнению врача, даты или услуги.
Используй историю диалога.
```

Используется в: `fallback_node`, `agent.py` (для FAQ и LLM ответов).

### DOCTOR_INFO_SUMMARY_PROMPT

```
Сделай очень краткое и дружелюбное описание врача для пациента
на основе следующей информации: {info}
```

Используется в: `doctor_info_node` (intent=want_procedure).

### FAQ_ANSWER_PROMPT

```
Ты — оператор клиники Psy Family.
Ответь пациенту на основе FAQ.

Вопрос пациента: {user_question}
Найденный FAQ:
Вопрос: {faq_question}
Ответ: {faq_answer}

Сформулируй краткий и естественный ответ на русском языке.
```

Используется в: `faq_node`.

---

## Стриминг (`utils/streaming.py`)

### `single_chunk_stream(text) -> Generator[AgentChunk]`

Оборачивает строку в единственный `AgentChunk(type="template")`:

```python
def single_chunk_stream(text: str) -> Generator[AgentChunk, None, None]:
    yield AgentChunk(text=text, type="template")
```

Используется для чисто шаблонных ответов (appointment, doctor_info/question_about_doctor).

### `prepend_chunk_and_stream(template_text, llm_stream)`

Комбинирует шаблонный префикс с LLM-стримом:

```python
def prepend_chunk_and_stream(template_text, llm_stream):
    yield AgentChunk(text=template_text, type="template")   # Сначала шаблон
    for chunk in llm_stream:
        if chunk:
            yield AgentChunk(text=chunk, type="llm")         # Потом LLM-токены
```

Используется для doctor_info/want_procedure: сначала "ЭПИ можно пройти у Хайретдинова.", потом LLM-описание врача.

---

## Форматирование (`utils/format.py`)

### `format_slot(slot: str) -> str`

Преобразует ISO-слот в естественную русскую форму:

```python
format_slot("2026-04-06 10:00")
# → "шестого апреля в десять утра"
```

- Русские порядковые числительные для дней (1-31)
- Месяцы в родительном падеже (январь → января, и т.д.)
- Часы прописью на русском
- Поддержка минут: 0, 10, 15, 20, 30, 40, 45, 50
- Fallback на оригинальную строку при ошибке парсинга

### `decline_name(full_name: str, case: str = "genitive") -> str`

Склоняет русские ФИО в родительный или дательный падеж:

```python
decline_name("Хайретдинов Олег Замильевич")
# → "Хайретдинова Олега Замильевича"

decline_name("Хайретдинов Олег Замильевич", case="dative")
# → "Хайретдинову Олегу Замильевичу"
```

Поддерживаемые окончания: `-ов/-ев`, `-ин`, `-й/-ь`, `-ич`, согласные.
