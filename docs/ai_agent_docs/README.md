# AI Agent - Полная документация

Модуль `ai_agent/` - мозг голосового ассистента клиники **Psy Family**. Построен на **LangGraph** (state machine) + **OpenRouter** (Gemini 2.0 Flash). Обрабатывает запись к врачу, справки о врачах, FAQ о клинике и произвольные вопросы. Интегрирован в голосовой пайплайн через `core/call_handler.py`.

## Оглавление

| Документ | Описание |
|----------|----------|
| [Архитектура](architecture.md) | Общая архитектура, граф, потоки данных |
| [Схемы данных](schemas.md) | Pydantic-модели, state, типы |
| [Ноды графа](nodes.md) | Extract, Router, Appointment, DoctorInfo, FAQ, Fallback |
| [Сервисы](services.md) | LLM, Doctors, Medesk, FAQ |
| [Утилиты](utils.md) | Промпты, стриминг, форматирование, склонение |
| [Точка входа](entrypoint.md) | agent.py, call_handler.py, сессии |
| [Данные](data.md) | doctors.json, faq.json |
| [Связь с основным проектом](integration.md) | Голосовой пайплайн, STT, TTS, филлеры |
| [Сценарии диалога](scenarios.md) | Примеры пользовательских сценариев |
| [Автотесты](testing.md) | Детерминированные (233) + интеграционные (58) тесты (cases 1-46) |

## Структура файлов

```
ai_agent/
  agent.py              # PsyFamilyAgent - главный класс-оркестратор
  graph.py              # LangGraph: сборка и компиляция графа
  studio.py             # Точка входа для LangGraph Studio
  state.py              # AgentState (TypedDict) - состояние графа
  schemas.py            # Pydantic-модели данных
  config.py             # Конфигурация (env vars, пути к данным)
  main.py               # CLI точка входа (тестовый)
  nodes/
    extract.py          # Нода извлечения интента/сущностей через LLM
    router.py           # Нода маршрутизации по интенту + pending_action
    appointment.py      # Нода записи на прием (многоходовая стейт-машина)
    doctor_info.py      # Нода информации о врачах
    faq.py              # Нода FAQ
    fallback.py         # Нода fallback (свободная генерация)
  services/
    llm_service.py      # OpenRouter/OpenAI Chat Completions API (json_object)
    doctors_service.py  # Поиск врачей с fuzzy matching (SequenceMatcher)
    medesk_service.py   # Mock-сервис слотов с fuzzy date matching
    faq_service.py      # Поиск по FAQ (keyword matching)
  utils/
    prompts.py          # Системные промпты (extraction, fallback, doctor, FAQ)
    streaming.py        # Утилиты стриминга AgentChunk
    format.py           # Форматирование дат и склонение ФИО
  data/
    doctors.json        # База врачей (1 запись + стоимость)
    faq.json            # FAQ клиники (3 записи)

core/
  call_handler.py       # Голосовой цикл: STT → филлер + Agent → TTS → RTP
  ari_client.py         # Asterisk ARI WebSocket + REST
  audio_bridge.py       # UDP RTP приём/отправка
  rtp.py                # RTP пакеты (RFC 3550)

stt/
  deepgram_stt.py       # Groq Whisper + vocabulary hint + фильтр галлюцинаций

tts/
  elevenlabs_tts.py     # ElevenLabs → MP3 → ulaw 8kHz
```

## Стек технологий

- **LangGraph** - граф состояний (state machine)
- **OpenRouter** (Gemini 2.0 Flash) - извлечение интента, генерация ответов, стриминг
- **Pydantic** - валидация данных и structured output (json_object)
- **Asterisk ARI** - управление SIP-звонками
- **Groq Whisper** - STT (бесплатный, быстрый, русский)
- **ElevenLabs** - TTS
- **LangSmith** - трейсинг (опционально)
- **LangGraph Studio** - визуальный отладчик графа (`langgraph dev`)
