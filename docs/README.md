# MedTech Voice Bot — Архитектурная документация (C4 Model)

Документация построена по модели [C4](https://c4model.com/) — от общего контекста к деталям каждого компонента.

Диаграммы написаны на PlantUML — исходники в [diagrams/](diagrams/).

## Уровни

### C1 — System Context
- [c1-context.md](c1-context.md) — Кто использует систему, с какими внешними сервисами взаимодействует

### C2 — Containers
- [c2-containers.md](c2-containers.md) — Два контейнера (Asterisk PBX + Python MedBot), их взаимодействие, сетевая модель

### C3 — Components
Детальное описание каждого модуля Python-бота:

| Документ | Модуль | Описание |
|----------|--------|----------|
| [c3-core.md](c3-core.md) | `core/` | ARI-клиент, обработка звонков, RTP аудио, silence detection |
| [c3-llm.md](c3-llm.md) | `llm/` | Управление диалогом, промпт, tool calling, OpenRouter API |
| [c3-stt.md](c3-stt.md) | `stt/` | Распознавание речи через Groq Whisper |
| [c3-tts.md](c3-tts.md) | `tts/` | Синтез речи через ElevenLabs + ffmpeg конвертация |
| [c3-integrations.md](c3-integrations.md) | `integrations/` | Medesk CRM клиент (stub + план интеграции) |
| [c3-config.md](c3-config.md) | `config/` | Pydantic Settings, переменные окружения |
| [c3-utils.md](c3-utils.md) | `utils/` | Аудио-конвертация, логирование |

### Sequence Diagram
- [c3-call-flow.md](c3-call-flow.md) — Полная последовательность обработки звонка

## Навигация по уровням

```
C1 Context          Что делает система и кто её использует
    │
    ▼
C2 Containers       Из каких процессов состоит (Asterisk + Python)
    │
    ▼
C3 Components       Как устроен каждый модуль внутри
    │
    ▼
C4 Code             → читайте исходный код в соответствующих .py файлах
```
