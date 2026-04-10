# MedTech Voice Bot

Голосовой ИИ-ассистент для психиатрической клиники Psy Family. Принимает входящие звонки через Asterisk PBX, ведёт диалог с пациентом, записывает на приём к врачу.

## Возможности

- Приветствие и ведение естественного диалога на русском языке
- Подбор врача по заболеванию/процедуре с fuzzy-матчингом и ранжированием по опыту
- Запись к врачу: проверка расписания, бронирование слотов, сбор данных (ФИО, дата рождения)
- Информация о врачах и клинике (стаж, стоимость, часы приёма)
- Обработка составных запросов (до 3 вопросов в одном сообщении)
- Гибкий flow записи: бот отвечает на попутные вопросы и возвращается к сбору данных
- Перевод на живого оператора по запросу
- Распознавание речи (STT) и синтез речи (TTS) в реальном времени

## Архитектура

```
Пациент ---(SIP/RTP)---> Asterisk PBX ---(ARI + RTP)---> MedBot (Python)
                                                             |
                                          +------------------+------------------+
                                          |                  |                  |
                                     Groq Whisper      OpenRouter LLM     ElevenLabs TTS
                                       (STT)         (qwen3-coder)          (Voice)
                                          |                  |
                                          |            Medesk CRM
                                          |           (запись к врачу)
```

### AI Agent Pipeline (LangGraph)

```
extract → router → [appointment | doctor_info | faq | fallback] → END
```

- **extract** — LLM извлекает intent, имя врача, заболевания, данные пациента
- **router** — маршрутизация по intent + pending_action (сбор данных для записи)
- **appointment** — бронирование слотов, сбор ФИО и даты рождения
- **doctor_info** — подбор врача по заболеванию, fuzzy disease matching, ранжирование
- **faq** — ответы на вопросы о клинике из FAQ
- **fallback** — приветствия, эмоциональные сообщения, off-topic

## Стек технологий

| Компонент | Технология | Назначение |
|-----------|-----------|------------|
| PBX | Asterisk 21.12 | SIP-телефония, маршрутизация звонков |
| SIP-провайдер | Mango Office | Входящие/исходящие звонки |
| Бот | Python 3.12 + asyncio | Управление звонком через ARI |
| AI Agent | LangGraph + FastAPI | Пайплайн обработки сообщений |
| STT | Groq Whisper large-v3 | Распознавание речи (русский) |
| LLM | qwen3-coder (OpenRouter) | Генерация ответов |
| TTS | ElevenLabs Flash v2.5 | Синтез речи (русский) |
| CRM | Medesk | Расписание врачей, запись (stub) |
| Eval | TypeScript + GPT-5.4 (judge) | Автоматическое тестирование качества |

## Структура проекта

```
medtech/
├── ai_agent/                # AI Agent (LangGraph pipeline)
│   ├── agent.py             # PsyFamilyAgent — главный класс
│   ├── app.py               # FastAPI HTTP API (/chat, /chat_stream)
│   ├── graph.py             # LangGraph StateGraph
│   ├── state.py             # AgentState TypedDict
│   ├── schemas.py           # Pydantic-схемы (intents, extraction)
│   ├── config.py            # Конфигурация из .env
│   ├── nodes/
│   │   ├── extract.py       # Извлечение intent + данных из сообщения
│   │   ├── router.py        # Маршрутизация по intent/pending_action
│   │   ├── appointment.py   # Запись на приём (слоты, сбор данных)
│   │   ├── doctor_info.py   # Информация о врачах, подбор по заболеванию
│   │   ├── faq.py           # FAQ клиники
│   │   └── fallback.py      # Приветствия, off-topic, эмоции
│   ├── services/
│   │   ├── llm_service.py   # OpenAI-совместимый LLM клиент
│   │   ├── doctors_service.py # Поиск врачей (fuzzy matching)
│   │   ├── disease_matcher.py # Fuzzy disease matching + doctor ranking
│   │   ├── medesk_service.py  # Medesk CRM (stub)
│   │   └── faq_service.py   # FAQ из JSON
│   ├── utils/
│   │   ├── prompts.py       # Системные промпты (ALICE_PERSONA)
│   │   ├── format.py        # Склонение имён, форматирование дат
│   │   └── streaming.py     # Streaming helpers
│   ├── data/
│   │   ├── doctors.json     # База врачей (10 специалистов)
│   │   ├── diseases.json    # Каталог заболеваний (37 групп + aliases)
│   │   └── faq.json         # FAQ клиники (18 вопросов)
│   └── tg_test_bot.py       # Telegram-бот для тестирования
├── tests/
│   ├── test_integration.py  # Интеграционные тесты (LLM, 47+ кейсов)
│   ├── test_cases.py        # Unit-тесты (mock extraction)
│   └── qa_runner.py         # QA-агент: сценарии + чеклист (87 проверок)
├── eval/                    # Eval-фреймворк (TypeScript)
│   ├── config.ts            # Конфиг: endpoint бота, судья, пороги
│   ├── scenarios.ts         # 6 сценариев (S1–S6) с проверками
│   ├── judge.ts             # LLM-судья (GPT через SOCKS5 proxy)
│   ├── runner.ts            # Прогон сценариев N раз
│   ├── report.ts            # JSON + человекочитаемый отчёт
│   ├── run.ts               # Точка входа (CLI)
│   └── results/             # JSON-отчёты (gitignored)
├── run.py                   # Точка входа голосового бота
├── docs/                    # Архитектурная документация
├── .env.example
└── .gitignore
```

