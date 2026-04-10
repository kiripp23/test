"""ElevenLabs TTS client."""

import audioop
import io
import logging
import time

import httpx
from pydub import AudioSegment

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech"


def _mp3_to_ulaw(mp3_data: bytes) -> bytes:
    """Convert MP3 bytes to ulaw 8kHz mono."""
    audio = AudioSegment.from_mp3(io.BytesIO(mp3_data))
    audio = audio.set_frame_rate(8000).set_channels(1).set_sample_width(2)
    pcm = audio.raw_data
    return audioop.lin2ulaw(pcm, 2)


async def synthesize(
    text: str,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_v3",
) -> bytes:
    """Synthesize text to ulaw 8kHz audio bytes."""
    t0 = time.monotonic()

    url = f"{ELEVENLABS_API_URL}/{voice_id}/stream"
    headers = {"xi-api-key": api_key}
    payload = {
        "text": text,
        "model_id": model_id,
        "language_code": "ru",
        "apply_text_normalization": "on",
        "voice_settings": {
            "stability": 0.0,
            "similarity_boost": 0.8,
        },
    }

    chunks = []
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                logger.error("ElevenLabs error %s: %s", resp.status_code, body[:200])
                return b""
            async for chunk in resp.aiter_bytes(8192):
                chunks.append(chunk)

    t_download = time.monotonic() - t0
    mp3_data = b"".join(chunks)
    if not mp3_data:
        return b""

    t1 = time.monotonic()
    ulaw = _mp3_to_ulaw(mp3_data)
    t_convert = time.monotonic() - t1

    logger.info("[TIMING] TTS download=%.2fs convert=%.2fs total=%.2fs (%d bytes)",
                t_download, t_convert, time.monotonic() - t0, len(ulaw))
    return ulaw
