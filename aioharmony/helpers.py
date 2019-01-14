# -*- coding: utf-8 -*-

"""
This only contains some helper routines that are used.
"""

import asyncio
import logging
from typing import List, Optional
from functools import partial

from aioharmony.handler import CallbackType

_LOGGER = logging.getLogger(__name__)


# pylint: disable=broad-except
def call_callback(callback_handler: CallbackType,
                  result: object,
                  callback_uuid: str,
                  callback_name: str) -> bool:
    # If we were provided a callback handler then call it now.
    if callback_handler is None:
        return False

    try:
        callback_result = call_raw_callback(
            callback=callback_handler,
            result=result,
            callback_uuid=callback_uuid,
            callback_name=callback_name
        )
    # Catching everything here.
    except Exception as exc:
        _LOGGER.exception("Exception in %s: %s",
                          callback_name,
                          exc)
        return False

    if not callback_result:
        _LOGGER.error("%s was not called due to mismatch in "
                      "callback type.",
                      callback_name
                      )
        return False

    return True


# TODO: Add this to Handler class
def call_raw_callback(callback: CallbackType,
                      result: object = None,
                      callback_uuid: str = None,
                      callback_name: str = None) -> bool:
    """
    Executes or sets the callback provided based on the type of callback:
      * Future : sets the result of the future to result provided
      * Event : sets the event
      * Coroutine: schedules the coroutine on the loop, result is
                   provided as the argument
      * Callback: Executes the callback, result is provided as the
                  argument

    :param callback: Handler that will be actioned
    :type callback: CallbackType
    :param callback_uuid: Unique number for the callback, only used in debug
                          logging
    :type callback_uuid:  str
    :param callback_name: Descriptive name for the callback, only used in
                          debug logging
    :type callback_name:  str
    :param result: object to set Future to, or to provide to the callback
                   routines
    :type result: object
    :return: True if callback was done successfully, False if it wasn't
    :rtype: bool
    """
    called_callback = False
    if asyncio.isfuture(callback):
        # It is a future, set the result of the future to
        # the message.
        if callback.done():
            _LOGGER.debug("Result of future %s with UUID %s was already "
                          "set",
                          callback_name,
                          callback_uuid)

        else:
            _LOGGER.debug("Future %s with UUID %s is set",
                          callback_name,
                          callback_uuid)
            callback.set_result(result)
            called_callback = True

        return called_callback

    if isinstance(callback, asyncio.Event):
        # It is an event, set the event.
        _LOGGER.debug("Setting event for handler %s with UUID %s",
                      callback_name,
                      callback_uuid)
        callback.set()
        return True

    if asyncio.iscoroutinefunction(callback):
        # Is a coroutine, schedule it on the loop.
        _LOGGER.debug("Scheduling coroutine %s with UUID %s",
                      callback_name,
                      callback_uuid)
        partial_func = partial(callback, result)
        partial_coro = asyncio.coroutine(partial_func)
        asyncio.ensure_future(partial_coro())
        return True

    if callable(callback):
        # This is a callback to be run. Execution of
        # these should be fast otherwise they're going
        # to block the loop.
        _LOGGER.debug("Calling callback %s with UUID %s",
                      callback_name,
                      callback_uuid)
        func = partial(callback, result)
        func()
        return True

    return False


def search_dict(match_value: object = None,
                key: str = None,
                search_list: List[dict] = None) -> Optional[dict]:
    """
    Returns the 1st element in a list containing dictionaries
    where the value of key provided matches the value provided.

    :param match_value: value to match upon (search for)
    :type match_value: object
    :param key: dictionary key to use for the match
    :type key: str
    :param search_list: List containing dictionary objects in which to search
    :type search_list: List[dict]
    :return: Dictionary object that matches
    :rtype: dict
    """
    if match_value is None or key is None or search_list is None:
        return None

    return next(
        (element for element in search_list
         if element[key] == match_value), None)
