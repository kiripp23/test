"""ARI WebSocket + REST client for Asterisk."""

import asyncio
import json
import logging
from typing import Callable, Awaitable

import aiohttp

logger = logging.getLogger(__name__)


class ARIClient:
    """Connects to Asterisk ARI via WebSocket and provides REST helpers."""

    def __init__(self, url: str, username: str, password: str, app_name: str):
        self.base_url = url
        self.username = username
        self.password = password
        self.app_name = app_name
        self._auth = aiohttp.BasicAuth(username, password)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

        # Event callbacks
        self.on_stasis_start: Callable[[dict], Awaitable[None]] | None = None
        self.on_stasis_end: Callable[[dict], Awaitable[None]] | None = None
        self.on_channel_hangup: Callable[[dict], Awaitable[None]] | None = None

    async def connect(self) -> None:
        """Connect to ARI WebSocket and listen for events."""
        self._session = aiohttp.ClientSession()
        ws_url = (
            f"{self.base_url.replace('http', 'ws')}/ari/events"
            f"?api_key={self.username}:{self.password}"
            f"&app={self.app_name}"
            f"&subscribeAll=true"
        )

        while True:
            try:
                logger.info("Connecting to ARI WebSocket...")
                self._ws = await self._session.ws_connect(ws_url, heartbeat=30)
                logger.info("Connected to ARI WebSocket")

                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_event(json.loads(msg.data))
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error("WebSocket error: %s", self._ws.exception())
                        break
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        logger.warning("WebSocket closed")
                        break

            except (aiohttp.ClientError, ConnectionError) as e:
                logger.error("ARI connection error: %s", e)

            logger.info("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

    async def _handle_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        logger.debug("ARI event: %s", event_type)

        if event_type == "StasisStart":
            if self.on_stasis_start:
                asyncio.create_task(self.on_stasis_start(event))
        elif event_type == "StasisEnd":
            if self.on_stasis_end:
                asyncio.create_task(self.on_stasis_end(event))
        elif event_type == "ChannelHangupRequest":
            if self.on_channel_hangup:
                asyncio.create_task(self.on_channel_hangup(event))

    # --- REST helpers ---

    async def _request(self, method: str, path: str, **kwargs) -> dict | None:
        url = f"{self.base_url}/ari{path}"
        async with self._session.request(
            method, url, auth=self._auth, **kwargs
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                logger.error("ARI %s %s -> %s: %s", method, path, resp.status, body)
                return None
            if resp.content_type == "application/json":
                return await resp.json()
            return None

    async def answer_channel(self, channel_id: str) -> None:
        await self._request("POST", f"/channels/{channel_id}/answer")

    async def hangup_channel(self, channel_id: str) -> None:
        await self._request("DELETE", f"/channels/{channel_id}")

    async def create_bridge(self, bridge_type: str = "mixing") -> dict | None:
        return await self._request("POST", "/bridges", json={"type": bridge_type})

    async def add_to_bridge(self, bridge_id: str, channel_id: str) -> None:
        await self._request(
            "POST",
            f"/bridges/{bridge_id}/addChannel",
            json={"channel": channel_id},
        )

    async def destroy_bridge(self, bridge_id: str) -> None:
        await self._request("DELETE", f"/bridges/{bridge_id}")

    async def create_external_media(
        self, host: str, port: int, fmt: str = "ulaw"
    ) -> dict | None:
        return await self._request(
            "POST",
            "/channels/externalMedia",
            json={
                "app": self.app_name,
                "external_host": f"{host}:{port}",
                "format": fmt,
                "encapsulation": "rtp",
                "transport": "udp",
                "direction": "both",
            },
        )

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
