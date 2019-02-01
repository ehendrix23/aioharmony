# -*- coding: utf-8 -*-

"""
This is the main module containing the class to be imported and used:
from aioharmony.harmonyapi import HarmonyAPI

The HarmonyAPI class is a wrapper around the HarmonyClient class
which represents the Harmony Hub.
Using the methods of this class allows one to query or send commands to the
Hub.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Union
from aioharmony.const import (
    ClientConfigType, SendCommandArg, SendCommandDevice, SendCommandResponse
)
from aioharmony.harmonyclient import ClientCallbackType, HarmonyClient
from aioharmony.handler import Handler

_LOGGER = logging.getLogger(__name__)

# Making these types available for import.
ClientConfigType = ClientConfigType
SendCommandDevice = SendCommandDevice

# TODO: Add docstyle comments
# TODO: Clean up code styling


# pylint: disable=too-many-public-methods
class HarmonyAPI:

    # pylint: disable=too-many-arguments
    def __init__(self,
                 ip_address: str,
                 callbacks: ClientCallbackType = None,
                 loop: asyncio.AbstractEventLoop = None) -> None:
        _LOGGER.debug("%s: Initialize", ip_address)
        loop = loop if loop else asyncio.get_event_loop()

        self._harmony_client = HarmonyClient(
            ip_address=ip_address,
            callbacks=callbacks,
            loop=loop
        )

    @property
    def ip_address(self) -> str:
        return self._harmony_client.ip_address

    @property
    def hub_config(self) -> ClientConfigType:
        return self._harmony_client.hub_config

    @property
    def name(self) -> Optional[str]:
        return self._harmony_client.name

    @property
    def email(self) -> Optional[str]:
        return self.hub_config.info.get('email')

    @property
    def account_id(self) -> Optional[str]:
        return self.hub_config.info.get('accountId')

    @property
    def fw_version(self) -> Optional[str]:
        return self.hub_config.info.get('current_fw_version')

    @property
    def current_activity(self) -> tuple:
        return self._harmony_client.current_activity_id, \
            self._harmony_client.get_activity_name(
                self._harmony_client.current_activity_id)

    @property
    def config(self) -> dict:
        return self.hub_config.config

    @property
    def json_config(self) -> dict:
        """Returns configuration as a dictionary (json)"""

        result = {}
        config = self.config
        activity_dict = {}

        for activity in config.get('activity', []):
            activity_dict.update({activity['id']: activity['label']})

        result.update(Activities=activity_dict)

        devices_dict = {}
        for device in config.get('device', []):
            command_list = []
            for control_group in device.get('controlGroup', []):
                for function in control_group.get('function', []):
                    action = json.loads(function.get('action'))
                    if action is not None:
                        command_list.append(action.get('command'))

            device_dict = {
                'id': device.get('id'),
                'commands': command_list
            }

            devices_dict.update({device.get('label'): device_dict})

        result.update(Devices=devices_dict)

        return result

    @property
    def callbacks(self) -> ClientCallbackType:
        return self._harmony_client.callbacks

    @callbacks.setter
    def callbacks(self, value: ClientCallbackType) -> None:
        self._harmony_client.callbacks = value

    def get_activity_id(self, activity_name) -> Optional[str]:
        return self._harmony_client.get_activity_id(
            activity_name=activity_name)

    def get_activity_name(self, activity_id) -> Optional[str]:
        return self._harmony_client.get_activity_name(activity_id=activity_id)

    def get_device_id(self, device_name) -> Optional[str]:
        return self._harmony_client.get_device_id(device_name=device_name)

    def get_device_name(self, device_id) -> Optional[str]:
        return self._harmony_client.get_device_name(device_id=device_id)

    async def connect(self) -> bool:
        return await self._harmony_client.connect()

    async def close(self) -> None:
        await self._harmony_client.close()

    def register_handler(self,
                         handler: Handler,
                         msgid: str = None,
                         expiration: Union[
                             datetime,
                             timedelta] = None) -> str:
        """Register a handler.

        :param handler: Handler object to be registered
        :type handler: Handler
        :param msgid: Message ID to match upon.
                      DEFAULT = None
        :type msgid: Optional[str]
        :param expiration: How long or when handler should be removed. When
                           this is specified it will override what is set in
                           the Handler object.
                           If datetime is provided then UTC will be assumed
                           if tzinfo of the object is None.
                           DEFAULT = None
        :type expiration: Optional[Union[
                             datetime.datetime,
                             datetime.timedelta]]
        :return: Handler UUID number, this is a unique number for this handler
        :rtype: str
        """
        return self._harmony_client.register_handler(
            handler=handler,
            msgid=msgid,
            expiration=expiration)

    def unregister_handler(self,
                           handler_uuid: str) -> bool:
        """Unregister a handler.

        :param handler_uuid: Handler UUID, this is returned by
                             register_handler when registering the handler
        :type handler_uuid: str
        :return: True if handler was found and thus deleted, False if it was
                 not found
        :rtype: bool
        """
        return self._harmony_client.unregister_handler(
            handler_uuid=handler_uuid)

    async def sync(self) -> bool:
        """Syncs the harmony hub with the web service."""
        _LOGGER.debug("%s: Performing sync", self.name)
        # Send the command to the HUB
        response = await self._harmony_client.send_to_hub(command='sync')
        if not response or response.get('code') != 200:
            # There was an issue
            return False

        # Update our own information.
        await self._harmony_client.refresh_info_from_hub()
        return True

    async def start_activity(self, activity_id) -> tuple:
        return await self._harmony_client.start_activity(
            activity_id=activity_id)

    async def send_commands(self,
                            commands: SendCommandArg) -> \
            List[SendCommandResponse]:

        if isinstance(commands, list):
            _LOGGER.debug("%s: Sending commands to HUB", self.name)
        else:
            _LOGGER.debug("%s: Sending command to HUB", self.name)
            # Changing it to list.
            commands = [commands]

        return await self._harmony_client.send_commands(commands=commands)

    async def power_off(self) -> bool:
        """Turns the system off if it's on, otherwise it does nothing.

        Returns:
            True if the system becomes or is off
        """
        result = await self.start_activity(-1)
        return result[0]

    async def change_channel(self, channel: int) -> bool:
        """Change channel

        :param channel: Channel number
        :type channel: int
        :return: True if successfully, False if unsuccessfully
        :rtype: bool
        """
        _LOGGER.debug("%s: Changing channel to %s",
                      self.name,
                      channel)
        params = {
            "timestamp": 0,
            'channel': str(channel)
        }

        # Send the command to the HUB
        response = await self._harmony_client.send_to_hub(
            command='change_channel',
            params=params)
        if not response:
            # There was an issue
            return False

        return response.get('code') == 200
