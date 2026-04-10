"""Call lifecycle handler — orchestrates STT, AI Agent, TTS for a single call."""

import asyncio
import logging
import random
import time

from config.settings import Settings
from core.ari_client import ARIClient
from core.audio_bridge import AudioBridge
from ai_agent.agent import PsyFamilyAgent
from tts import elevenlabs_tts
from stt import deepgram_stt

logger = logging.getLogger(__name__)

# Silence detection: if RTP payload energy is below threshold, it's silence
SILENCE_THRESHOLD = 50  # ulaw energy threshold
SILENCE_DURATION = 0.8  # seconds of silence before processing speech
MIN_SPEECH_DURATION = 0.2  # minimum seconds of speech to process
INACTIVITY_TIMEOUT = 30.0  # seconds of total silence before bot hangs up

# Filler phrases — played immediately after STT while agent is thinking.
# Short, natural, fill the silence gap so patient knows bot is working.
FILLERS = [
    "Секундочку.",
    "Сейчас посмотрю.",
    "Одну секунду.",
    "Так, сейчас проверю.",
    "Минуточку.",
    "Да, сейчас посмотрю.",
]


def _ulaw_energy(data: bytes) -> float:
    """Calculate approximate energy of ulaw audio."""
    if not data:
        return 0
    # ulaw silence is 0xFF (127) or 0x7F (127), speech deviates from these
    total = sum(abs(b - 0xFF) if b > 0x7F else abs(b - 0x7F) for b in data)
    return total / len(data)


