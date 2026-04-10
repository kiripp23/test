# Архитектура AI-агента Psy Family

## Обзор

Голосовой бот клиники Psy Family построен на LangGraph — фреймворке для state-machine графов. Агент обрабатывает телефонные звонки пациентов: запись к врачу, ответы на вопросы, информация о специалистах.

## Граф обработки

```
[user_message] → extract → router → node → [response]
                                      ↓
                              ┌───────┼───────┬──────────┐
                              │       │       │          │
                         appointment doctor_info faq  fallback
                              │       │       │          │
                              └───────┴───────┴──────────┘
                                          ↓
                                         END
```

## Двухуровневая классификация

Каждое сообщение пациента классифицируется по двум осям:

### 1. patient_intent (что хочет пациент)

12 интентов:

| Интент | Маршрут | Описание |
|--------|---------|----------|
| `make_appoint` | appointment | Хочет записаться к врачу |
| `asks_about_slot` | appointment | Спрашивает про свободное время |
| `chosen_slot` | appointment | Соглашается на предложенный слот |
| `slot_not_suitable` | appointment | Предложенное время не подходит |
| `doctor_ok` | appointment | Соглашается на предложенного врача |
| `doctor_not_ok` | appointment | Не хочет к предложенному врачу |
| `want_procedure` | doctor_info | Ищет врача по процедуре/заболеванию |
| `question_about_doctor` | doctor_info | Вопрос о враче (стаж, часы и т.п.) |
| `question_about_clinic` | faq | Вопрос о клинике |
| `wants_operator` | signal | Просит оператора |
| `end_conversation` | signal | Прощается |
| `arbitrary_message` | по категории | Всё остальное |

### 2. message_category (контекст сообщения)

5 категорий — определяет стратегию для `arbitrary_message`:

| Категория | Маршрут | Стратегия |
|-----------|---------|-----------|
| `clinic_related` | faq | FAQ-контекст + LLM |
| `off_topic` | fallback | Шаблонный мягкий отказ |
| `emotional` | fallback | Эмпатичный LLM-ответ |
| `greeting` | fallback | Шаблонное приветствие |
| `gratitude` | fallback | Шаблонная благодарность |

## Ноды

### extract

Файл: `ai_agent/nodes/extract.py`

Извлекает структурированные данные из сообщения пациента через LLM (JSON mode). Определяет intent, category и сущности (имя врача, дата, процедура и т.д.).

### router

Файл: `ai_agent/nodes/router.py`

Трёхэтапная маршрутизация:

1. **Сигналы** (`_handle_signals`) — `wants_operator` и `end_conversation` обрабатываются немедленно
2. **Pending state** (`_resolve_pending_route`) — если бот ждёт ответа (имя, дата рождения, подтверждение слота), приоритет у сбора данных. Вопросы пациента обрабатываются параллельно (переключение контекста)
3. **Intent + Category** (`_resolve_intent_route`) — чистая маршрутизация по интенту и категории

### appointment

Файл: `ai_agent/nodes/appointment.py`

Многошаговый form-filling:
- Поиск врача → предложение слота → подтверждение → сбор имени → сбор даты рождения → запись
- Поддержка отклонения слотов, предложение альтернативных врачей
- Inline fallback при off-topic во время сбора данных

### doctor_info

Файл: `ai_agent/nodes/doctor_info.py`

Два режима:
- `want_procedure` — поиск врача по заболеванию/процедуре
- `question_about_doctor` — ответы на вопросы о конкретном враче

### faq

Файл: `ai_agent/nodes/faq.py`

Двухуровневый FAQ-матчинг:
1. **Стем-матчинг с синонимами** — быстрый поиск по ключевым словам с расширением синонимов
2. **LLM с FAQ-контекстом** — если стем-матчинг не нашёл совпадение, LLM получает полный список FAQ

### fallback

Файл: `ai_agent/nodes/fallback.py`

Категориальный fallback:
- `greeting` → шаблон "Здравствуйте! Клиника Psy Family..."
- `gratitude` → шаблон "Пожалуйста! Могу ещё чем-то помочь?"
- `off_topic` → шаблон "Я могу помочь с записью к врачу..."
- `emotional` → LLM с эмпатичным системным промптом
- `clinic_related` → LLM с контекстом клиники

## FAQ-сервис

Файл: `ai_agent/services/faq_service.py`

### Стем-матчинг

Наивный русский стеммер отсекает типичные окончания. Карта синонимов расширяет запрос:

```
"доехать" → ["адрес", "находится"]
"график"  → ["режим", "работа"]
"цена"    → ["стоимость", "цены"]
"онлайн"  → ["формат", "видео"]
"пнд"     → ["анонимно", "конфиденциальность"]
```

### Пороги

- `score >= 2` — обязательно минимум 2 совпавших стема
- `score >= 1 && len(faq_tokens) <= 3` — для коротких FAQ достаточно 1

## Сервисы

| Сервис | Файл | Описание |
|--------|------|----------|
| LLMService | `services/llm_service.py` | OpenAI-совместимый клиент (OpenRouter) |
| DoctorsService | `services/doctors_service.py` | Поиск врачей: по имени (fuzzy), процедуре, заболеванию |
| MedeskService | `services/medesk_service.py` | Mock-сервис слотов (замена на Medesk API) |
| FAQService | `services/faq_service.py` | Стем + синонимы + LLM fallback |

## Session State

Состояние диалога хранится в памяти между запросами:

```python
pending_action      # текущее ожидание: ask_patient_name, ask_birth_date, confirm_slot, confirm_alt_doctor
pending_doctor_name # текущий обсуждаемый врач
patient_name        # имя пациента (если уже назвал)
birth_date          # дата рождения
selected_slot       # выбранный слот
rejected_slots      # отклонённые слоты (не предлагаются повторно)
tried_doctors       # предложенные альтернативные врачи
prev_intent         # интент предыдущего сообщения
message_category    # категория текущего сообщения
call_signal         # end_call | transfer_operator | None
```

## Тестирование

### Юнит-тесты (`tests/test_cases.py`)

170+ тестов. Мокают LLM-извлечение, тестируют routing + node логику:
- Все основные сценарии записи
- Переключение контекста
- Категориальный fallback
- FAQ-синонимы
- Сигналы (оператор, прощание)

### Интеграционные тесты (`tests/test_integration.py`)

25+ тестов через реальный LLM. Требуют `LLM_API_KEY`:
```bash
pytest -m integration -v
```
