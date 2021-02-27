# -*- coding: utf-8 -*-

"""Connector class for connecting to send requests and receive

responses."""

import asyncio
import logging
import socket
from typing import Optional, Union

import aiohttp

from aioharmony.const import ConnectorCallbackType

DEFAULT_DOMAIN = "svcs.myharmony.com"
DEFAULT_TIMEOUT = 5

_LOGGER = logging.getLogger(__name__)


# TODO: Add docstyle comments
# TODO: Clean up code styling


# pylint: disable=too-many-instance-attributes
class HubConnector:
    """An websocket client for connecting to the Logitech Harmony devices."""

    def __init__(
        self,
        ip_address: str,
        response_queue: asyncio.Queue,
        callbacks: Optional[ConnectorCallbackType] = None,
        auto_reconnect=True,
    ) -> None:
        self._ip_address = ip_address
        self._response_queue = response_queue
        self._callbacks = (
            callbacks if callbacks is not None else ConnectorCallbackType(None, None)
        )
        self._auto_reconnect = auto_reconnect

        self._domain = DEFAULT_DOMAIN
        self._connect_disconnect_lock = asyncio.Lock()
        self._listener_task = None
        self._connected = False
        self._aiohttp_session = None

    @property
    def callbacks(self) -> ConnectorCallbackType:
        """Return callbacks."""
        return self._callbacks

    @callbacks.setter
    def callbacks(self, value: ConnectorCallbackType) -> None:
        """Set callbacks."""
        self._callbacks = value

    @property
    def aiohttp_session(self):
        """Create the aiohttp client session if not existing."""
        # Set connection timeout. Default total timeout is 5 minutes.
        if self._aiohttp_session:
            return self._aiohttp_session

        # Specify socket
        conn = aiohttp.TCPConnector(
            family=socket.AF_INET,
            verify_ssl=False,
            force_close=True,
            enable_cleanup_closed=True,
        )

        session_timeout = aiohttp.ClientTimeout(connect=DEFAULT_TIMEOUT)  # type: ignore
        self._aiohttp_session = aiohttp.ClientSession(
            connector=conn, timeout=session_timeout
        )
        return self._aiohttp_session

    async def close(self):
        """Close all connections and tasks

        This should be called to ensure everything is stopped and
        cancelled out.
        """
        # Close connections.
        await self.hub_disconnect()

    async def hub_connect(self, is_reconnect: bool = False) -> bool:
        raise NotImplementedError

    async def hub_disconnect(self) -> None:
        _LOGGER.debug("%s: Closing HTTP client session.", self._ip_address)
        await self.aiohttp_session.close()

    async def hub_send(
        self, command, params, get_timeout: Optional[int], msgid=None, post=False
    ) -> Optional[Union[asyncio.Future, str]]:
        """Send a payload request to Harmony Hub and return json response."""
        raise NotImplementedError

    async def hub_post(self, url, json_request, headers=None) -> Optional[dict]:
        """Post a json request and return the response."""
        _LOGGER.debug("%s: Sending post request: %s", self._ip_address, json_request)
        try:
            async with self.aiohttp_session.post(
                url, json=json_request, headers=headers
            ) as response:
                json_response = await response.json(content_type=None)
                _LOGGER.debug("%s: Post response: %s", self._ip_address, json_response)
        except aiohttp.ClientError as exc:
            _LOGGER.error("%s: Exception on post: %s", self._ip_address, exc)
        else:
            return json_response

        return None
