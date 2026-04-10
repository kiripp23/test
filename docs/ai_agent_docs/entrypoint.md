# Точка входа

Файлы: `agent.py`, `studio.py`, `main.py`

---

## PsyFamilyAgent (`agent.py`)

Главный класс-оркестратор. Связывает LangGraph, сервисы, сессии и стриминг.

### Инициализация

```python
class PsyFamilyAgent:
    def __init__(self, api_key: str = "", model: str = "", base_url: str = ""):
        self.llm = LLMService(api_key=api_key, model=model, base_url=base_url)
        self.doctors_service = DoctorsService()
        self.medesk_service = MedeskService()
        self.faq_service = FAQService()

        self.graph = build_graph(
            llm=self.llm,
            doctors_service=self.doctors_service,
            medesk_service=self.medesk_service,
            faq_service=self.faq_service,
        )

        self.sessions: Dict[str, Dict] = {}
```

Сервисы инжектируются в граф при сборке.

### Сессии

Каждая сессия хранится в `self.sessions[session_id]`:

```python
{
    "history": [],               # Полная история диалога
    "pending_action": None,      # confirm_slot | ask_patient_name | ask_birth_date | None
    "pending_doctor_name": None, # Имя врача в контексте
    "patient_name": None,        # ФИО пациента
    "birth_date": None,          # Дата рождения
    "selected_slot": None,       # Выбранный слот
    "rejected_slots": [],        # Слоты, отклонённые пациентом
    "prev_intent": None,         # Интент предыдущего хода
    "call_signal": None,         # end_call | transfer_operator | None
}
```

### Публичные методы

| Метод | Возвращает | Описание |
|-------|-----------|----------|
| `get_greeting()` | `str` | "Здравствуйте! Клиника Psy Family, чем могу помочь?" |
| `get_call_signal(session_id)` | `Optional[str]` | `"end_call"`, `"transfer_operator"` или `None` |
| `drop_session(session_id)` | `None` | Удаляет сессию из памяти |
| `__call__(message, session_id)` | `Generator[AgentChunk]` | Основной вызов — обработка сообщения |

### Вызов (`__call__`)

```python
def __call__(self, message: str, session_id: str = "default") -> Generator[AgentChunk]:
```

**Шаги:**

1. **Загрузка сессии** - получает или создает session state
2. **Формирование input_state** - собирает AgentState из сессии + текущее сообщение
3. **Выполнение графа** - `self.graph.invoke(input_state)` -> проходит extract -> router -> terminal node
4. **Обновление сессии** - сохраняет pending_action, имена, слоты, prev_intent
5. **Добавление сообщения** - user message -> history
6. **Создание генератора** - в зависимости от результата:

```python
# Вариант 1: Только шаблон
if not need_llm_stream:
    -> single_chunk_stream(template_response)

# Вариант 2: LLM с fallback_messages (fallback нода)
if result.get("fallback_messages"):
    messages = result["fallback_messages"]

# Вариант 3: LLM с llm_prompt (FAQ, doctor_info)
elif result.get("llm_prompt"):
    messages = [system_prompt, llm_prompt]

# Вариант 4: LLM без специального промпта
else:
    messages = [system_prompt, user_message]
```

7. **Сохранение в историю** - после завершения стриминга, полный текст ответа добавляется в session["history"]

### Ленивый стриминг

Ответ возвращается как генератор. Токены LLM начинают отдаваться до того, как полный ответ сгенерирован. Это критично для голосового бота - можно начинать TTS до завершения генерации.

---

## CLI интерфейс (`main.py`)

Минимальная точка входа для тестирования:

```python
def main():
    agent = PsyFamilyAgent()
    while True:
        user_message = input("Пациент: ").strip()
        if user_message.lower() in {"exit", "quit"}:
            break
        print("Ассистент: ", end="", flush=True)
        for chunk in agent(user_message, session_id="demo"):
            print(chunk.text, end="", flush=True)
        print()
```

- Одна сессия (`session_id="demo"`)
- Стриминг в stdout по токенам
- Выход по `exit` / `quit`

---

## LangGraph Studio (`studio.py`)

Точка входа для визуального отладчика LangGraph Studio. Создаёт скомпилированный граф с абсолютными импортами (требование `langgraph dev`).

```python
from ai_agent.graph import build_graph
from ai_agent.services.llm_service import LLMService
# ...

graph = build_graph(
    llm=LLMService(api_key=LLM_API_KEY, model=LLM_MODEL, base_url=LLM_BASE_URL),
    doctors_service=DoctorsService(),
    medesk_service=MedeskService(),
    faq_service=FAQService(),
)
```

Конфигурация в `langgraph.json`:
```json
{
  "dependencies": ["."],
  "graphs": {"agent": "./ai_agent/studio.py:graph"},
  "env": ".env"
}
```

Запуск: `langgraph dev --no-browser --host 0.0.0.0 --port 2024`
