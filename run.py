"""Entry point for the MedTech voice bot service."""

import asyncio
import logging

from config.settings import Settings
from core.ari_client import ARIClient
from core.call_handler import CallHandler
from utils.logging import setup_logging

logger = logging.getLogger(__name__)


class MedBot:
    """Main application — manages ARI connection and active calls."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ari = ARIClient(
            url=settings.ari_url,
            username=settings.ari_username,
            password=settings.ari_password,
            app_name=settings.ari_app_name,
        )
        self._next_port = settings.rtp_port_start
        self._active_calls: dict[str, CallHandler] = {}

    def _allocate_port(self) -> int:
        port = self._next_port
        self._next_port += 1
        if self._next_port > self.settings.rtp_port_end:
            self._next_port = self.settings.rtp_port_start
        return port

    async def _on_stasis_start(self, event: dict) -> None:
        channel = event.get("channel", {})
        channel_id = channel.get("id", "")
        channel_name = channel.get("name", "")
        caller_id = channel.get("caller", {}).get("number", "unknown")

        # Ignore ExternalMedia channels — they also trigger StasisStart
        if channel_name.startswith("UnicastRTP/"):
            logger.debug("Ignoring ExternalMedia StasisStart: %s", channel_name)
            return

        logger.info("New call: channel=%s caller=%s", channel_id, caller_id)

        port = self._allocate_port()
        handler = CallHandler(
            ari=self.ari,
            settings=self.settings,
            channel_id=channel_id,
            caller_id=caller_id,
            rtp_port=port,
        )
        self._active_calls[channel_id] = handler

        try:
            await handler.run()
        finally:
            self._active_calls.pop(channel_id, None)

    async def _on_stasis_end(self, event: dict) -> None:
        channel_id = event.get("channel", {}).get("id", "")
        handler = self._active_calls.get(channel_id)
        if handler:
            handler._running = False
            logger.info("Call ended: %s", channel_id)

    async def run(self) -> None:
        self.ari.on_stasis_start = self._on_stasis_start
        self.ari.on_stasis_end = self._on_stasis_end
        self.ari.on_channel_hangup = self._on_stasis_end

        logger.info("MedBot starting...")
        logger.info("ARI: %s (app=%s)", self.settings.ari_url, self.settings.ari_app_name)
        await self.ari.connect()


def main():
    setup_logging("INFO")
    settings = Settings()
    bot = MedBot(settings)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
