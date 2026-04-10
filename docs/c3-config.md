# C3 — Component Diagram: Config Module

Детальная архитектура модуля `config/` — конфигурация приложения.

## Диаграмма

```
         .env file
            │
            │  читается автоматически
            │
┌───────────▼──────────────────────────────────────────┐
│              Settings (config/settings.py)            │
│              extends BaseSettings (pydantic)          │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Asterisk ARI                                   │ │
│  │  • ari_url: str = "http://127.0.0.1:8088"     │ │
│  │  • ari_username: str = "medbot"                │ │
│  │  • ari_password: str                           │ │
│  │  • ari_app_name: str = "medbot"               │ │
│  ├─────────────────────────────────────────────────┤ │
│  │  RTP Media                                      │ │
│  │  • external_media_host: str = "127.0.0.1"     │ │
│  │  • rtp_port_start: int = 30000                 │ │
│  │  • rtp_port_end: int = 30100                   │ │
│  ├─────────────────────────────────────────────────┤ │
│  │  LLM (OpenRouter)                               │ │
│  │  • openrouter_api_key: str                     │ │
│  │  • openrouter_model: str                       │ │
│  ├─────────────────────────────────────────────────┤ │
│  │  TTS (ElevenLabs)                               │ │
│  │  • elevenlabs_api_key: str                     │ │
│  │  • elevenlabs_voice_id: str                    │ │
│  │  • elevenlabs_model_id: str                    │ │
│  ├─────────────────────────────────────────────────┤ │
│  │  STT (Groq Whisper)                             │ │
│  │  • stt_api_key: str                            │ │
│  │  • stt_api_url: str                            │ │
│  │  • stt_model: str                              │ │
│  ├─────────────────────────────────────────────────┤ │
│  │  CRM (Medesk)                                   │ │
│  │  • medesk_api_key: str                         │ │
│  │  • medesk_base_url: str                        │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  model_config = {                                     │
│    "env_file": ".env",                               │
│    "env_file_encoding": "utf-8"                      │
│  }                                                    │
│                                                       │
└───────────────────────────────────────────────────────┘
            │
            │  используется в
            │
   ┌────────┴────────────────┐
   ▼                         ▼
 MedBot                 CallHandler
(run.py)           (call_handler.py)
```

## Компонент: Settings

**Файл:** `config/settings.py`

**Технология:** Pydantic Settings (`pydantic-settings`)

**Назначение:** Централизованная конфигурация всего приложения. Автоматически читает переменные окружения и `.env` файл.

### Приоритет значений

1. Переменные окружения (высший приоритет)
2. Файл `.env`
3. Значения по умолчанию в классе

### Группы настроек

#### Asterisk ARI

| Переменная | Env | Default | Описание |
|-----------|-----|---------|----------|
| `ari_url` | `ARI_URL` | `http://127.0.0.1:8088` | HTTP endpoint ARI |
| `ari_username` | `ARI_USERNAME` | `medbot` | ARI пользователь |
| `ari_password` | `ARI_PASSWORD` | — | ARI пароль |
| `ari_app_name` | `ARI_APP_NAME` | `medbot` | Имя Stasis приложения |

#### RTP Media

| Переменная | Env | Default | Описание |
|-----------|-----|---------|----------|
| `external_media_host` | `EXTERNAL_MEDIA_HOST` | `127.0.0.1` | Хост для ExternalMedia |
| `rtp_port_start` | `RTP_PORT_START` | `30000` | Начало диапазона портов |
| `rtp_port_end` | `RTP_PORT_END` | `30100` | Конец диапазона (101 порт) |

#### LLM (OpenRouter)

| Переменная | Env | Default | Описание |
|-----------|-----|---------|----------|
| `openrouter_api_key` | `OPENROUTER_API_KEY` | `""` | API-ключ OpenRouter |
| `openrouter_model` | `OPENROUTER_MODEL` | `google/gemma-3n-e4b-it:free` | Модель LLM |

#### TTS (ElevenLabs)

| Переменная | Env | Default | Описание |
|-----------|-----|---------|----------|
| `elevenlabs_api_key` | `ELEVENLABS_API_KEY` | `""` | API-ключ ElevenLabs |
| `elevenlabs_voice_id` | `ELEVENLABS_VOICE_ID` | `""` | ID голоса |
| `elevenlabs_model_id` | `ELEVENLABS_MODEL_ID` | `eleven_flash_v2_5` | Модель TTS |

#### STT (Groq Whisper)

| Переменная | Env | Default | Описание |
|-----------|-----|---------|----------|
| `stt_api_key` | `STT_API_KEY` | `""` | API-ключ Groq |
| `stt_api_url` | `STT_API_URL` | `https://api.groq.com/...` | Whisper endpoint |
| `stt_model` | `STT_MODEL` | `whisper-large-v3` | Модель STT |

#### CRM (Medesk)

| Переменная | Env | Default | Описание |
|-----------|-----|---------|----------|
| `medesk_api_key` | `MEDESK_API_KEY` | `""` | API-ключ Medesk |
| `medesk_base_url` | `MEDESK_BASE_URL` | `https://api.medesk.md/api/v2` | Base URL API |

### Файл .env.example

Шаблон для создания `.env`:

```env
# Asterisk ARI
ARI_URL=http://127.0.0.1:8088
ARI_USERNAME=medbot
ARI_PASSWORD=your_ari_password
ARI_APP_NAME=medbot

# RTP for ExternalMedia
EXTERNAL_MEDIA_HOST=127.0.0.1
RTP_PORT_START=30000
RTP_PORT_END=30100

# OpenRouter LLM
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=google/gemini-2.0-flash-001

# ElevenLabs TTS
ELEVENLABS_API_KEY=sk_...
ELEVENLABS_VOICE_ID=...
ELEVENLABS_MODEL_ID=eleven_flash_v2_5

# STT — Groq Whisper
STT_API_KEY=gsk_...
STT_API_URL=https://api.groq.com/openai/v1/audio/transcriptions
STT_MODEL=whisper-large-v3

# Medesk CRM
MEDESK_API_KEY=...
MEDESK_BASE_URL=https://api.medesk.md/api/v2
```
