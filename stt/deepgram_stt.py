"""STT using Groq Whisper API (OpenAI-compatible) for Russian speech.

Groq provides free, fast Whisper large-v3 with excellent Russian support.
Also works with OpenAI, or any Whisper-compatible endpoint.
"""

import io
import logging
import wave

import httpx

from utils.audio import ulaw_to_pcm

logger = logging.getLogger(__name__)

# Known Whisper hallucination patterns on silence/noise
_HALLUCINATION_PATTERNS = [
    "редактор субтитров",
    "корректор",
    "подписывайтесь",
    "субтитры",
    "продолжение следует",
    "благодарю за внимание",
    "спасибо за просмотр",
    "до новых встреч",
]

# Vocabulary hint for Whisper — improves recognition of domain-specific terms.
# Include doctor names, clinic name, procedures, medical terms.
# Whisper uses this as a conditioning prompt to bias recognition.
STT_PROMPT = (
    "Клиника Psy Family. "
    "Хайретдинов Олег Замильевич, врач-психиатр детский. "
    "ЭПИ, невротическое расстройство, тревожное расстройство, "
    "расстройство пищевого поведения, нарушение развития. "
    "Запись на приём, слот, дата рождения, ФИО пациента."
)


async def transcribe_ulaw(
    ulaw_data: bytes,
    api_key: str,
    api_url: str = "https://api.groq.com/openai/v1/audio/transcriptions",
    model: str = "whisper-large-v3",
    language: str = "ru",
    prompt: str = STT_PROMPT,
) -> str:
    """Transcribe ulaw audio using Whisper-compatible API.

    Args:
        ulaw_data: Raw ulaw 8kHz mono audio bytes.
        api_key: API key (Groq, OpenAI, etc.).
        api_url: Whisper-compatible transcription endpoint.
        model: Whisper model name.
        language: Language hint.
        prompt: Vocabulary hint to improve recognition accuracy.

    Returns:
        Transcribed text.
    """
    if len(ulaw_data) < 1600:  # Less than 200ms of audio — too short
        return ""

    # Convert ulaw to PCM 16-bit
    pcm_data = ulaw_to_pcm(ulaw_data)

    # Wrap in WAV for the API
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm_data)
    wav_buffer.seek(0)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            data = {
                "model": model,
                "language": language,
                "response_format": "json",
            }
            if prompt:
                data["prompt"] = prompt

            resp = await client.post(
                api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.wav", wav_buffer, "audio/wav")},
                data=data,
            )

        if resp.status_code != 200:
            logger.error("STT error %s: %s", resp.status_code, resp.text[:200])
            return ""

        result = resp.json()
        text = result.get("text", "").strip()
        if not text:
            return ""

        # Filter Whisper hallucinations (subtitle credits, etc.)
        text_lower = text.lower()
        if any(p in text_lower for p in _HALLUCINATION_PATTERNS):
            logger.warning("STT hallucination filtered: %s", text)
            return ""

        logger.info("STT: %s", text)
        return text

    except Exception as e:
        logger.error("STT exception: %s", e)
        return ""