class CallHandler:
    """Manages a single call session."""

    def __init__(
        self,
        ari: ARIClient,
        settings: Settings,
        channel_id: str,
        caller_id: str,
        rtp_port: int,
    ):
        self.ari = ari
        self.settings = settings
        self.channel_id = channel_id
        self.caller_id = caller_id
        self.rtp_port = rtp_port

        self.audio = AudioBridge(settings.external_media_host, rtp_port)
        self.agent = PsyFamilyAgent(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
        )
        self._session_id = channel_id

        self._bridge_id: str | None = None
        self._ext_channel_id: str | None = None
        self._running = False
        self._ended = False
        self._call_signal: str | None = None
        self._filler_cache: list[bytes] = []
        self._filler_index = 0

    async def run(self) -> None:
        """Main call loop."""
        try:
            await self._setup()
            await self._greeting()
            await self._conversation_loop()
        except Exception:
            logger.exception("Call handler error for %s", self.channel_id)
        finally:
            await self._cleanup()

    async def _setup(self) -> None:
        """Create ExternalMedia channel and bridge."""
        logger.info(
            "Setting up call %s from %s on RTP port %d",
            self.channel_id, self.caller_id, self.rtp_port,
        )

        # Answer the call first
        await self.ari.answer_channel(self.channel_id)
        await asyncio.sleep(0.3)

        # Start audio bridge (UDP socket)
        await self.audio.start()

        # Create ExternalMedia channel
        ext = await self.ari.create_external_media(
            self.settings.external_media_host, self.rtp_port
        )
        if not ext:
            raise RuntimeError("Failed to create ExternalMedia channel")
        self._ext_channel_id = ext["id"]

        # Create bridge and add both channels
        bridge = await self.ari.create_bridge("mixing")
        if not bridge:
            raise RuntimeError("Failed to create bridge")
        self._bridge_id = bridge["id"]

        await self.ari.add_to_bridge(self._bridge_id, self.channel_id)
        await self.ari.add_to_bridge(self._bridge_id, self._ext_channel_id)

        self._running = True
        logger.info("Call setup complete: bridge=%s", self._bridge_id)

    async def _greeting(self) -> None:
        """Play initial greeting and pre-synthesize fillers in background."""
        # Wait for first RTP packet so we know the remote address
        for _ in range(50):  # up to 5 seconds
            await self.audio.receive_audio()
            if self.audio._remote_addr:
                break
        if not self.audio._remote_addr:
            logger.warning("No RTP received from Asterisk, cannot send audio")
            return

        t0 = time.monotonic()
        greeting_text = self.agent.get_greeting()
        t_llm = time.monotonic() - t0
        logger.info("[TIMING] greeting=%.2fs | text: %s", t_llm, greeting_text)
        await self._speak(greeting_text)

        # Pre-synthesize fillers in background while patient is talking
        asyncio.create_task(self._preload_fillers())

    async def _preload_fillers(self) -> None:
        """Pre-synthesize filler phrases so they play instantly."""
        shuffled = FILLERS[:]
        random.shuffle(shuffled)
        for phrase in shuffled:
            try:
                ulaw = await elevenlabs_tts.synthesize(
                    phrase,
                    api_key=self.settings.elevenlabs_api_key,
                    voice_id=self.settings.elevenlabs_voice_id,
                    model_id=self.settings.elevenlabs_model_id,
                )
                if ulaw:
                    self._filler_cache.append(ulaw)
            except Exception:
                logger.debug("Failed to preload filler: %s", phrase)
        logger.info("Preloaded %d filler phrases", len(self._filler_cache))

    async def _play_filler(self) -> None:
        """Play a pre-synthesized filler phrase (round-robin)."""
        if not self._filler_cache:
            return
        audio = self._filler_cache[self._filler_index % len(self._filler_cache)]
        self._filler_index += 1
        await self.audio.send_audio(audio)

    async def _conversation_loop(self) -> None:
        """Listen -> STT -> Filler+Agent -> TTS loop."""
        last_activity = time.monotonic()

        while self._running and not self._ended:
            # Collect speech
            speech_audio = await self._listen_for_speech()
            if not speech_audio or not self._running:
                # Check inactivity timeout
                if time.monotonic() - last_activity > INACTIVITY_TIMEOUT:
                    logger.info("Inactivity timeout for %s", self.channel_id)
                    await self._speak("Если у вас больше нет вопросов, завершаю звонок. До свидания!")
                    await asyncio.sleep(0.5)
                    await self._hangup()
                    return
                continue

            last_activity = time.monotonic()

            t_start = time.monotonic()

            # STT
            t0 = time.monotonic()
            text = await deepgram_stt.transcribe_ulaw(
                speech_audio,
                api_key=self.settings.stt_api_key,
                api_url=self.settings.stt_api_url,
                model=self.settings.stt_model,
            )
            t_stt = time.monotonic() - t0
            if not text:
                continue

            logger.info("Patient said: %s", text)

            # Play filler + run agent in parallel
            t0 = time.monotonic()
            agent_task = asyncio.create_task(self._process_agent_input(text))
            await self._play_filler()
            response = await agent_task
            t_llm = time.monotonic() - t0
            if not response:
                continue

            logger.info("Bot responds: %s", response)

            # TTS -> play
            t0 = time.monotonic()
            await self._speak(response)
            t_tts = time.monotonic() - t0

            t_total = time.monotonic() - t_start
            logger.info(
                "[TIMING] STT=%.2fs Agent=%.2fs TTS+play=%.2fs TOTAL=%.2fs",
                t_stt, t_llm, t_tts, t_total,
            )

            if self._ended:
                # Bot initiates hangup after speaking the final message
                logger.info(
                    "Bot ending call (signal=%s) for %s",
                    self._call_signal, self.channel_id,
                )
                await asyncio.sleep(0.5)
                await self._hangup()
                break

    async def _process_agent_input(self, text: str) -> str:
        """Run sync PsyFamilyAgent in a thread and collect full response."""
        def _run():
            chunks = self.agent(text, session_id=self._session_id)
            return "".join(chunk.text for chunk in chunks)

        response = await asyncio.to_thread(_run)

        # Check for call-control signals
        self._call_signal = self.agent.get_call_signal(self._session_id)
        if self._call_signal in ("end_call", "transfer_operator"):
            self._ended = True

        return response

    async def _listen_for_speech(self) -> bytes | None:
        """Listen to RTP and collect audio until silence after speech."""
        audio_buffer = bytearray()
        speech_started = False
        silence_start: float | None = None
        speech_start: float | None = None

        while self._running:
            payload = await self.audio.receive_audio()
            if payload is None:
                if not self._running:
                    return None
                continue

            energy = _ulaw_energy(payload)

            if energy > SILENCE_THRESHOLD:
                # Speech detected
                if not speech_started:
                    speech_started = True
                    speech_start = time.monotonic()
                    logger.debug("Speech started")
                silence_start = None
                audio_buffer.extend(payload)
            elif speech_started:
                # Silence after speech
                audio_buffer.extend(payload)
                if silence_start is None:
                    silence_start = time.monotonic()
                elif time.monotonic() - silence_start > SILENCE_DURATION:
                    # Enough silence — speech ended
                    duration = time.monotonic() - speech_start
                    if duration >= MIN_SPEECH_DURATION:
                        logger.debug(
                            "Speech ended, duration=%.1fs, buffer=%d bytes",
                            duration, len(audio_buffer),
                        )
                        return bytes(audio_buffer)
                    else:
                        # Too short, reset
                        audio_buffer.clear()
                        speech_started = False
                        silence_start = None

        return None

    async def _speak(self, text: str) -> None:
        """Convert text to speech and play via RTP."""
        ulaw_audio = await elevenlabs_tts.synthesize(
            text,
            api_key=self.settings.elevenlabs_api_key,
            voice_id=self.settings.elevenlabs_voice_id,
            model_id=self.settings.elevenlabs_model_id,
        )
        if ulaw_audio:
            await self.audio.send_audio(ulaw_audio)

    async def _hangup(self) -> None:
        """Bot actively hangs up the call. Asterisk sends SIP BYE to Mango."""
        try:
            await self.ari.hangup_channel(self.channel_id)
            logger.info("Bot hung up channel %s", self.channel_id)
        except Exception:
            logger.debug("Channel %s already gone", self.channel_id)

    async def _cleanup(self) -> None:
        """Clean up all resources."""
        self._running = False
        logger.info("Cleaning up call %s", self.channel_id)

        try:
            self.agent.drop_session(self._session_id)
        except Exception:
            logger.debug("drop_session failed for %s", self._session_id)
        await self.audio.stop()

        if self._ext_channel_id:
            await self.ari.hangup_channel(self._ext_channel_id)
        if self._bridge_id:
            await self.ari.destroy_bridge(self._bridge_id)

        try:
            await self.ari.hangup_channel(self.channel_id)
        except Exception:
            pass  # Channel may already be hung up
