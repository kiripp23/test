# Тестирование AI-агента

Для тестирования диалоговой логики без живых звонков есть три компонента:
- **Автоматические тесты** (`tests/`) — pytest, 233 unit + 58 integration тестов → [подробная документация](ai_agent_docs/testing.md)
- **FastAPI HTTP API** (`ai_agent/app.py`) — REST-обёртка над `PsyFamilyAgent`
- **Telegram тест-бот** (`ai_agent/tg_test_bot.py`) — интерфейс для ручного тестирования через Telegram

## Быстрый старт: автотесты

```bash
# Детерминированные тесты (без LLM API, < 2с)
pytest tests/test_cases.py -v

# Интеграционные тесты (нужен LLM_API_KEY, ~3-15 мин)
source .env && pytest tests/test_integration.py -v
```

Подробнее: [Автоматическое тестирование AI-агента](ai_agent_docs/testing.md)

---

## Архитектура тестового стенда

```
Telegram → tg_test_bot → POST /chat → ai-agent-api → PsyFamilyAgent → LLM
                                                          ↓
Голосовой бот → POST /chat_stream → ai-agent-api → PsyFamilyAgent → LLM (streaming)
```

---

## HTTP API (`ai_agent/app.py`)

**Версия:** определяется в `ai_agent/version.py` (текущая: `0.2.4`)

### Эндпоинты

| Метод | URL | Назначение |
|-------|-----|-----------|
| `POST` | `/chat_stream` | Стриминг текста (`text/plain`) — для интеграции с голосовым ботом / TTS |
| `POST` | `/chat` | Полный JSON-ответ — для Telegram и ручных тестов |
| `GET` | `/greeting` | Текст приветствия агента |
| `POST` | `/session/reset` | Сброс состояния сессии |
| `GET` | `/health` | Healthcheck (`{"status": "ok", "version": "..."}`) |

### Схемы запросов/ответов

**ChatRequest** (для `/chat` и `/chat_stream`):
```json
{
  "message": "Хочу записаться к врачу",
  "session_id": "default"
}
```

**ChatResponse** (ответ `/chat`):
```json
{
  "reply": "К какому врачу вы хотите записаться?",
  "call_signal": null
}
```
`call_signal`: `"end_call"` | `"transfer_operator"` | `null`

**GreetingResponse** (ответ `/greeting`):
```json
{
  "greeting": "Здравствуйте! Клиника Psy Family, чем могу помочь?"
}
```

**SessionResetRequest** (для `/session/reset`):
```json
{
  "session_id": "default"
}
```

### Пример вызова

```bash
# Полный ответ
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Хочу записаться к Хайретдинову", "session_id": "test1"}'

# Стриминг
curl -X POST http://localhost:8000/chat_stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Расскажите про ЭПИ", "session_id": "test1"}'

# Сброс сессии
curl -X POST http://localhost:8000/session/reset \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test1"}'
```

---

## Telegram тест-бот (`ai_agent/tg_test_bot.py`)

Бот на **aiogram**, отправляет сообщения в HTTP API и возвращает ответ в чат.

### Команды

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и инструкция |
| `/chat` | Пробный запрос ("Здравствуйте") |
| `/reset` | Сброс контекста диалога |
| `/help` | Справка по использованию |
| `/version` | Версия бота и URL API |

Обычный текст (без `/`) отправляется в агент как сообщение пациента.

Если агент возвращает `call_signal`, он отображается в конце ответа: `[signal: end_call]`.

### Переменные окружения

| Переменная | Описание | Значение по умолчанию |
|-----------|----------|----------------------|
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather | (обязательно) |
| `AI_AGENT_API_URL` | URL API агента | `http://127.0.0.1:8000` |

---

## Docker

### Файлы

- `Dockerfile` — Python 3.12 + ffmpeg, единый образ для всех сервисов
- `docker-compose.yml` — три сервиса

### Сервисы

| Сервис | Команда | Порт | Описание |
|--------|---------|------|----------|
| `ai-agent-api` | `uvicorn ai_agent.app:app` | 8000 | FastAPI HTTP API |
| `tg-test-bot` | `python -m ai_agent.tg_test_bot` | — | Telegram бот, ходит в API |
| `medbot` | `python run.py` | host network | Голосовой бот (profile `voice`) |

### Запуск

```bash
# API + Telegram бот (тестирование)
docker compose up -d --build ai-agent-api tg-test-bot

# Только API
docker compose up -d --build ai-agent-api

# Голосовой бот (нужен Asterisk на хосте)
docker compose --profile voice up -d --build medbot

# Логи
docker compose logs -f ai-agent-api
docker compose logs -f tg-test-bot
```

### Переменные окружения

Все переменные читаются из `.env` (см. `.env.example`). Для тестирования через API и Telegram обязательны:

| Переменная | Для чего |
|-----------|----------|
| `LLM_API_KEY` | Ключ OpenRouter для LLM |
| `LLM_MODEL` | Модель (по умолчанию `google/gemini-2.0-flash-001`) |
| `LLM_BASE_URL` | URL OpenRouter API |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота (только для `tg-test-bot`) |

---

## Локальный запуск (без Docker)

```bash
# API
source venv/bin/activate
uvicorn ai_agent.app:app --host 0.0.0.0 --port 8000

# Telegram бот (в отдельном терминале)
python -m ai_agent.tg_test_bot
```

---

## LangGraph Studio

Визуальный отладчик для интерактивной работы с графом агента.

### Настройка

1. Убедитесь, что `LANGSMITH_API_KEY` заполнен в `.env`
2. Установите зависимости:
```bash
pip install -U "langgraph-cli[inmem]"
pip install -e .
```

3. Запустите dev-сервер:
```bash
source venv/bin/activate && set -a && source .env && set +a
langgraph dev --no-browser --host 0.0.0.0 --port 2024
```

4. Откройте Studio: `https://smith.langchain.com/studio/?baseUrl=http://<IP_сервера>:2024`

### Конфигурация

Файл `langgraph.json` в корне проекта:
```json
{
  "dependencies": ["."],
  "graphs": {
    "agent": "./ai_agent/studio.py:graph"
  },
  "env": ".env"
}
```

Точка входа для Studio — `ai_agent/studio.py` (отдельно от FastAPI `app.py`).

### LangSmith трейсинг

Переменные в `.env`:
```
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=medtech-agent
LANGSMITH_TRACING=true
```

Трейсы каждого вызова графа автоматически отправляются в [smith.langchain.com](https://smith.langchain.com) → проект **medtech-agent**. Для отключения: `LANGSMITH_TRACING=false`.

### Генерация PNG-схемы графа

```bash
python -c "
from ai_agent.graph import build_graph
from ai_agent.services.doctors_service import DoctorsService
from ai_agent.services.medesk_service import MedeskService
from ai_agent.services.faq_service import FAQService

class FakeLLM:
    def extract_structured(self, *a, **kw): pass
    def stream_text(self, *a, **kw): return iter([])

g = build_graph(FakeLLM(), DoctorsService(), MedeskService(), FAQService())
open('docs/graph.png','wb').write(g.get_graph().draw_png())
"
```

Требует `pygraphviz` (`pip install pygraphviz`, `apt install graphviz libgraphviz-dev`).
