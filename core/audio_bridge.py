"""ExternalMedia channel + UDP RTP socket management."""

import asyncio
import logging
import time

import asyncio_dgram

from core.rtp import RTPPacket, RTPSender, PAYLOAD_ULAW, SAMPLES_PER_FRAME

logger = logging.getLogger(__name__)


class AudioBridge:
    """Manages bidirectional RTP audio for a single call."""

    def __init__(self, listen_host: str, listen_port: int):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self._udp: asyncio_dgram.DatagramStream | None = None
        self._remote_addr: tuple[str, int] | None = None
        self._rtp_sender = RTPSender(ssrc=12345, payload_type=PAYLOAD_ULAW)
        self._running = False
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def start(self) -> None:
        """Bind UDP socket and start receiving."""
        self._udp = await asyncio_dgram.bind((self.listen_host, self.listen_port))
        self._running = True
        logger.info("AudioBridge listening on %s:%d", self.listen_host, self.listen_port)

    async def stop(self) -> None:
        """Stop and close UDP socket."""
        self._running = False
        if self._udp:
            self._udp.close()
            self._udp = None

    async def receive_audio(self) -> bytes | None:
        """Receive one RTP packet's audio payload. Returns ulaw bytes or None."""
        if not self._udp or not self._running:
            return None
        try:
            data, addr = await asyncio.wait_for(self._udp.recv(), timeout=1.0)
            self._remote_addr = addr
            pkt = RTPPacket.parse(data)
            return pkt.payload
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.debug("RTP receive error: %s", e)
            return None

    async def send_audio(self, ulaw_data: bytes) -> None:
        """Send ulaw audio as RTP packets to Asterisk (20ms frames)."""
        if not self._udp or not self._remote_addr:
            logger.warning("Cannot send audio: no remote address yet")
            return

        offset = 0
        while offset < len(ulaw_data):
            chunk = ulaw_data[offset : offset + SAMPLES_PER_FRAME]
            if len(chunk) < SAMPLES_PER_FRAME:
                # Pad last frame with silence (ulaw silence = 0xFF)
                chunk = chunk + b"\xff" * (SAMPLES_PER_FRAME - len(chunk))
            packet = self._rtp_sender.make_packet(chunk)
            await self._udp.send(packet, self._remote_addr)
            # Pace at 20ms per frame for real-time playback
            await asyncio.sleep(0.02)
            offset += SAMPLES_PER_FRAME

    async def drain_until_speech(self, timeout: float = 0.5) -> None:
        """Drain incoming RTP packets for a period (used during TTS playback)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            await self.receive_audio()
