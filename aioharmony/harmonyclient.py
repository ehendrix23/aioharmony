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
from typing import List, NamedTuple, Optional, Union
from uuid import uuid4
from async_timeout import timeout

import aioharmony.exceptions
import aioharmony.handler as handlers
from aioharmony.helpers import call_callback, search_dict
from aioharmony.handler import CallbackType
from aioharmony.hubconnector import HubConnector
from aioharmony.responsehandler import Handler, ResponseHandler

_LOGGER = logging.getLogger(__name__)

DEFAULT_CMD = 'vnd.logitech.connect'
DEFAULT_DISCOVER_STRING = '_logitech-reverse-bonjour._tcp.local.'
DEFAULT_HUB_PORT = '8088'
DEFAULT_TIMEOUT = 60

DEFAULT_HARMONY_MIME = 'vnd.logitech.harmony/vnd.logitech.harmony.engine'
HUB_COMMANDS = {
    'change_channel':       {
        'mime':    'harmony.engine',
        'command': 'changeChannel'
    },
    'get_config':           {
        'mime':    DEFAULT_HARMONY_MIME,
        'command': 'config'
    },
    'get_current_activity': {
        'mime':    DEFAULT_HARMONY_MIME,
        'command': 'getCurrentActivity'
    },
    'send_command':         {
        'mime':    DEFAULT_HARMONY_MIME,
        'command': 'holdAction'
    },
    'start_activity':       {
        'mime':    'harmony.activityengine',
        'command': 'runactivity'
    },
    'sync':                 {
        'mime':    'setup.sync',
        'command': None
    }
}

SendCommandDevice = NamedTuple('SendCommandDevice',
                               [('device', int),
                                ('command', str),
                                ('delay', float)
                                ])
SendCommandDelay = float
SendCommand = Union[SendCommandDevice, SendCommandDelay]
SendCommandList = List[SendCommand]
SendCommandArg = Union[SendCommand, SendCommandList]

SendCommandResponse = NamedTuple('SendCommandResponse',
                                 [('command', SendCommandDevice),
                                  ('code', str),
                                  ('msg', str)
                                  ])


# TODO: Add docstyle comments
# TODO: Clean up code styling
# TODO: Add handler for connect so that we can update the HUB configuration
#  anytime we connect

