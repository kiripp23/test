# C3 — Component Diagram: Utils Module

Детальная архитектура модуля `utils/` — утилиты аудио-конвертации и логирования.

## Диаграмма

```
      STT module              TTS module
          │                       │
    ulaw_to_pcm()          pcm_to_ulaw()
          │               downsample_*()
          │                       │
┌─────────▼───────────────────────▼─────────────────────┐
│                  audio.py                              │
│                                                        │
│  Codec-конвертеры на базе audioop (CPython built-in)  │
│                                                        │
│  ┌──────────────────────────────────────────────────┐ │
│  │                                                  │ │
│  │  ulaw_to_pcm(data) → bytes                      │ │
│  │    audioop.ulaw2lin(data, 2)                     │ │
│  │    ulaw 8kHz 8-bit → PCM 8kHz 16-bit signed LE │ │
│  │                                                  │ │
│  │  pcm_to_ulaw(data) → bytes                      │ │
│  │    audioop.lin2ulaw(data, 2)                     │ │
│  │    PCM 8kHz 16-bit → ulaw 8kHz 8-bit           │ │
│  │                                                  │ │
│  │  downsample_16k_to_8k(data) → bytes             │ │
│  │    audioop.ratecv(data, 2, 1, 16000, 8000, None)│ │
│  │    PCM 16kHz → PCM 8kHz                         │ │
│  │                                                  │ │
│  │  downsample_24k_to_8k(data) → bytes             │ │
│  │    Выравнивание длины до чётного числа байт     │ │
│  │    audioop.ratecv(data, 2, 1, 24000, 8000, None)│ │
│  │    PCM 24kHz → PCM 8kHz                         │ │
│  │                                                  │ │
│  └──────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────┐
│                  logging.py                            │
│                                                        │
│  setup_logging(level="INFO")                          │
│    Формат: "%(asctime)s [%(name)s] %(levelname)s:     │
│             %(message)s"                               │
│    Уровень: настраивается (default INFO)              │
│                                                        │
└───────────────────────────────────────────────────────┘
```

## Компоненты

### audio.py

**Назначение:** Утилиты конвертации аудио-форматов. Используется модулями STT и TTS.

**Зависимость:** `audioop` — встроенный модуль CPython для низкоуровневых аудио-операций.

| Функция | Вход | Выход | Используется в |
|---------|------|-------|----------------|
| `ulaw_to_pcm()` | ulaw 8kHz 8-bit | PCM 8kHz 16-bit | STT (перед отправкой в Whisper) |
| `pcm_to_ulaw()` | PCM 8kHz 16-bit | ulaw 8kHz 8-bit | TTS (после конвертации) |
| `downsample_16k_to_8k()` | PCM 16kHz | PCM 8kHz | Резерв (не используется) |
| `downsample_24k_to_8k()` | PCM 24kHz | PCM 8kHz | Резерв (TTS использует pydub) |

**Важно:** `downsample_24k_to_8k()` выравнивает данные до чётного числа байт (2 bytes per sample) перед вызовом `audioop.ratecv()`, чтобы избежать ошибки `not a whole number of frames`.

### logging.py

**Назначение:** Единая точка настройки логирования для всего приложения.

```python
def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
```

**Уровни, используемые в проекте:**
- `DEBUG` — RTP-пакеты, детали silence detection
- `INFO` — звонки, STT-результаты, LLM-ответы, tool calls
- `WARNING` — отсутствие remote address для аудио
- `ERROR` — ошибки API, WebSocket, call handler exceptions

## Справка: Аудио-форматы в проекте

```
Телефония (Mango/Asterisk):
  G.711 μ-law (ulaw) — 8kHz, 8-bit, 64 kbps
  G.711 A-law (alaw) — 8kHz, 8-bit, 64 kbps

RTP ExternalMedia:
  ulaw (payload type 0) — 160 байт/фрейм (20ms)

STT (Groq Whisper):
  WAV — PCM 16-bit, 8kHz, mono

TTS (ElevenLabs):
  MP3 — 44.1kHz (streaming endpoint)
  → конвертируется в ulaw 8kHz через pydub/ffmpeg/audioop

Внутренний формат бота:
  ulaw 8kHz mono — единый формат для хранения и передачи
```
