# Сервисы

## 1. LLMService

OpenRouter / OpenAI-совместимый клиент (Chat Completions API).

```python
LLMService(api_key, model, base_url)
```

| Метод | Назначение |
|-------|-----------|
| `extract_structured(messages, schema)` | JSON extraction через `response_format: json_object` (не json_schema — Gemini его не поддерживает) |
| `stream_text(messages)` | Стриминг по токенам (`stream=True`) |
| `one_shot_text(messages)` | Полный ответ за один вызов |

**По умолчанию:** `google/gemini-2.0-flash-001` через OpenRouter.

## 2. DoctorsService

Поиск врачей с **fuzzy matching** (порог 0.55).

```python
DoctorsService(path=DOCTORS_PATH)
```

| Метод | Что делает |
|-------|-----------|
| `find_by_name(name)` | Fuzzy match: "Харединов" → "Хайретдинов" (0.90), "Хайретдинову" → найден (падежи) |
| `find_by_procedure(procedure)` | Substring match по `procedures[]` |
| `find_by_disease(diseases)` | Substring match по `diseases[]` |

Fuzzy matching через `SequenceMatcher` (stdlib). Матчит по:
1. Точное вхождение подстроки
2. Fuzzy по полному имени (порог 0.55)
3. Fuzzy по частям имени (фамилия, имя, отчество отдельно)

## 3. MedeskService (mock)

Управление слотами расписания с **fuzzy date matching**.

```python
MedeskService()
```

| Метод | Что делает |
|-------|-----------|
| `get_available_slots(doctor_name)` | Список слотов по имени |
| `find_matching_or_next(doctor_name, requested_slots)` | Fuzzy match: "6 апреля в 11" → "2026-04-06 11:00" |

Fuzzy matching: извлекает день и час из запроса пациента и сравнивает с доступными слотами.

Mock-данные:
- Хайретдинов: 2026-04-06 10:00, 11:00, 2026-04-07 14:00
- Иванова: 2026-04-08 12:00, 15:00, 2026-04-10 09:30

## 4. FAQService

Keyword matching по FAQ.

```python
FAQService(path=FAQ_PATH)
```

Токенизация вопроса из FAQ, подсчёт совпадений с сообщением пациента. Порог: минимум 1 совпадение. Результат передаётся LLM для переформулировки.
