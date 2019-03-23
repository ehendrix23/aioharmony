# -*- coding: utf-8 -*-

"""Connector class for connecting to send requests and receive

responses."""

import asyncio
import logging
import socket
from contextlib import suppress
from typing import NamedTuple, Optional
from urllib.parse import urlparse
from uuid import uuid4

import aiohttp
from async_timeout import timeout

from aioharmony.handler import CallbackType
from aioharmony.helpers import call_callback

DEFAULT_DOMAIN = 'svcs.myharmony.com'
DEFAULT_HUB_PORT = '8088'
DEFAULT_TIMEOUT = 5

_LOGGER = logging.getLogger(__name__)


# TODO: Add docstyle comments
# TODO: Clean up code styling

ConnectorCallbackType = NamedTuple('ConnectorCallbackType',
                                   [('connect', Optional[CallbackType]),
                                    ('disconnect', Optional[CallbackType])
                                    ])


# pylint: disable=too-many-instance-attributes
class HubConnector:
    """An websocket client for connecting to the Logitech Harmony devices."""

    def __init__(self,
                 ip_address: str,
                 response_queue: asyncio.Queue,
                 callbacks: ConnectorCallbackType = None,
                 auto_reconnect=True) -> None:
        self._ip_address = ip_address
        self._response_queue = response_queue
        self._callbacks = callbacks if callbacks is not None else \
            ConnectorCallbackType(None, None)
        self._auto_reconnect = auto_reconnect

        self._remote_id = None
        self._domain = DEFAULT_DOMAIN

        self._aiohttp_session = None
        self._websocket = None

        self._connect_disconnect_lock = asyncio.Lock()

        self._listener_task = None

        self._connected = False

    @property
    def callbacks(self) -> ConnectorCallbackType:
        """Return callbacks."""
        return self._callbacks

    @callbacks.setter
    def callbacks(self, value: ConnectorCallbackType) -> None:
        """Set callbacks."""
        self._callbacks = value

    async def close(self):
        """Close all connections and tasks

           This should be called to ensure everything is stopped and
           cancelled out.
        """
        # Close connections.
        await self.disconnect()

    @property
    def _session(self):
        """Create the aiohttp client session if not existing."""
        # Set connection timeout. Default total timeout is 5 minutes.
        if self._aiohttp_session:
            return self._aiohttp_session

        # Specify socket
        conn = aiohttp.TCPConnector(
            family=socket.AF_INET,
            verify_ssl=False,
        )

        session_timeout = aiohttp.ClientTimeout(connect=5)
        self._aiohttp_session = aiohttp.ClientSession(connector=conn,
                                                      timeout=session_timeout)
        return self._aiohttp_session

    async def _get_remote_id(self) -> Optional[str]:
        """Retrieve remote id from the HUB."""

        if self._remote_id is None:
            # We do not have the remoteId yet, get it first.
            response = await self.retrieve_hub_info()
            if response is not None:
                self._remote_id = response.get('activeRemoteId')
                domain = urlparse(response.get('discoveryServer'))
                self._domain = domain.netloc if domain.netloc else \
                    DEFAULT_DOMAIN
        return self._remote_id

    async def connect(self, is_reconnect: bool = False) -> bool:
        """Connect to Hub Web Socket"""
        # Acquire the lock.
        if self._connect_disconnect_lock.locked():
            _LOGGER.debug("%s: Waiting for other connect", self._ip_address)

        async with self._connect_disconnect_lock:
            # Return connected if we are already connected.
            if self._websocket is not None and not self._websocket.closed:
                return True

            _LOGGER.debug("%s: Starting connect.", self._ip_address)

            if is_reconnect:
                log_level = 10
            else:
                log_level = 40

            if await self._get_remote_id() is None:
                # No remote ID means no connect.
                _LOGGER.log(log_level,
                            "%s: Unable to retrieve HUB id",
                            self._ip_address)
                return False

            _LOGGER.debug("%s: Connecting for hub %s", self._ip_address,
                          self._remote_id)
            try:
                self._websocket = await self._session.ws_connect(
                    'ws://{}:{}/?domain={}&hubId={}'.format(
                        self._ip_address,
                        DEFAULT_HUB_PORT,
                        self._domain,
                        self._remote_id
                    ),
                    heartbeat=10
                )
            except (aiohttp.ServerTimeoutError, aiohttp.ClientError,
                    aiohttp.WSServerHandshakeError) as exc:
                if not is_reconnect:
                    if isinstance(exc, aiohttp.ServerTimeoutError):
                        _LOGGER.log(log_level,
                                    "%s: Connection timed out for hub %s",
                                    self._ip_address,
                                    self._remote_id)
                    elif isinstance(exc, aiohttp.ClientError):
                        _LOGGER.log(log_level,
                                    "%s: Exception trying to establish web "
                                    "socket connection for hub %s: %s",
                                    self._ip_address,
                                    self._remote_id,
                                    exc)
                    else:
                        _LOGGER.log(log_level,
                                    "%s: Invalid status code %s received "
                                    "trying to connect for hub %s: %s",
                                    self._ip_address,
                                    exc.status,
                                    self._remote_id,
                                    exc)
                self._websocket = None
                return False

            # Now put the listener on the loop.
            if not self._listener_task:
                self._listener_task = asyncio.ensure_future(
                    self._listener(self._websocket))

            # Set connected to True, disconnect sets this to False to
            # prevent automatic reconnect when disconnect is explicitly called
            self._connected = True
            call_callback(callback_handler=self._callbacks.connect,
                          result=self._ip_address,
                          callback_uuid=self._ip_address,
                          callback_name='connected'
                          )
            return True

    async def disconnect(self) -> None:
        """Disconnect from Hub"""
        _LOGGER.debug("%s: Disconnecting", self._ip_address)
        # Acquire the lock.
        async with self._connect_disconnect_lock:
            # Set connected to false preventing reconnect from trying to
            # reconnect.
            self._connected = False

            if self._websocket:
                with suppress(asyncio.TimeoutError), timeout(DEFAULT_TIMEOUT):
                    await self._websocket.close()

                await self._session.close()
                # Zero-sleep to allow underlying connections to close.
                await asyncio.sleep(0)

            # Stop our listener if still running
            if self._listener_task and not self._listener_task.done():
                self._listener_task.cancel()
                # One more wait ensuring that our tasks are cancelled.
                await asyncio.sleep(0)

            self._websocket = None

    async def _reconnect(self) -> None:
        """Perform reconnect to HUB if connection failed"""
        call_callback(callback_handler=self._callbacks.disconnect,
                      result=self._ip_address,
                      callback_uuid=self._ip_address,
                      callback_name='disconnected'
                      )
        if not self._connected:
            _LOGGER.debug("%s: Connection was closed through "
                          "disconnect, not reconnecting",
                          self._ip_address)
            return

        _LOGGER.debug("%s: Connection closed, reconnecting",
                      self._ip_address)

        async with self._connect_disconnect_lock:
            # It is possible that the web socket hasn't been closed yet,
            # if this is the case then close it now.
            if self._websocket is not None and not self._websocket.closed:
                _LOGGER.debug("%s: Web Socket half-closed, closing first",
                              self._ip_address)
                with suppress(asyncio.TimeoutError), timeout(DEFAULT_TIMEOUT):
                    await self._websocket.close()

            if self._aiohttp_session is not None and not \
                    self._aiohttp_session.closed:
                _LOGGER.debug("%s: Closing sessions",
                              self._ip_address)
                with suppress(asyncio.TimeoutError), timeout(DEFAULT_TIMEOUT):
                    await self._aiohttp_session.close()

        # Set web socket to none allowing for reconnect.
        self._websocket = None
        self._aiohttp_session = None

        is_reconnect = False
        sleep_time = 1
        # Wait for 1 second before trying reconnects.
        await asyncio.sleep(sleep_time)
        while not await self.connect(is_reconnect=is_reconnect):
            await asyncio.sleep(sleep_time)
            sleep_time = sleep_time * 2
            sleep_time = min(sleep_time, 30)
            is_reconnect = True

    async def send(self, command, params, msgid=None) -> Optional[str]:
        """Send a payload request to Harmony Hub and return json response."""
        # Make sure we're connected.
        if not await self.connect():
            return

        if not msgid:
            msgid = str(uuid4())

        payload = {
            "hubId": self._remote_id,
            "timeout": DEFAULT_TIMEOUT,
            "hbus": {
                "cmd": command,
                "id": msgid,
                "params": params
            }
        }

        _LOGGER.debug("%s: Sending payload: %s", self._ip_address, payload)
        try:
            await self._websocket.send_json(payload)
        except aiohttp.ClientError as exc:
            _LOGGER.error("%s: Exception sending payload: %s",
                          self._ip_address, exc)
            return

        return msgid

    async def post(self, url, json_request, headers=None) -> Optional[dict]:
        """Post a json request and return the response."""
        _LOGGER.debug("%s: Sending post request: %s",
                      self._ip_address,
                      json_request)
        try:
            async with self._session.post(
                    url, json=json_request, headers=headers) as response:
                json_response = await response.json(content_type=None)
                _LOGGER.debug("%s: Post response: %s",
                              self._ip_address,
                              json_response)
        except aiohttp.ClientError as exc:
            _LOGGER.error("%s: Exception on post: %s", self._ip_address, exc)
        else:
            return json_response

        return None

    # pylint: disable=broad-except
    async def _listener(self, websocket=None) -> None:
        """Listen for  messages on web socket"""
        _LOGGER.debug("%s: Listener started", self._ip_address)
        if not websocket:
            websocket = self._websocket

        if not websocket:
            _LOGGER.error("%s: No web socket to listen on", self._ip_address)
        # This is a continuous loop until socket is closed.
        have_connection = True
        while have_connection:
            # Put everything here in a try block, we do not want this
            # to stop running out due to an exception.
            try:
                if not websocket or websocket.closed:
                    _LOGGER.debug("%s: Web socket has been closed")
                    have_connection = False
                    break

                try:
                    response = await websocket.receive()
                except aiohttp.ClientError as exc:
                    _LOGGER.error("%s: Exception during receive: %s",
                                  self._ip_address, exc)
                    break

                _LOGGER.debug("%s: Response payload: %s", self._ip_address,
                              response.data)

                if response.type == aiohttp.WSMsgType.CLOSED:
                    _LOGGER.debug("%s: Web socket closed",
                                  self._ip_address)
                    # Issue reconnect event if enabled
                    have_connection = False
                    break

                if response.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("%s: Response error", self._ip_address)
                    continue

                if response.type != aiohttp.WSMsgType.TEXT:
                    continue

                response_json = response.json()
                if not response_json:
                    continue

                # Put response on queue.
                self._response_queue.put_nowait(response_json)

            except asyncio.CancelledError:
                _LOGGER.debug("%s: Received STOP for listener",
                              self._ip_address)
                break

            # pylint: disable=broad-except
            # Need to catch everything here to prevent an issue in a
            # callback from ever causing the handler to exit.
            except Exception as exc:
                _LOGGER.exception("%s: Exception in listener: %s",
                                  self._ip_address, exc)

        self._listener_task = None
        _LOGGER.debug("%s: Listener stopped.", self._ip_address)

        # If we exited the loop due to connection closed then
        # call reconnect to determine if we should reconnect again.
        if not have_connection:
            await self._reconnect()

    async def retrieve_hub_info(self) -> Optional[dict]:
        """Retrieve the harmony Hub information."""
        _LOGGER.debug("%s: Retrieving Harmony Hub information.",
                      self._ip_address)
        url = 'http://{}:{}/'.format(self._ip_address, DEFAULT_HUB_PORT)
        headers = {
            'Origin': 'http://sl.dhg.myharmony.com',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Accept-Charset': 'utf-8',
        }
        json_request = {
            "id ": 1,
            "cmd": "setup.account?getProvisionInfo",
            "params": {}
        }

        response = await self.post(url, json_request, headers)

        if response is not None:
            return response.get('data')

        return None