## Быстрый старт

### AI Agent (HTTP API)

```bash
cd medtech
pip install -r requirements.txt
cp .env.example .env  # заполнить LLM_API_KEY

# Запуск API
uvicorn ai_agent.app:app --host 0.0.0.0 --port 8000

# Проверка
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Хочу записаться к врачу", "session_id": "test"}'
```

### Голосовой бот (Asterisk)

Требования: Ubuntu 22.04+, Python 3.11+, Asterisk 21+, ffmpeg

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # заполнить все ключи
python run.py
```

### Тестирование

```bash
# Unit + интеграционные тесты (нужен LLM_API_KEY)
pytest tests/ -v

# QA-агент (3 сценария + чеклист, 87 проверок)
python -m tests.qa_runner

# Eval-фреймворк (6 сценариев x N повторов, LLM-судья)
cd eval && npm install
npx ts-node run.ts                    # все сценарии x10
npx ts-node run.ts --scenario S3      # один сценарий
npx ts-node run.ts --repeat 5         # 5 повторов
npx ts-node run.ts --critical-only    # только критические
```

## Eval-сценарии

| ID | Название | Проверяет |
|----|---------|-----------|
| S1 | Полный цикл записи | Слот → подтверждение → сбор данных → финал |
| S2 | Отвлекающие вопросы | Вопросы вместо данных → ответ + возврат к сбору |
| S3 | Два вопроса в одном | Составные сообщения → ответ на ОБА |
| S4 | Провокации | Попытки вытянуть техинфо о модели |
| S5 | Вне профиля | Жалобы не по специализации клиники |
| S6 | Регрессия | Запись без сбора обязательных данных |

### Пороги прохождения

- **Critical** (100%): кодировка, утечка модели, запись без данных
- **Core** (90%): flow записи, контекст, сбор данных
- **Nice-to-have** (70%): эмпатия, тон, альтернативы

## Переменные окружения

| Переменная | Описание |
|-----------|----------|
| `LLM_API_KEY` | Ключ OpenRouter для LLM бота |
| `LLM_MODEL` | Модель LLM (default: qwen/qwen3-coder) |
| `LLM_BASE_URL` | Base URL LLM API |
| `JUDGE_API_KEY` | Ключ OpenAI для LLM-судьи (eval) |
| `JUDGE_BASE_URL` | Base URL судьи (default: https://api.openai.com/v1) |
| `JUDGE_PROXY` | SOCKS5 прокси для судьи |
| `JUDGE_MODEL` | Модель судьи (default: gpt-4o) |
| `ARI_URL` | URL Asterisk ARI |
| `ARI_USERNAME` / `ARI_PASSWORD` | Учётные данные ARI |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота для тестирования |

## Лицензия

Proprietary. All rights reserved.
