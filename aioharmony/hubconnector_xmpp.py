# -*- coding: utf-8 -*-

"""Connector class for connecting to send requests and receive

responses."""

import asyncio
import json
import logging
from typing import Optional, Union
from uuid import uuid4

from async_timeout import timeout
import slixmpp
from slixmpp.exceptions import IqTimeout
from slixmpp.xmlstream import ET
from slixmpp.xmlstream.handler.callback import Callback
from slixmpp.xmlstream.matcher import MatchXPath

from aioharmony.const import (
    DEFAULT_XMPP_HUB_PORT as DEFAULT_HUB_PORT,
    ConnectorCallbackType,
)
import aioharmony.exceptions as aioexc
from aioharmony.helpers import call_callback
from aioharmony.hubconnector import HubConnector

DEFAULT_DOMAIN = "svcs.myharmony.com"
DEFAULT_TIMEOUT = 5
DEFAULT_USER = "user@connect.logitech.com/gatorade."
DEFAULT_PASSWORD = "password"
DEFAULT_NS = "connect.logitech.com"

_LOGGER = logging.getLogger(__name__)


# TODO: Add docstyle comments
# TODO: Clean up code styling


# pylint: disable=too-many-instance-attributes
class HubConnector_xmpp(HubConnector):
    """An XMPP client for connecting to the Logitech Harmony devices."""

    def __init__(
        self,
        ip_address: str,
        response_queue: asyncio.Queue,
        callbacks: Optional[ConnectorCallbackType] = None,
        auto_reconnect=True,
    ) -> None:

        self._listener_message_received = None
        self._xmppclient = None

        super().__init__(
            ip_address=ip_address,
            response_queue=response_queue,
            callbacks=callbacks,
            auto_reconnect=auto_reconnect,
        )
        self._init_xmppclient()

    def _init_xmppclient(self):

        self._xmppclient = slixmpp.ClientXMPP(
            jid=DEFAULT_USER,
            password=DEFAULT_PASSWORD,
            plugin_config={
                # Enables PLAIN authentication which is off by default.
                "feature_mechanisms": {"unencrypted_plain": True},
            },
        )

        # Set keep-alive to 30 seconds.
        self.whitespace_keepalive_interval = 30

        # Register all the event handlers and callbacks within XMPP
        self._register_handlers()

    def _register_handlers(self):
        """Register all the different handlers within XMPP based on

        messages being received and events."""
        _LOGGER.debug("%s: Registering internal handlers.", self._ip_address)
        # Register the callback for messages being received
        self._listener()

        # Register callback for connection.
        self._xmppclient.add_event_handler(
            "connected",
            self._connected_handler,
            disposable=False,
        )

        # Register callback for disconnections.
        self._xmppclient.add_event_handler(
            "disconnected",
            self._disconnected_handler,
            disposable=False,
        )

    def _deregister_handlers(self):
        # Remove handlers.
        _LOGGER.debug("%s: Removing internal handlers.", self._ip_address)
        self._xmppclient.del_event_handler("connected", self._connected_handler)
        self._xmppclient.del_event_handler("disconnected", self._disconnected_handler)
        self._xmppclient.remove_handler("listener")

    async def hub_connect(self, is_reconnect: bool = False) -> bool:
        """Connect to Hub"""
        # Acquire the lock.
        if self._connect_disconnect_lock.locked():
            _LOGGER.debug("%s: Waiting for other connect", self._ip_address)

        async with self._connect_disconnect_lock:
            # Return connected if we are already connected.
            if self._connected:
                return True

            _LOGGER.debug("%s: Connecting to hub", self._ip_address)

            if is_reconnect:
                log_level = 10
            else:
                log_level = 40

            loop = asyncio.get_event_loop()
            connected = loop.create_future()

            def connection_success(_):
                self._xmppclient.del_event_handler(
                    "connection_failed", connection_failed
                )
                connected.set_result(True)

            def connection_failed(event):
                connected.set_exception(event)
                self._xmppclient.cancel_connection_attempt()
                self._xmppclient.del_event_handler("connected", connection_success)

            def remove_handlers():
                # Remove the handlers.
                self._xmppclient.del_event_handler(
                    "connection_failed", connection_failed
                )
                self._xmppclient.del_event_handler("connected", connection_success)

            self._xmppclient.add_event_handler(
                "connected",
                connection_success,
                disposable=True,
            )

            self._xmppclient.add_event_handler(
                "connection_failed",
                connection_failed,
                disposable=True,
            )

            try:
                self._xmppclient.connect(
                    address=(self._ip_address, DEFAULT_HUB_PORT),
                    disable_starttls=True,
                    use_ssl=False,
                )

            except IqTimeout:
                _LOGGER.log(
                    log_level, "%s: Connection timed out for hub", self._ip_address
                )

                # Remove the handlers.
                remove_handlers()
                return False

            # Wait till we're connected.
            try:
                await connected
            except (asyncio.TimeoutError, TimeoutError):
                _LOGGER.log(
                    log_level,
                    "%s: Timeout waiting for connecting to hub",
                    self._ip_address,
                )
                # Remove the handlers.
                remove_handlers()
                return False
            except asyncio.CancelledError:
                _LOGGER.debug(
                    "%s: Connecting to hub has been cancelled", self._ip_address
                )
                # Remove the handlers.
                remove_handlers()
                return False
            except OSError as exc:
                _LOGGER.log(
                    log_level,
                    "%s: Connecting to HUB failed with error: %s",
                    self._ip_address,
                    exc,
                )
                # Remove the handlers.
                remove_handlers()
                return False

            # Remove the handlers.
            self._connected = True
            remove_handlers()
            _LOGGER.debug("%s: Connected to hub", self._ip_address)
            return True

    async def hub_disconnect(self) -> None:
        """Disconnect from Hub"""
        _LOGGER.debug("%s: Disconnecting", self._ip_address)
        # Acquire the lock.
        async with self._connect_disconnect_lock:
            if not self._connected:
                return

            # Set connected to false preventing reconnect from trying to
            # reconnect.
            self._connected = False

            loop = asyncio.get_event_loop()
            disconnected = loop.create_future()

            def disconnect_result(_):
                disconnected.set_result(True)

            self._deregister_handlers()

            self._xmppclient.add_event_handler(
                "disconnected",
                disconnect_result,
                disposable=True,
            )
            self._xmppclient.disconnect()

            # Wait till we're disconnected.
            try:
                with timeout(DEFAULT_TIMEOUT):
                    await disconnected
            except asyncio.TimeoutError:
                _LOGGER.debug("%s: Timeout trying to disconnect.", self._ip_address)
                self._xmppclient.del_event_handler("disconnected", disconnect_result)
                raise aioexc.TimeOut

            await super().hub_disconnect()

    def _connected_handler(self, _) -> None:
        """Call handler for connection."""
        self._connected = True
        if self._callbacks.connect:
            call_callback(
                callback_handler=self._callbacks.connect,
                result=self._ip_address,
                callback_uuid=self._ip_address,
                callback_name="connected",
            )
        else:
            _LOGGER.debug("No connect callback handler provided")

    async def _disconnected_handler(self, _) -> None:
        """Perform reconnect to HUB if connection failed"""
        if self._callbacks.disconnect:
            call_callback(
                callback_handler=self._callbacks.disconnect,
                result=self._ip_address,
                callback_uuid=self._ip_address,
                callback_name="disconnected",
            )
        else:
            _LOGGER.debug("No disconnect callback handler provided")

        if not self._connected:
            _LOGGER.debug(
                "%s: Connection was closed through " "disconnect, not reconnecting",
                self._ip_address,
            )
            return

        if not self._auto_reconnect:
            _LOGGER.debug(
                "%s: Connection closed, auto-reconnect disabled", self._ip_address
            )
            return

        _LOGGER.debug("%s: Connection closed, reconnecting", self._ip_address)
        self._connected = False
        is_reconnect = False

        self._deregister_handlers()
        self._init_xmppclient()

        sleep_time = 1
        await asyncio.sleep(sleep_time)
        while True:
            try:
                if await self.hub_connect(is_reconnect=is_reconnect):
                    # Exit loop if connected.
                    break
            except IqTimeout:
                pass
            finally:
                # Wait and try again.
                await asyncio.sleep(sleep_time)
                sleep_time = sleep_time * 2
                sleep_time = min(sleep_time, 30)
            is_reconnect = True

    async def hub_send(
        self,
        command,
        iq_type="get",
        params=None,
        get_timeout=None,
        msgid=None,
        post=False,
    ) -> Optional[Union[asyncio.Future, str]]:
        """Send a payload request to Harmony Hub and return json response."""

        if not msgid:
            msgid = str(uuid4())

        if post:
            return await super().hub_send(
                command=command,
                params=params,
                get_timeout=get_timeout,
                msgid=msgid,
                post=post,
            )

        # Make sure we're connected.
        if not await self.hub_connect():
            return

        def result_callback(future_result):
            # This is done to ensure that any time out exceptions are
            # captured
            try:
                future_result.result()
            except IqTimeout:
                pass

        if iq_type == "query":
            iq_stanza = self._xmppclient.make_iq_query()
        elif iq_type == "set":
            iq_stanza = self._xmppclient.make_iq_set()
        elif iq_type == "result":
            iq_stanza = self._xmppclient.make_iq_result()
        elif iq_type == "error":
            iq_stanza = self._xmppclient.make_iq_error(id=msgid)
        else:
            iq_stanza = self._xmppclient.make_iq_get()
        iq_stanza["id"] = msgid

        payload = ET.Element("oa")
        payload.attrib["xmlns"] = DEFAULT_NS
        payload.attrib["mime"] = command

        payload_text = None
        for key in params:
            if payload_text is None:
                payload_text = key + "=" + str(params[key])
            else:
                payload_text = payload_text + ":" + key + "=" + str(params[key])

        payload.text = payload_text
        iq_stanza.set_payload(payload)

        _LOGGER.debug(
            "%s: Sending payload: %s %s", self._ip_address, payload.attrib, payload.text
        )

        result = iq_stanza.send(timeout=1)

        # Add done callback to capture any timeout exceptions.
        result.add_done_callback(result_callback)

        return msgid

    def _listener(self) -> None:
        """Enable callback"""

        def message_received(event):
            def add_to_dict(dictionary: dict, key: str, value: Optional[str] = None):
                if value is None:
                    value = ""

                if value[0] == "{":
                    # It is a JSON value. Run it through for decoding.
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError as err:
                        _LOGGER.debug(
                            "%s: JSON decoding error %s at line %s and column %s. Document: %s",
                            self._ip_address,
                            err.msg,
                            err.lineno,
                            err.colno,
                            err.doc,
                        )
                dictionary.update({key: value})

            payload = event.get_payload()
            if len(payload) == 0:
                _LOGGER.error(
                    "%s: Invalid payload length of 0 received.",
                    self._ip_address,
                )
                return

            for message in payload:
                data = {}
                # Try to convert JSON object if JSON object was received
                if message.text is not None and message.text != "":
                    try:
                        data = json.loads(message.text)
                    except json.JSONDecodeError as err:
                        # Should mean only a single value was received.
                        _LOGGER.debug(
                            "%s: JSON decoding error %s at line %s and column %s. Document: %s",
                            self._ip_address,
                            err.msg,
                            err.lineno,
                            err.colno,
                            err.doc,
                        )
                        pairings = {"{": "}", '"': '"'}
                        have_key = escape = False
                        key = value = ""
                        stack = []
                        for character in message.text:
                            # If we don't have the key yet then keep adding to the key until we reach =
                            if not have_key:
                                if character == "=":
                                    have_key = True
                                else:
                                    key = key + character
                                continue

                            # If we have : and nothing on the stack then we have the value
                            if character == ":" and not stack:
                                # Now we will have key and value, add to our dictionary.
                                add_to_dict(dictionary=data, key=key, value=value)
                                have_key = escape = False
                                key = value = ""
                                stack = []
                                continue

                            # We're going through the value. This can be a JSON object and hence we need to
                            # get everything between the 1st { and last }
                            value = value + character

                            # If previous was a \ (escape) then just move on with next character.
                            if escape:
                                escape = False
                                continue

                            # If character now is \ then it means it is escape character.
                            if character == "\\":
                                escape = True
                                continue

                            # Now we know that if we get " and we already have an open " that it is the closing one.
                            # Check if what we should get for the next closing element: } or "
                            closing_element = (
                                pairings.get(stack[-1]) if len(stack) != 0 else None
                            )

                            # Check if this character is the closing element we're expecting
                            if character == closing_element:
                                # It is, pop from our stack and move to next one.
                                stack.pop()
                                continue

                            # It is not a closing. Only thing left now is an open one or any other character
                            # any other character.
                            if character in pairings:
                                # This is an opening character, add it to the stack.
                                stack.append(character)

                        # Add the last one.
                        if have_key:
                            add_to_dict(dictionary=data, key=key, value=value)

                # Create response dictionary
                response = {
                    "id": event.get("id"),
                    "xmlns": message.attrib.get("xmlns"),
                    "cmd": message.attrib.get("mime"),
                    "type": message.attrib.get("type"),
                    "code": float(message.attrib.get("errorcode", "0")),
                    "codestring": message.attrib.get("errorstring"),
                    "data": data,
                }
                _LOGGER.debug("%s: Response payload: %s", self._ip_address, response)
                # Put response on queue.
                self._response_queue.put_nowait(response)

        self._listener_message_received = message_received

        # Register our callback.
        self._xmppclient.register_handler(
            Callback(
                "Listener",
                MatchXPath(
                    "{{{0}}}iq/{{{1}}}oa".format(
                        self._xmppclient.default_ns, DEFAULT_NS
                    )
                ),
                message_received,
            )
        )

        self._xmppclient.register_handler(
            Callback(
                "Listener",
                MatchXPath(
                    "{{{0}}}message/{{{1}}}event".format(
                        self._xmppclient.default_ns, DEFAULT_NS
                    )
                ),
                message_received,
            )
        )
