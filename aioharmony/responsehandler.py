# -*- coding: utf-8 -*-

"""

This is the module contains the classes for the callback handler. It is used
by :class:`~aioharmony.client.HarmonyClient` class to wait for responses from
the HUB through callbacks.

"""

import asyncio
import logging
from typing import (
    List, NamedTuple, Optional, Pattern, Union, Tuple
)
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from aioharmony.helpers import call_callback
from aioharmony.handler import Handler

DEFAULT_TIMEOUT = 60

_LOGGER = logging.getLogger(__name__)

DataPatternType = Tuple[str, Pattern]

RespDataPatternType = Union[
    List[DataPatternType],
    DataPatternType
]

CallbackEntryType = NamedTuple('CallbackEntryType',
                               [('handler_uuid', str),
                                ('msgid', Optional[str]),
                                ('expiration', Optional[datetime]),
                                ('handler', Handler)
                                ])


class ResponseHandler:
    """

    Class to listen for json responses on the queue and then call
    registered handlers based on search patterns.

    This class is used by :class:`~aioharmony.client.HarmonyClient`, there is
    no need to use this class.

    The :class:`~aioharmony.client.HarmonyClient` class exposes methods
    :meth:`~ResponseHandler.register_handler` and
    :meth:`~ResponseHandler.unregister_handler` for registering
    additional handlers.

    :param message_queue: queue to listen on for JSON messages
    :type message_queue: asyncio.Queue
    """

    def __init__(self,
                 message_queue: asyncio.Queue) -> None:
        """"""
        self._message_queue = message_queue
        self._handler_list = []

        self._callback_task = asyncio.ensure_future(self._callback_handler())

    async def close(self):
        """Close all connections and tasks

           This should be called to ensure everything is stopped and
           cancelled out.
        """
        # Stop callback task
        if self._callback_task and not self._callback_task.done():
            self._callback_task.cancel()

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
        handler_uuid = str(uuid4())
        if expiration is None:
            expiration = handler.expiration

        if isinstance(expiration, timedelta):
            expiration = datetime.now(timezone.utc) + expiration

        if expiration is None:
            _LOGGER.debug("Registering handler %s with UUID %s",
                          handler.handler_name,
                          handler_uuid)
        else:
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=timezone.utc)

            _LOGGER.debug("Registering handler %s with UUID %s that will "
                          "expire on %s",
                          handler.handler_name,
                          handler_uuid,
                          expiration.astimezone())

        self._handler_list.append(CallbackEntryType(
            handler_uuid=handler_uuid,
            msgid=msgid,
            expiration=expiration,
            handler=handler
        ))
        return handler_uuid

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
        _LOGGER.debug("Unregistering handler with UUID %s",
                      handler_uuid)
        found_uuid = False
        for index in [index for index, element in enumerate(self._handler_list)
                      if element.handler_uuid == handler_uuid]:
            del self._handler_list[index]
            found_uuid = True
            break

        return found_uuid

    # pylint: disable=too-many-return-statements
    def _handler_match(self, dict_list, message, key=None):

        if key is not None:
            message = message.get(key)
            value = dict_list.get(key)
        else:
            value = dict_list

        if message is None or value is None:
            return False

        if isinstance(value, (dict, list)):
            # If they're different types then it is no match.
            # pylint: disable=unidiomatic-typecheck
            if type(message) != type(value):
                return False

            for new_key in value:
                if not self._handler_match(dict_list=value,
                                           message=message,
                                           key=new_key):
                    return False
            return True

        # value is a string or a pattern. If message is a dict or a list
        # then it is not a match.
        # Unable to check if message and value type are same when value is
        # not a list or dict as it can then be a string or a pattern whereas
        # message should be a string to do matching.
        if isinstance(message, (dict, list)):
            return False

        if isinstance(value, Pattern):
            if value.search(message) is None:
                return False
            return True

        return value == message

    def _get_handlers(self,
                      message: dict) -> List[CallbackEntryType]:
        """
        Find the handlers to be called for the JSON message received

        :param message: JSON message to use
        :type message: dict
        :return: List of Handler objects.
        :rtype: List[Handler]
        """

        callback_list = []
        for handler in self._handler_list:

            if handler.msgid is not None:
                if message.get('id') is None or \
                        message.get('id') != handler.msgid:
                    _LOGGER.debug("No match on msgid for %s",
                                  handler.handler.handler_name)
                    continue

            if handler.handler.resp_json is not None:
                if not self._handler_match(
                        dict_list=handler.handler.resp_json,
                        message=message):
                    _LOGGER.debug("No match for handler %s",
                                  handler.handler.handler_name)
                    continue

            _LOGGER.debug("Match for %s", handler.handler.handler_name)
            callback_list.append(handler)

        return callback_list

    def _unregister_expired_handlers(self,
                                     single_handler: CallbackEntryType =
                                     None) -> bool:
        """
        Unregisters any expired handlers based on their expiration
        datetime. Will check the handler dict instead of the list if provided
        :param single_handler:  Handler dict as it is put in the handler
                                list by register_handler.
                                DEFAULT = NONE
        :type single_handler: dict
        :return: True if one or more handlers were unregistered,
                 otherwise False
        :rtype: bool
        """

        if single_handler is None:
            handler_list = self._handler_list
        else:
            handler_list = [single_handler]

        removed_expired = False
        for handler in handler_list:
            if handler.expiration is not None:
                if datetime.now(timezone.utc) > handler.expiration:
                    _LOGGER.debug("Handler %s with UUID %s has "
                                  "expired, removing: %s",
                                  handler.handler.handler_name,
                                  handler.handler_uuid,
                                  handler.expiration.astimezone())
                    self.unregister_handler(handler.handler_uuid)
                    removed_expired = True

        return removed_expired

    # pylint: disable=broad-except
    async def _callback_handler(self) -> None:
        """
        Listens on the queue for JSON messages and then processes them by
        calling any handler(s)
        """
        _LOGGER.debug("Callback handler started")

        while True:
            # Put everything here in a try block, we do not want this
            # to stop running out due to an exception.
            try:
                # Wait for something to appear on the queue.
                message = await self._message_queue.get()
                _LOGGER.debug("Message received: %s", message)

                # Go through list and call
                for handler in self._get_handlers(message=message):

                    # Make sure handler hasn't expired yet.
                    if self._unregister_expired_handlers(
                            single_handler=handler):
                        # Was expired and now removed, go on with next one.
                        continue

                    call_callback(
                        callback_handler=handler.handler.handler_obj,
                        result=message,
                        callback_uuid=handler.handler_uuid,
                        callback_name=handler.handler.handler_name
                    )

                    # Remove the handler from the list if it was only to be
                    # called once.
                    if handler.handler.once:
                        self.unregister_handler(handler.handler_uuid)

                # Go through all handlers and remove expired ones IF
                # currently
                # nothing in the queue.
                if self._message_queue.empty():
                    # Go through list and remove all expired ones.
                    _LOGGER.debug("Checking for expired handlers")
                    self._unregister_expired_handlers()

            except asyncio.CancelledError:
                _LOGGER.debug("Received STOP for callback handler")
                break

            # Need to catch everything here to prevent an issue in a
            # from causing the handler to exit.
            except Exception as exc:
                _LOGGER.exception("Exception in callback handler: %s", exc)

        # Reset the queue.
        self._message_queue = asyncio.Queue()

        _LOGGER.debug("Callback handler stopped.")
