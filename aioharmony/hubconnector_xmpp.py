# -*- coding: utf-8 -*-

"""Connector class for connecting to send requests and receive

responses."""

import asyncio
import json
import logging
from typing import Optional
from uuid import uuid4

import slixmpp
from async_timeout import timeout
from slixmpp.exceptions import IqTimeout
from slixmpp.xmlstream import ET
from slixmpp.xmlstream.handler.callback import Callback
from slixmpp.xmlstream.matcher import MatchXPath

import aioharmony.exceptions as aioexc
from aioharmony.const import (
    ConnectorCallbackType,
    DEFAULT_XMPP_HUB_PORT as DEFAULT_HUB_PORT
)
from aioharmony.helpers import call_callback

DEFAULT_DOMAIN = 'svcs.myharmony.com'
DEFAULT_TIMEOUT = 5
DEFAULT_USER = 'user@connect.logitech.com/gatorade.'
DEFAULT_PASSWORD = 'password'
DEFAULT_NS = 'connect.logitech.com'

_LOGGER = logging.getLogger(__name__)


# TODO: Add docstyle comments
# TODO: Clean up code styling


# pylint: disable=too-many-instance-attributes
class HubConnector(slixmpp.ClientXMPP):
    """An XMPP client for connecting to the Logitech Harmony devices."""

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

        self._domain = DEFAULT_DOMAIN

        self._connect_disconnect_lock = asyncio.Lock()

        self._listener_task = None

        self._connected = False

        plugin_config = {
            # Enables PLAIN authentication which is off by default.
            'feature_mechanisms': {'unencrypted_plain': True},
        }
        super(HubConnector, self).__init__(
            DEFAULT_USER, DEFAULT_PASSWORD, plugin_config=plugin_config)

        # Set keep-alive to 30 seconds.
        self.whitespace_keepalive_interval = 30

        # Register all the event handlers and callbacks within XMPP
        self._register_handlers()

    @property
    def callbacks(self) -> ConnectorCallbackType:
        """Return callbacks."""
        return self._callbacks

    @callbacks.setter
    def callbacks(self, value: ConnectorCallbackType) -> None:
        """Set callbacks."""
        self._callbacks = value

    def _register_handlers(self):
        """Register all the different handlers within XMPP based on

        messages being received and events."""

        # Register the callback for messages being received
        self._listener()

        # Register callback for connection.
        self.add_event_handler('connected',
                               self._connected_handler,
                               disposable=False,
                               )

        # Register callback for disconnections.
        self.add_event_handler('disconnected',
                               self._disconnected_handler,
                               disposable=False,
                               )

    async def close(self):
        """Close all connections and tasks

           This should be called to ensure everything is stopped and
           cancelled out.
        """
        # Close connections.
        await self.hub_disconnect()

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

            loop = asyncio.get_event_loop()
            connected = loop.create_future()

            def connection_success(_):
                connected.set_result(True)

            def connection_failed(event):
                connected.set_exception(event)
                self.cancel_connection_attempt()
                # Doing below as for some reason cancel does not really
                # cancel it. This will result in exception and it
                # stopping.
                self.address = (None, None)

            def remove_handlers():
                # Remove the handlers.
                self.del_event_handler('connection_failed', connection_failed)
                self.del_event_handler('connected', connection_success)

            self.add_event_handler('connected',
                                   connection_success,
                                   disposable=True,
                                   )

            self.add_event_handler('connection_failed',
                                   connection_failed,
                                   disposable=True,
                                   )

            try:
                super(HubConnector, self).connect(
                    address=(self._ip_address, DEFAULT_HUB_PORT),
                    disable_starttls=True,
                    use_ssl=False,
                )

            except IqTimeout:
                if not is_reconnect:
                    _LOGGER.error("%s: Connection timed out for hub",
                                  self._ip_address)

                # Remove the handlers.
                remove_handlers()
                return False

            # Wait till we're connected.
            try:
                await connected
            except asyncio.TimeoutError:
                # Remove the handlers.
                remove_handlers()
                raise aioexc.TimeOut
            except (asyncio.CancelledError, TimeoutError):
                # Remove the handlers.
                remove_handlers()
                return False

            # Remove the handlers.
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

            self.add_event_handler('disconnected',
                                   disconnect_result,
                                   disposable=True,
                                   )
            super(HubConnector, self).disconnect()

            # Wait till we're disconnected.
            try:
                with timeout(DEFAULT_TIMEOUT):
                    await disconnected
            except asyncio.TimeoutError:
                self.del_event_handler('disconnected', disconnect_result)
                raise aioexc.TimeOut

    def _connected_handler(self, _) -> None:
        """Call handler for connection."""
        self._connected = True
        call_callback(callback_handler=self._callbacks.connect,
                      result=self._ip_address,
                      callback_uuid=self._ip_address,
                      callback_name='connected'
                      )

    async def _disconnected_handler(self, _) -> None:
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

        if not self._auto_reconnect:
            _LOGGER.debug("%s: Connection closed, auto-reconnect disabled",
                          self._ip_address)
            return

        _LOGGER.debug("%s: Connection closed, reconnecting",
                      self._ip_address)
        self._connected = False
        is_reconnect = False
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

    async def hub_send(self,
                       command,
                       iq_type='get',
                       params=None,
                       msgid=None) -> \
            Optional[str]:
        """Send a payload request to Harmony Hub and return json response."""
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

        if not msgid:
            msgid = str(uuid4())

        if iq_type == 'query':
            iq_stanza = self.make_iq_query()
        elif iq_type == 'set':
            iq_stanza = self.make_iq_set()
        elif iq_type == 'result':
            iq_stanza = self.make_iq_result()
        elif iq_type == 'error':
            iq_stanza = self.make_iq_error(id=msgid)
        else:
            iq_stanza = self.make_iq_get()
        iq_stanza['id'] = msgid

        payload = ET.Element('oa')
        payload.attrib['xmlns'] = DEFAULT_NS
        payload.attrib['mime'] = command

        payload_text = None
        for key in params:
            if payload_text is None:
                payload_text = key + '=' + str(params[key])
            else:
                payload_text = payload_text + ':' + \
                    key + '=' + str(params[key])

        payload.text = payload_text
        iq_stanza.set_payload(payload)

        _LOGGER.debug("%s: Sending payload: %s %s",
                      self._ip_address,
                      payload.attrib,
                      payload.text)

        result = iq_stanza.send(timeout=1)

        # Add done callback to capture any timeout exceptions.
        result.add_done_callback(result_callback)

        return msgid

    def _listener(self) -> None:
        """Enable callback"""
        def message_received(event):
            payload = event.get_payload()
            if len(payload) != 1:
                _LOGGER.error("%s: Invalid payload length of %s received",
                              self._ip_address,
                              len(payload))
                return

            message = payload[0]
            data = {}
            # Try to convert JSON object if JSON object was received
            if message.text is not None and message.text != '':
                try:
                    data = json.loads(message.text)
                except json.JSONDecodeError:
                    # Should mean only a single value was received.
                    for item in message.text.split(':'):
                        item_split = item.split('=')
                        if len(item_split) == 2:
                            data.update({item_split[0]: item_split[1]})

            # Create response dictionary
            response = {
                'id': event.get('id'),
                'xmlns': message.attrib.get('xmlns'),
                'cmd': message.attrib.get('mime'),
                'type': message.attrib.get('type'),
                'code': int(message.attrib.get('errorcode', '0')),
                'codestring': message.attrib.get('errorstring'),
                'data': data,
            }
            _LOGGER.debug("%s: Response payload: %s", self._ip_address,
                          response)
            # Put response on queue.
            self._response_queue.put_nowait(response)

        # Register our callback.
        self.register_handler(
            Callback('Listener',
                     MatchXPath('{{{0}}}iq/{{{1}}}oa'.format(self.default_ns,
                                                             DEFAULT_NS)),
                     message_received))

        self.register_handler(
            Callback('Listener',
                     MatchXPath('{{{0}}}message/{{{1}}}event'.format(
                         self.default_ns,
                         DEFAULT_NS)),
                     message_received))

    async def retrieve_hub_info(self) -> Optional[dict]:
        """ To retrieve HUB info, not used for XMPP. """
        pass