# pylint: disable=too-many-public-methods
# pylint: disable=too-many-instance-attributes
class HarmonyClient:
    """An websocket client for connecting to the Logitech Harmony devices."""

    # pylint: disable=too-many-arguments
    def __init__(self,
                 ip_address: str,
                 connect_callback: CallbackType = None,
                 disconnect_callback: CallbackType = None,
                 new_activity_callback: CallbackType = None,
                 config_updated_callback: CallbackType = None,
                 loop=None):
        _LOGGER.debug("%s: Initialize", ip_address)
        self._ip_address = ip_address
        self._connect_callback = connect_callback
        self._disconnect_callback = disconnect_callback
        self._new_activity_callback = new_activity_callback
        self._config_updated_callback = config_updated_callback
        self._loop = loop if loop else asyncio.get_event_loop()

        self._hub_info_dict = {}

        self._config = None
        self._current_activity = None
        self._activities = None
        self._devices = None
        self._hub_config_version = None

        # Get the queue on which JSON responses will be put
        self._response_queue = asyncio.Queue()
        # Get the Hub Connection
        self._hub_connection = HubConnector(
            ip_address=self._ip_address,

            response_queue=self._response_queue)
        # Get the Response Handler
        self._callback_handler = ResponseHandler(
            message_queue=self._response_queue)

        # Create the lock for sending commands or starting an activity
        self._snd_cmd_act_lck = asyncio.Lock()

        # Create the lock for getting HUB information.
        self._sync_lck = asyncio.Lock()

        # Create the activity start handler object
        handler = copy.copy(handlers.HANDLER_START_ACTIVITY_FINISHED)
        handler.handler_obj = self._update_activity_callback
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
    def name(self) -> Optional[str]:
        name = self.hub_info.get('friendlyName')
        return name if name is not None else self._ip_address

    @property
    def connect_callback(self) -> CallbackType:
        return self._hub_connection.connect_callback

    @connect_callback.setter
    def connect_callback(self, value: CallbackType) -> None:
        self._hub_connection.connect_callback = value

    @property
    def disconnect_callback(self) -> CallbackType:
        return self._hub_connection.disconnect_callback

    @disconnect_callback.setter
    def disconnect_callback(self, value: CallbackType) -> None:
        self._hub_connection.disconnect_callback = value

    @property
    def new_activity_callback(self) -> CallbackType:
        return self._new_activity_callback

    @new_activity_callback.setter
    def new_activity_callback(self, value: CallbackType) -> None:
        self._new_activity_callback = value

    @property
    def config_updated_callback(self) -> CallbackType:
        return self._config_updated_callback

    @config_updated_callback.setter
    def config_updated_callback(self, value: CallbackType) -> None:
        self._config_updated_callback = value

    @property
    def config(self) -> dict:
        return self._config

    @property
    def current_activity_id(self) -> int:
        return self._current_activity

    @property
    def hub_info(self) -> dict:
        return self._hub_info_dict

    async def connect(self) -> bool:
        """

        :return: True if connection was successful, False if it was not.
        :rtype: bool
        :raises: :class:`~aioharmony.exceptions.HarmonyClientTimeOutException`
        """
        try:
            with timeout(DEFAULT_TIMEOUT):
                if not await self._hub_connection.connect():
                    return False
        except asyncio.TimeoutError:
            raise aioharmony.exceptions.HarmonyClientTimeOutException

        # Get all the HUB information.
        await self.refresh_info_from_hub()
        return True

    async def close(self) -> None:
        """Close all connections and tasks

           This should be called to ensure everything is stopped and
           cancelled out.
        """
        if self._hub_connection:
            try:
                with timeout(DEFAULT_TIMEOUT):
                    await self._hub_connection.close()
            except asyncio.TimeoutError:
                raise aioharmony.exceptions.HarmonyClientTimeOutException

        if self._callback_handler:
            try:
                with timeout(DEFAULT_TIMEOUT):
                    await self._callback_handler.close()
            except asyncio.TimeoutError:
                raise aioharmony.exceptions.HarmonyClientTimeOutException

    async def disconnect(self) -> None:
        """Disconnect from Hub"""
        _LOGGER.debug("%s: Disconnecting from %s",
                      self.name,
                      self._ip_address)
        try:
            with timeout(DEFAULT_TIMEOUT):
                await self._hub_connection.disconnect()
        except asyncio.TimeoutError:
            raise aioharmony.exceptions.HarmonyClientTimeOutException

    async def refresh_info_from_hub(self) -> None:
        _LOGGER.debug("%s: Retrieving HUB information",
                      self.name)

        async with self._sync_lck:
            results = await asyncio.gather(self._get_config(),
                                           self._retrieve_hub_info(),
                                           self._get_current_activity(),
                                           return_exceptions=True
                                           )
            for idx, result in enumerate(results):
                if isinstance(
                        result,
                        aioharmony.exceptions.HarmonyClientTimeOutException):
                    # Timeout exception, just put out error then.
                    if idx == 0:
                        result_name = 'config'
                    elif idx == 1:
                        result_name = 'hub_info'
                    else:
                        result_name = 'get_current_activity'

                    _LOGGER.error("%s: Timeout trying to retrieve %s.",
                                  self.name,
                                  result_name)
                elif isinstance(result, Exception):
                    # Other exception, raise it.
                    raise result

        # If we were provided a callback handler then call it now.
        if self._config_updated_callback:
            call_callback(
                callback_handler=self._config_updated_callback,
                result=self.config,
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
        response = await self.send_to_hub(command='get_config')
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

        self._config = response.get('data')

        self._activities = list(
            {
                'name':       a['label'],
                'name_match': a['label'].lower(),
                'id':         int(a['id'])
            } for a in self._config.get('activity'))

        self._devices = list(
            {
                'name':       a['label'],
                'name_match': a['label'].lower(),
                'id':         int(a['id'])
            } for a in self._config.get('device'))

        return self._config

    async def _retrieve_hub_info(self) -> Optional[dict]:
        """Retrieve some information from the Hub."""
        try:
            with timeout(DEFAULT_TIMEOUT):
                response = await self._hub_connection.retrieve_hub_info()
        except asyncio.TimeoutError:
            raise aioharmony.exceptions.HarmonyClientTimeOutException

        self._hub_info_dict = response
        return self._hub_info_dict

    async def send_to_hub(self,
                          command: str,
                          params: dict = None,
                          msgid: str = None,
                          wait: bool = True) -> Union[dict, bool]:

        if msgid is None:
            msgid = str(uuid4())

        if params is None:
            params = {
                'verb':   'get',
                'format': 'json'
            }

        response = None
        if wait:
            response = self._loop.create_future()
            resp_handler = Handler(handler_obj=response,
                                   handler_name=command,
                                   once=True,
                                   expiration=timedelta(
                                       seconds=DEFAULT_TIMEOUT)
                                   )
            self.register_handler(handler=resp_handler,
                                  msgid=msgid)

        try:
            with timeout(DEFAULT_TIMEOUT):
                if await self._hub_connection.send(
                        command='{}?{}'.format(
                            HUB_COMMANDS[command]['mime'],
                            HUB_COMMANDS[command]['command']
                        ),
                        params=params,
                        msgid=msgid
                ) is None:
                    # There was an issue
                    return False
        except asyncio.TimeoutError:
            raise aioharmony.exceptions.HarmonyClientTimeOutException

        if not wait:
            return True

        # Wait for the response to be available.
        try:
            with timeout(DEFAULT_TIMEOUT):
                await response
        except asyncio.TimeoutError:
            raise aioharmony.exceptions.HarmonyClientTimeOutException

        return response.result()

    async def _get_current_activity(self) -> bool:
        """Update current activity when changed."""
        _LOGGER.debug("%s: Retrieving current activity", self.name)

        # Send the command to the HUB
        try:
            response = await self.send_to_hub(command='get_current_activity')
        except aioharmony.exceptions.HarmonyClientTimeOutException:
            _LOGGER.error("%s: Timeout trying to retrieve current activity.",
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

        self._current_activity = int(response['data']['result'])
        _LOGGER.debug("%s: Current activity: %s(%s)",
                      self.name,
                      self.get_activity_name(self._current_activity),
                      self._current_activity)

        # If we were provided a callback handler then call it now.
        if self._new_activity_callback:
            call_callback(
                callback_handler=self._new_activity_callback,
                result=(self._current_activity,
                        self.get_activity_name(
                            activity_id=self._current_activity)
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

            if current_hub_config_version is not None and \
                    current_hub_config_version != self._hub_config_version:
                _LOGGER.debug("%s: HUB configuration was updated, "
                              "new version is: %s",
                              self.name,
                              current_hub_config_version)
                # Get all the HUB information.
                self._hub_config_version = current_hub_config_version
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

        self._current_activity = new_activity
        _LOGGER.debug("%s: New activity: %s(%s)",
                      self.name,
                      self.get_activity_name(self._current_activity),
                      self._current_activity)

        # If we were provided a callback handler then call it now.
        if self._new_activity_callback:
            call_callback(
                callback_handler=self._new_activity_callback,
                result=(self._current_activity,
                        self.get_activity_name(self._current_activity)
                        ),
                callback_uuid=self._ip_address,
                callback_name='new_activity_callback'
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
            "async":      "true",
            "timestamp":  0,
            "args":       {
                "rule": "start"
            },
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
                    'total':     data.get('total')
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

        # Get the lock ensuring we're the only ones able to initiate
        async with self._snd_cmd_act_lck:
            response = await self.send_to_hub(command='start_activity',
                                              params=params,
                                              msgid=msgid,
                                              wait=False)
            if not response:
                unregister_handlers()

            try:
                with timeout(DEFAULT_TIMEOUT):
                    status = await activity_completed
            except asyncio.TimeoutError:
                raise aioharmony.exceptions.HarmonyClientTimeOutException
            finally:
                unregister_handlers()
                _LOGGER.debug("%s: Start activity %s (%s) has been completed",
                              self.name,
                              self.get_activity_name(activity_id),
                              activity_id)

        return status

    async def send_commands(self,
                            commands: SendCommandList) -> \
            List[SendCommandResponse]:

        _LOGGER.debug("%s: Sending commands to HUB", self.name)
        # Get the lock ensuring we're the only ones able to initiate
        command_future_list = []
        msgid_dict = {}
        async with self._snd_cmd_act_lck:
            for command in commands:
                if isinstance(command, SendCommandDelay):
                    await asyncio.sleep(command)
                    continue

                # Create the future to be set for the result.
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

                msgid = await self._send_command(command,
                                                 command_handler)
                if msgid is not None:
                    msgid_dict.update({msgid: command})

        # Go through the result list to determine there were any issues with
        # any of the commands set. Only if there is an issue would a response
        # have been received.
        error_response_list = []
        try:
            done, _pending = await asyncio.wait(command_future_list,
                                                timeout=1)
            for result_returned in done:
                msgid = result_returned.get('id')
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

                _LOGGER.debug("%s: Received code %s for command %s to device "
                              "%s: %s",
                              self.name,
                              result_returned.get('code'),
                              command_sent.command,
                              command_sent.device,
                              result_returned.get('msg')
                              )
                error_response_list.append(SendCommandResponse(
                    command=command_sent,
                    code=result_returned.get('code'),
                    msg=result_returned.get('msg')
                ))
        except asyncio.TimeoutError as exc:
            _LOGGER.debug("%s: Timed out waiting for send command errors: %s",
                          self.name,
                          exc)

        _LOGGER.debug("%s: Sending commands to HUB has been completed",
                      self.name)
        return error_response_list

    async def _send_command(self,
                            command: SendCommandDevice,
                            callback_handler: Handler) -> Optional[str]:
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
            "status":    "press",
            "timestamp": '0',
            "verb":      "render",
            "action":    '{{"command": "{}",'
                         '"type": "IRCommand",'
                         '"deviceId": "{}"}}'.format(command.command,
                                                     command.device)
        }
        msgid = str(uuid4())

        # Register the handler for this command.
        self.register_handler(handler=callback_handler,
                              msgid=msgid)

        # Send the command to the HUB
        response = await self.send_to_hub(command='send_command',
                                          params=params,
                                          msgid=msgid,
                                          wait=False)
        if not response:
            # There was an issue
            return None

        if command.delay > 0:
            await asyncio.sleep(command.delay)

        params['status'] = 'release'
        # Send the command to the HUB
        await self.send_to_hub(command='send_command',
                               params=params,
                               msgid=msgid,
                               wait=False)

        _LOGGER.debug("%s: Sending command %s to device %s (%s) with delay "
                      "%ss has been completed",
                      self.name,
                      command.command,
                      self.get_device_name(command.device),
                      command.device,
                      command.delay)
        return msgid

    def get_activity_id(self, activity_name) -> Optional[str]:
        """Find the activity ID for the provided activity name."""
        item = search_dict(match_value=activity_name.lower(),
                           key='name_match',
                           search_list=self._activities)
        return item.get('id') if item else None

    def get_activity_name(self, activity_id) -> Optional[str]:
        """Find the activity name for the provided ID."""
        item = search_dict(match_value=activity_id,
                           key='id',
                           search_list=self._activities)
        return item.get('name') if item else None

    def get_device_id(self, device_name) -> Optional[str]:
        """Find the device ID for the provided device name."""
        item = search_dict(match_value=device_name.lower(),
                           key='name_match',
                           search_list=self._devices)
        return item.get('id') if item else None

    def get_device_name(self, device_id) -> Optional[str]:
        """Find the device name for the provided ID."""
        item = search_dict(match_value=device_id,
                           key='name_match',
                           search_list=self._devices)
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
