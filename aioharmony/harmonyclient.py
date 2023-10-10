# -*- coding: utf-8 -*-

"""
This is the main module containing the class to be imported and used:
from aioharmony.client import HarmonyClient

The HarmonyClient class represents the Harmony Hub. Using the methods of
this class allows one to query or send commands to the Hub.
"""

import asyncio
import copy
import logging
from datetime import timedelta
from typing import List, NamedTuple, Optional, Tuple, Union
from uuid import uuid4

from async_timeout import timeout

import aioharmony.exceptions as aioexc
import aioharmony.handler as handlers
from aioharmony.const import (
    ClientCallbackType, ClientConfigType, ConnectorCallbackType,
    DEFAULT_XMPP_HUB_PORT, HUB_COMMANDS, PROTOCOL, SendCommand, SendCommandDevice,
    SendCommandResponse, WEBSOCKETS, XMPP
)
from aioharmony.helpers import call_callback, search_dict
from aioharmony.responsehandler import Handler, ResponseHandler

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60

# TODO: Add docstyle comments
# TODO: Clean up code styling


# pylint: disable=too-many-instance-attributes
class HarmonyClient:
    """An websocket client for connecting to the Logitech Harmony devices."""
    # pylint: disable=too-many-arguments
    def __init__(self,
                 ip_address: str,
                 protocol: PROTOCOL = None,
                 callbacks: ClientCallbackType = None,
                 loop: asyncio.AbstractEventLoop = None) -> None:
        _LOGGER.debug("%s: Initialize HUB", ip_address)
        self._ip_address = ip_address
        self._protocol = protocol
        self._callbacks = callbacks if callbacks is not None else \
            ClientCallbackType(None, None, None, None, None)
        self._loop = loop if loop else asyncio.get_event_loop()

        self._hub_config = ClientConfigType({}, {}, {}, {}, None, [], [])
        self._current_activity_id = None
        self._hub_connection = None

        # Get the queue on which JSON responses will be put
        self._response_queue = asyncio.Queue()

        # Get the Response Handler
        self._callback_handler = ResponseHandler(
            message_queue=self._response_queue,
            name=self.name
        )

        # Create the lock for sending commands or starting an activity
        self._snd_cmd_act_lck = asyncio.Lock()

        # Create the lock for getting HUB information.
        self._sync_lck = asyncio.Lock()

        # Create the activity start handler object when start activity is finished
        handler = copy.copy(handlers.HANDLER_START_ACTIVITY_FINISHED)
        handler.handler_obj = self._update_activity_callback
        self._callback_handler.register_handler(
            handler=handler)

        # Create the activity start handler object when start activity is finished
        handler = copy.copy(handlers.HANDLER_START_ACTIVITY_NOTIFY_STARTED)
        handler.handler_obj = self._update_start_activity_callback
        self._callback_handler.register_handler(
            handler=handler)

        # Create the activity start handler object when start activity is finished
        handler = copy.copy(handlers.HANDLER_STOP_ACTIVITY_NOTIFY_STARTED)
        handler.handler_obj = self._update_start_activity_callback
        self._callback_handler.register_handler(
            handler=handler)

        # Create the notification handler object
        handler = copy.copy(handlers.HANDLER_NOTIFY)
        handler.handler_obj = self._notification_callback
        self._callback_handler.register_handler(
            handler=handler)

    @property
    def ip_address(self) -> str:
        return self._ip_address

    @property
    def protocol(self) -> str:
        return self._protocol

    @property
    def name(self) -> Optional[str]:
        name = self._hub_config.discover_info.get('friendlyName')
        return name if name is not None else self._ip_address

    @property
    def hub_config(self) -> ClientConfigType:
        return self._hub_config

    @property
    def callbacks(self) -> ClientCallbackType:
        return self._callbacks

    @callbacks.setter
    def callbacks(self, value: ClientCallbackType) -> None:
        self._callbacks = value
        self._hub_connection.callbacks = ConnectorCallbackType(
            connect=self._callbacks.connect,
            disconnect=self._callbacks.disconnect
        )

    @property
    def current_activity_id(self):
        return self._current_activity_id

    async def _websocket_or_xmpp(self) -> bool:
        """ Determine if XMPP is enabled, if not fall-back to web sockets.
        """
        if not self._protocol == WEBSOCKETS :
            try:
                _, _ = await asyncio.open_connection(host=self._ip_address,
                                                     port=DEFAULT_XMPP_HUB_PORT
                                                     )
            except ConnectionRefusedError:
                if self._protocol == XMPP:
                    _LOGGER.warning("%s: XMPP is not enabled on this HUB, will be defaulting back to WEBSOCKETS.",
                                    self.name)
                else:
                    _LOGGER.debug("%s: XMPP is not enabled, using web sockets.",
                                    self.name)
                self._protocol = WEBSOCKETS
            except OSError as exc:
                _LOGGER.error("%s: Unable to determine if XMPP is enabled: %s",
                              self.name,
                              exc)
                if self._protocol is None:
                    return False
            else:
                _LOGGER.debug("%s: XMPP is enabled", self.name)
                self._protocol = XMPP

        if self._protocol == WEBSOCKETS:
            _LOGGER.debug("%s: Using WEBSOCKETS", self.name)
            from aioharmony.hubconnector_websocket import HubConnector
        else:
            _LOGGER.debug("%s: Using XMPP", self.name)
            from aioharmony.hubconnector_xmpp import HubConnector

        self._hub_connection = HubConnector(
            ip_address=self._ip_address,
            callbacks=ConnectorCallbackType(
                None,
                self._callbacks.disconnect
            ),
            response_queue=self._response_queue)
        return True

    async def connect(self) -> bool:
        """

        :return: True if connection was successful, False if it was not.
        :rtype: bool
        :raises: :class:`~aioharmony.exceptions.TimeOut`
        """

        if self._hub_connection is None:
            if not await self._websocket_or_xmpp():
                return False

        try:
            async with timeout(DEFAULT_TIMEOUT):
                if not await self._hub_connection.hub_connect():
                    return False
        except asyncio.TimeoutError:
            raise aioexc.TimeOut

        # Initiate a sync. That will then result in our notification handler
        # to receive the response and set our current config version
        # accordingly.

        results = await asyncio.gather(
            self.send_to_hub(command='get_current_state'),
            self.refresh_info_from_hub(),
            return_exceptions=True
        )
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                if not isinstance(
                        result,
                        aioexc.TimeOut):
                    raise result

                if idx == 0:
                    _LOGGER.error("%s: Timeout trying to sync hub.", self.name)

                continue

            if idx == 0:
                resp_data = result.get('data')
                if resp_data is not None:
                    self._hub_config = self._hub_config._replace(
                        config_version=resp_data.get('configVersion'))
                    self._hub_config = self._hub_config._replace(
                        hub_state=resp_data)
                    _LOGGER.debug("%s: HUB configuration version is: %s",
                                  self.name,
                                  self._hub_config.config_version)
            else:
                _LOGGER.debug("%s: HUB ID : %s",
                              self.name,
                              self._hub_config.info.get('activeRemoteId'))

        if self._hub_connection.callbacks.connect is None and \
                self._callbacks.connect is not None:
            # First time call, add the callback handler now and run it.
            _LOGGER.debug("%s, calling connect callback for first time",
                          self.name)
            call_callback(
                callback_handler=self._callbacks.connect,
                result=self._ip_address,
                callback_uuid=self._ip_address,
                callback_name='connected'
            )
            self._hub_connection.callbacks = ConnectorCallbackType(
                self._callbacks.connect,
                self._callbacks.disconnect
            )

        _LOGGER.debug("%s: Connected to HUB on IP %s with ID %s.",
                      self.name,
                      self._ip_address,
                      self._hub_config.info.get('activeRemoteId'))

        return True

    async def close(self) -> None:
        """Close all connections and tasks

           This should be called to ensure everything is stopped and
           cancelled out.
        """
        raise_exception = None
        if self._hub_connection:
            try:
                async with timeout(DEFAULT_TIMEOUT):
                    await self._hub_connection.close()
            except Exception as e:
                _LOGGER.debug("%s: Exception occurred during disconnection.",
                              self.name)
                raise_exception = e
                if isinstance(raise_exception, asyncio.TimeoutError):
                    raise_exception = aioexc.TimeOut

        if self._callback_handler:
            try:
                async with timeout(DEFAULT_TIMEOUT):
                    await self._callback_handler.close()
            except asyncio.TimeoutError:
                raise aioexc.TimeOut

        if raise_exception is not None:
            raise raise_exception


    async def disconnect(self) -> None:
        """Disconnect from Hub"""
        _LOGGER.debug("%s: Disconnecting from %s",
                      self.name,
                      self._ip_address)
        try:
            async with timeout(DEFAULT_TIMEOUT):
                await self._hub_connection.hub_disconnect()
        except asyncio.TimeoutError:
            raise aioexc.TimeOut

    async def refresh_info_from_hub(self) -> None:
        _LOGGER.debug("%s: Retrieving HUB information",
                      self.name)

        async with self._sync_lck:
            try:
                # Retrieve configuration and HUB version config.
                async with timeout(DEFAULT_TIMEOUT*4):
                    results = await asyncio.gather(
                        self._get_config(),
                        self._retrieve_hub_info(),
                        return_exceptions=True
                    )
            except asyncio.TimeoutError:
                _LOGGER.error("%s: Timeout trying to retrieve configuraton.",
                              self.name)
                raise aioexc.TimeOut

            for idx, result in enumerate(results):
                if isinstance(
                        result,
                        aioexc.TimeOut):
                    # Timeout exception, just put out error then.
                    if idx == 0:
                        result_name = 'config'
                    else:
                        result_name = 'hub info'

                    _LOGGER.error("%s: Timeout trying to retrieve %s.",
                                  self.name,
                                  result_name)
                    return
                elif isinstance(result, Exception):
                    # Other exception, raise it.
                    raise result

            await self._get_current_activity()

        # If we were provided a callback handler then call it now.
        if self._callbacks.config_updated:
            _LOGGER.debug("%s: Calling callback handler for config_updated", self.name)
            call_callback(
                callback_handler=self._callbacks.config_updated,
                result=self._hub_config.config,
                callback_uuid=self._ip_address,
                callback_name='config_updated_callback'
            )

    async def _get_config(self) -> Optional[dict]:
        """Retrieves the Harmony device configuration.

        Returns:
            A nested dictionary containing activities, devices, etc.
        """
        _LOGGER.debug("%s: Getting configuration",
                      self.name)
        # Send the command to the HUB
        try:
            async with timeout(DEFAULT_TIMEOUT/2):
                response = await self.send_to_hub(command='get_config', send_timeout=DEFAULT_TIMEOUT/4)
        except (asyncio.TimeoutError, aioexc.TimeOut):
            try:
                async with timeout(DEFAULT_TIMEOUT/2):
                    response = await self.send_to_hub(command='get_config', send_timeout=DEFAULT_TIMEOUT/4)
            except (asyncio.TimeoutError, aioexc.TimeOut):
                raise aioexc.TimeOut

        if not response:
            # There was an issue
            return None

        if response.get('code') != 200:
            _LOGGER.error("%s: Incorrect status code %s received trying to "
                          "get configuration for %s",
                          self.name,
                          response.get('code'),
                          self._ip_address)
            return None

        self._hub_config = self._hub_config._replace(
            config=response.get('data'))

        self._hub_config = self._hub_config._replace(
            activities=list(
                {
                    'name': a['label'],
                    'name_lowercase': a['label'].lower(),
                    'id': int(a['id'])
                } for a in self._hub_config.config.get('activity')))

        self._hub_config = self._hub_config._replace(
            devices=list(
                {
                    'name': a['label'],
                    'name_lowercase': a['label'].lower(),
                    'id': int(a['id'])
                } for a in self._hub_config.config.get('device')))

        return self._hub_config.config

    async def _retrieve_provision_info(self) -> Optional[dict]:

        response = None
        result = None
        try:
            async with timeout(DEFAULT_TIMEOUT/2):
                result = await self.send_to_hub(command='provision_info', post=True, send_timeout=DEFAULT_TIMEOUT/4)
        except (asyncio.TimeoutError, aioexc.TimeOut):
            try:
                _LOGGER.debug("%s: Timeout trying to retrieve provisioning info, retrying.", self.name)
                async with timeout(DEFAULT_TIMEOUT/2):
                    result = await self.send_to_hub(command='provision_info', post=True, send_timeout=DEFAULT_TIMEOUT/4)
            except (asyncio.TimeoutError, aioexc.TimeOut):
                _LOGGER.error("%s: Timeout trying to retrieve provisioning info.", self.name)

        if result is not None:
            if result.get('code') != 200 and result.get('code') != '200':
                _LOGGER.error("%s: Incorrect status code %s received trying to "
                              "get provisioning info for %s",
                              self.name,
                              result.get('code'),
                              self._ip_address)
            else:
                self._hub_config = self._hub_config._replace(
                    info=result.get('data'))
                response = self._hub_config.info

        return response

    async def _retrieve_discovery_info(self) -> None:

        result = None
        try:
            async with timeout(DEFAULT_TIMEOUT/2):
                result = await self.send_to_hub(command='discovery', post=False, send_timeout=DEFAULT_TIMEOUT/4)
        except (asyncio.TimeoutError, aioexc.TimeOut):
            try:
                _LOGGER.debug("%s: Timeout trying to retrieve discovery info, retrying", self.name)
                async with timeout(DEFAULT_TIMEOUT/2):
                    result = await self.send_to_hub(command='discovery', post=False, send_timeout=DEFAULT_TIMEOUT/4)
            except (asyncio.TimeoutError, aioexc.TimeOut):
                _LOGGER.error("%s: Timeout trying to retrieve discovery info.", self.name)

        if result is not None:
            if result.get('code') != 200 and result.get('code') != '200':
                _LOGGER.error("%s: Incorrect status code %s received trying to "
                              "get provisioning info for %s",
                              self.name,
                              result.get('code'),
                              self._ip_address)
            else:
                self._hub_config = self._hub_config._replace(
                    discover_info=result.get('data'))

    async def _retrieve_hub_info(self) -> Optional[dict]:
        """Retrieve some information from the Hub."""
        # Send the command to the HUB

        response = None

        results = await asyncio.gather(
            self._retrieve_provision_info(),
            self._retrieve_discovery_info(),
            return_exceptions=True
        )
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                raise result

            if idx == 0:
                response = result

        return response

    async def send_to_hub(self,
                          command: str,
                          params: dict = None,
                          msgid: str = None,
                          wait: bool = True,
                          post: bool = False,
                          send_timeout: int = DEFAULT_TIMEOUT) -> Union[dict, bool]:

        if msgid is None:
            msgid = str(uuid4())

        if params is None:
            params = {
                'verb': 'get',
                'format': 'json'
            }

        response = None
        handler_uuid = None
        if wait:
            response = self._loop.create_future()
            resp_handler = Handler(handler_obj=response,
                                   handler_name=command,
                                   once=True,
                                   expiration=timedelta(
                                       seconds=DEFAULT_TIMEOUT)
                                   )
            handler_uuid = self.register_handler(handler=resp_handler,
                                                 msgid=msgid)
        try:
            async with timeout(send_timeout):
                send_response = await self._hub_connection.hub_send(
                        command='{}?{}'.format(
                            HUB_COMMANDS[command]['mime'],
                            HUB_COMMANDS[command]['command']
                        ),
                        params=params,
                        msgid=msgid,
                        post=post,
                )
                if send_response is None:
                    # There was an issue
                    if handler_uuid is not None:
                        self.unregister_handler(handler_uuid=handler_uuid)
                    return False
        except asyncio.TimeoutError:
            if handler_uuid is not None:
                self.unregister_handler(handler_uuid=handler_uuid)
            raise aioexc.TimeOut

        if asyncio.isfuture(send_response):
            response = send_response
        elif not wait:
            return True

        # Wait for the response to be available.
        try:
            async with timeout(send_timeout):
                await response
        except asyncio.TimeoutError:
            if handler_uuid is not None:
                self.unregister_handler(handler_uuid=handler_uuid)
            raise aioexc.TimeOut

        return response.result()

    async def _get_current_activity(self) -> bool:
        """Update current activity when changed."""
        _LOGGER.debug("%s: Retrieving current activity", self.name)

        # Send the command to the HUB
        try:
            async with timeout(DEFAULT_TIMEOUT/2):
                response = await self.send_to_hub(command='get_current_activity', send_timeout=DEFAULT_TIMEOUT/4)
        except (asyncio.TimeoutError, aioexc.TimeOut):
            _LOGGER.debug("%s: Timeout trying to retrieve current activity, retrying.",
                          self.name)
            try:
                async with timeout(DEFAULT_TIMEOUT/2):
                    response = await self.send_to_hub(command='get_current_activity', send_timeout=DEFAULT_TIMEOUT/4)
            except (asyncio.TimeoutError, aioexc.TimeOut):
                _LOGGER.error("%s: Second Timeout trying to retrieve current activity.",
                              self.name)
                response = None

        if not response:
            # There was an issue
            return False

        if response.get('code') != 200:
            _LOGGER.error("%s: Incorrect status code %s received trying to get"
                          "current activity for %s",
                          self.name,
                          response.get('code'),
                          self._ip_address)
            return False

        self._current_activity_id = int(response['data']['result'])
        _LOGGER.debug("%s: Current activity: %s(%s)",
                      self.name,
                      self.get_activity_name(self._current_activity_id),
                      self._current_activity_id)

        # If we were provided a callback handler then call it now.
        if self._callbacks.new_activity:
            _LOGGER.debug("%s: Calling callback handler for new_activity", self.name)
            call_callback(
                callback_handler=self._callbacks.new_activity,
                result=(self._current_activity_id,
                        self.get_activity_name(
                            activity_id=self._current_activity_id)
                        ),
                callback_uuid=self._ip_address,
                callback_name='new_activity_callback'
            )
        return True

    # pylint: disable=broad-except
    async def _notification_callback(self,
                                     message: dict = None) -> None:
        # We received a notification, check if the config version has changed.
        _LOGGER.debug("%s: Notification was received", self.name)
        resp_data = message.get('data')
        if resp_data is not None:
            current_hub_config_version = resp_data.get('configVersion')
            sync_status = resp_data.get('syncStatus')

            # If no sync status or it is 1 (sync in progress) then nothing
            # to do
            if current_hub_config_version is None or sync_status is None or \
                    sync_status == 1:
                return

            # Only do config update
            if current_hub_config_version != \
                    self._hub_config.config_version:
                _LOGGER.debug("%s: HUB configuration updated from version "
                              "%s to %s",
                              self.name,
                              self._hub_config.config_version,
                              current_hub_config_version)
                self._hub_config = self._hub_config._replace(
                    config_version=current_hub_config_version)
                # Get all the HUB information.
                await self.refresh_info_from_hub()

    # pylint: disable=broad-except
    async def _update_activity_callback(self,
                                        message: dict = None) -> None:
        """Update current activity when changed."""
        _LOGGER.debug("%s: New activity was started", self.name)

        new_activity = None
        message_data = message.get('data')
        if message_data is not None:
            new_activity = int(message_data.get('activityId'))

        if new_activity is None:
            await self._get_current_activity()
            return

        self._current_activity_id = new_activity
        _LOGGER.debug("%s: New activity: %s(%s)",
                      self.name,
                      self.get_activity_name(self._current_activity_id),
                      self._current_activity_id)

        # If we were provided a callback handler then call it now.
        if self._callbacks.new_activity:
            _LOGGER.debug("%s: Calling callback handler for new_activity", self.name)
            call_callback(
                callback_handler=self._callbacks.new_activity,
                result=(self._current_activity_id,
                        self.get_activity_name(
                            self._current_activity_id)
                        ),
                callback_uuid=self._ip_address,
                callback_name='new_activity_callback'
            )

    # pylint: disable=broad-except
    async def _update_start_activity_callback(self,
                                        message: dict = None) -> None:
        """Update current activity when changed."""
        _LOGGER.debug("%s: New activity starting notification", self.name)

        message_data = message.get('data')
        if message_data is not None and message_data.get('activityStatus') == 0:
            # The HUB sends a power off notification again that it is starting when it is done
            # thus intercepting this so we do not redo the callback.
            if int(message_data.get('activityId')) == -1 and self._current_activity_id == -1:
                return

            self._current_activity_id = -1
            _LOGGER.debug("%s: Powering off from activity: %s(%s)",
                          self.name,
                          self.get_activity_name(self._current_activity_id),
                          self._current_activity_id)
            self._current_activity_id = -1
        else:
            if message_data is not None:
                self._current_activity_id = int(message_data.get('activityId'))
            else:
                self._current_activity_id = None

            _LOGGER.debug("%s: New activity starting: %s(%s)",
                          self.name,
                          self.get_activity_name(self._current_activity_id),
                          self._current_activity_id)

        # If we were provided a callback handler then call it now.
        if self._callbacks.new_activity_starting:
            _LOGGER.debug("%s: Calling callback handler for new_activity_starting", self.name)
            call_callback(
                callback_handler=self._callbacks.new_activity_starting,
                result=(self._current_activity_id,
                        self.get_activity_name(
                            self._current_activity_id)
                        ),
                callback_uuid=self._ip_address,
                callback_name='new_activity_starting_callback'
            )

    # pylint: disable=too-many-statements
    # pylint: disable=too-many-locals
    async def start_activity(self, activity_id) -> tuple:
        """Starts an activity.

        Args:
            activity_id: An int or string identifying the activity to start

        Returns:
            True if activity started, otherwise False
        """
        _LOGGER.debug("%s: Starting activity %s (%s)",
                      self.name,
                      self.get_activity_name(activity_id),
                      activity_id)
        params = {
            "async": "true",
            "timestamp": 0,
            "activityId": str(activity_id)
        }
        msgid = str(uuid4())

        activity_completed = self._loop.create_future()
        handler_list = []

        def register_activity_handler(activity_handler: Handler) -> None:
            handler_uuid = self.register_handler(
                handler=activity_handler,
                msgid=msgid
            )
            handler_list.append((handler_uuid, activity_handler))

        def unregister_handlers() -> None:
            _LOGGER.debug("%s: Unregistering handlers for Start Activity",
                          self.name)
            for activity_handler in handler_list:
                if not self.unregister_handler(activity_handler[0]):
                    _LOGGER.warning("%s: Callback %s with UUID %s was not "
                                    "found anymore",
                                    self.name,
                                    activity_handler[1].handler_name,
                                    activity_handler[0])

        def set_result(result: tuple) -> None:
            if activity_completed.done():
                _LOGGER.debug("%s: Result was already set through a previous "
                              "message.",
                              self.name)
                return
            activity_completed.set_result(result)

        def startactivity_started_callback(message: dict):
            if message.get('code') not in [100, 200]:
                _LOGGER.debug("%s: RunActivity code error: %s",
                              self.name,
                              message.get('code'))
                set_result((False,
                            message.get('msg')))

        def startactivity_in_progress_callback(message: dict) -> None:
            data = message.get('data')
            if data is not None:
                progress = {
                    'completed': data.get('done'),
                    'total': data.get('total')
                }
                _LOGGER.info("%s: %s/%s of start activity %s completed.",
                             self.name,
                             progress['completed'],
                             progress['total'],
                             activity_id)

        def startactivity_completed_callback(message: dict) -> None:
            if message.get('code') == 200:
                _LOGGER.debug("%s: Start or discrete completion code: %s",
                              self.name,
                              message.get('code'))
                set_result((True, message.get('msg')))
                return

            if message.get('code') != 100:
                _LOGGER.debug("%s: Start or discrete code error: %s",
                              self.name,
                              message.get('code'))
                set_result((False, message.get('msg')))
                return

        # Register handler to identify failure for initiating the start of
        # the activity
        handler = copy.copy(handlers.HANDLER_RUN_ACTIVITY)
        handler.handler_obj = startactivity_started_callback
        register_activity_handler(activity_handler=handler)

        # Register first handler to track progress of the start activity
        handler = copy.copy(handlers.HANDLER_START_ACTIVITY_IN_PROGRESS)
        handler.handler_obj = startactivity_in_progress_callback
        register_activity_handler(activity_handler=handler)

        # Register second handler to track progress of the start activity
        handler = copy.copy(handlers.HANDLER_HELPDISCRETES)
        handler.handler_obj = startactivity_in_progress_callback
        register_activity_handler(activity_handler=handler)

        handler = copy.copy(handlers.HANDLER_START_ACTIVITY_COMPLETE)
        handler.handler_obj = startactivity_completed_callback
        register_activity_handler(activity_handler=handler)

        _LOGGER.debug("%s: Handlers registered with ID %s",
                      self.name,
                      msgid)
        # Get the lock ensuring we're the only ones able to initiate
        async with self._snd_cmd_act_lck:
            response = await self.send_to_hub(command='start_activity',
                                              params=params,
                                              msgid=msgid,
                                              wait=False)
            if not response:
                unregister_handlers()

            try:
                async with timeout(DEFAULT_TIMEOUT):
                    status = await activity_completed
            except asyncio.TimeoutError:
                raise aioexc.TimeOut
            finally:
                unregister_handlers()
                _LOGGER.debug("%s: Start activity %s (%s) has been completed",
                              self.name,
                              self.get_activity_name(activity_id),
                              activity_id)

        return status

    async def send_commands(self,
                            commands: List[SendCommand]) -> \
            List[SendCommandResponse]:

        _LOGGER.debug("%s: Sending commands to HUB", self.name)
        # Get the lock ensuring we're the only ones able to initiate
        command_future_list = []
        msgid_dict = {}
        async with self._snd_cmd_act_lck:
            for command in commands:
                if isinstance(command, (float, int)):
                    await asyncio.sleep(command)
                    continue

                # Create the future to be set for the result.
                # The HUB sends a message back if there is an issue with
                # the command, otherwise it won't sent anything back.
                command_future_list.append(self._loop.create_future())

                expiration = 0.5
                if command.delay is not None:
                    expiration += command.delay

                command_handler = Handler(handler_obj=command_future_list[-1],
                                          handler_name='{}_{}'.format(
                                              command.device,
                                              command.command),
                                          expiration=timedelta(
                                              seconds=expiration)
                                          )

                msgid_press, msgid_release = await self._send_command(
                    command,
                    command_handler)
                if msgid_press is not None:
                    msgid_dict.update({msgid_press: command})
                if msgid_release is not None:
                    msgid_dict.update({msgid_release: command})

        # Go through the result list to determine there were any issues with
        # any of the commands sent. Only if there is an issue would a response
        # have been received.
        done, _ = await asyncio.wait(command_future_list, timeout=1)

        error_response_list = []
        for result_returned in done:
            result = result_returned.result()
            msgid = result.get('id')
            if msgid is None:
                _LOGGER.warning("%s: Received response for send commands "
                                "without a message id",
                                self.name)
                continue

            command_sent = msgid_dict.get(msgid)
            if command_sent is None:
                _LOGGER.warning("%s: Received response for send command "
                                "with unknown message id %s",
                                self.name,
                                msgid)
                continue

            if isinstance(command_sent, (float, int)):
                continue

            _LOGGER.debug("%s: Received code %s for command %s to device "
                          "%s: %s",
                          self.name,
                          result.get('code'),
                          command_sent.command,
                          command_sent.device,
                          result.get('msg')
                          )

            # HUB might send back OK (200) code, ignore those.
            if str(result.get('code')) != '200':
                error_response_list.append(SendCommandResponse(
                    command=command_sent,
                    code=result.get('code'),
                    msg=result.get('msg')
                ))

        _LOGGER.debug("%s: Sending commands to HUB has been completed",
                      self.name)
        return error_response_list

    async def _send_command(self,
                            command: SendCommandDevice,
                            callback_handler: Handler) ->\
            Tuple[Optional[str], Optional[str]]:
        """Send a command to specified device

        :param command: Command to send to the device. (device, command, delay)
        :type command: SendCommandDevice
        :return: msgid with which this command was sent for.
        :rtype: str

        """
        _LOGGER.debug("%s: Sending command %s to device %s (%s) with delay "
                      "%ss",
                      self.name,
                      command.command,
                      self.get_device_name(command.device),
                      command.device,
                      command.delay)
        params = {
            "status": "press",
            "timestamp": '0',
            "verb": "render",
            "action": '{{"command":: "{}",'
                      '"type":: "IRCommand",'
                      '"deviceId":: "{}"}}'.format(command.command,
                                                  command.device)
        }
        msgid_press = str(uuid4())

        # Register the handler for this command.
        self.register_handler(handler=callback_handler,
                              msgid=msgid_press)

        # Send the command to the HUB
        response = await self.send_to_hub(command='send_command',
                                          params=params,
                                          msgid=msgid_press,
                                          wait=False)
        if not response:
            # There was an issue
            return None, None

        if command.delay > 0:
            await asyncio.sleep(command.delay)

        params['status'] = 'release'

        msgid_release = str(uuid4())
        # Register the handler for this command.
        self.register_handler(handler=callback_handler,
                              msgid=msgid_release)
        # Send the command to the HUB
        await self.send_to_hub(command='send_command',
                               params=params,
                               msgid=msgid_release,
                               wait=False)

        _LOGGER.debug("%s: Sending command %s to device %s (%s) with delay "
                      "%ss has been completed",
                      self.name,
                      command.command,
                      self.get_device_name(command.device),
                      command.device,
                      command.delay)
        return msgid_press, msgid_release

    def get_activity_id(self, activity_name) -> Optional[int]:
        """Find the activity ID for the provided activity name."""
        if activity_name is None:
            return None

        item = search_dict(match_value=activity_name.lower(),
                           key='name_lowercase',
                           search_list=self._hub_config.activities)
        return item.get('id') if item else None

    def get_activity_name(self, activity_id) -> Optional[str]:
        """Find the activity name for the provided ID."""
        if activity_id is None:
            return None

        item = search_dict(match_value=int(activity_id),
                           key='id',
                           search_list=self._hub_config.activities)
        return item.get('name') if item else None

    def get_device_id(self, device_name) -> Optional[int]:
        """Find the device ID for the provided device name."""
        if device_name is None:
            return None

        item = search_dict(match_value=device_name.lower(),
                           key='name_lowercase',
                           search_list=self._hub_config.devices)
        return item.get('id') if item else None

    def get_device_name(self, device_id) -> Optional[str]:
        """Find the device name for the provided ID."""
        if device_id is None:
            return None

        item = search_dict(match_value=int(device_id),
                           key='id',
                           search_list=self._hub_config.devices)
        return item.get('name') if item else None

    def register_handler(self, *args, **kwargs) -> str:
        """
        Exposes
        :meth:`aioharmony.responsehandler.ResponseHandler.register_handler` for
        use to register other callbacks.

        See
        :meth:`~aioharmony.responsehandler.ResponseHandler.register_handler`
        in class
        :class:`~aioharmony.responsehandler.ResponseHandler` for further
        information on this method
        """
        return self._callback_handler.register_handler(*args, **kwargs)

    def unregister_handler(self, *args, **kwargs) -> bool:
        """
        Exposes
        :meth:`aioharmony.responsehandler.ResponseHandler.unregister_handler`
        for use to unregister callbacks.

        See
        :meth:`~aioharmony.responsehandler.ResponseHandler.unregister_handler`
        in class
        :class:`~aioharmony.responsehandler.ResponseHandler` for further
        information on this method
        """
        return self._callback_handler.unregister_handler(*args, **kwargs)
