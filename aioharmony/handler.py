# -*- coding: utf-8 -*-

"""
This is module specifies the Handler class.

A number of HANDLER constants have been defined here, do not USE these,
copy them instead.

"""
import re
from datetime import timedelta
from aioharmony.const import CallbackType

DEFAULT_TIMEOUT = 60


class Handler:
    """
    This class is to be used to create Handler objects for registering new
    callbacks through method
    :meth:`aioharmony.client.HarmonyClient.register_handler`

    **handler_obj:** This parameter can accept one of the following types of
    objects.


    * Future : result of the future will be set to the json that was received.

    * Event : event will be triggered (set). Event will not be cleared, this
      is the responsibility of the object listening for the event.

    * Coroutine : Coroutine will be put on the task loop with one (1)
      argument containing the json response.

    * Callback  : Callback will be called with one (1) argument containing
      the json response.

    :param handler_obj: Object to be called or set.
    :type handler_obj: CallbackType
    :param handler_name: Descriptive name for the handler. \
                         DEFAULT = None
    :type handler_name: Optional[str]
    :param resp_json: A JSON dict used to match the response upon.
                      DEFAULT = None
    :type resp_json: dict
    :param once: Only execute callback once (True) or keep callback until
                removed explicitly (False)
                DEFAULT = True
    :type once: Optional[bool]
    :param expiration: How long handler should be active once registered
                       before being removed.
                       DEFAULT = None
    :type expiration: Optional[timedelta]

    """

    # pylint: disable=too-many-arguments
    def __init__(self,
                 handler_obj: CallbackType,
                 handler_name: str = None,
                 resp_json: dict = None,
                 once: bool = True,
                 expiration: timedelta = None) -> None:
        self._handler_obj = handler_obj
        self._handler_name = handler_name
        self._resp_json = resp_json
        self._once = once
        self._expiration = expiration

    def __copy__(self):
        json_resp = dict()
        json_resp.update(self._resp_json)
        new_handler = Handler(
            handler_obj=self._handler_obj,
            handler_name=self._handler_name,
            resp_json=json_resp,
            once=self._once,
            expiration=self._expiration
        )
        return new_handler

    @property
    def handler_obj(self) -> CallbackType:
        """

        :param value: New handler_obj
        :type value: CallbackType
        :return: Returns handler_obj
        :rtype: CallbackType
        """
        return self._handler_obj

    @handler_obj.setter
    def handler_obj(self, value: CallbackType) -> None:
        """"""
        self._handler_obj = value

    @property
    def handler_name(self) -> str:
        """

        :param value: New handler_name
        :type value: str
        :return: Returns handler_name
        :rtype: str
        """
        return self._handler_name

    @handler_name.setter
    def handler_name(self, value: str) -> None:
        """"""
        self._handler_name = value

    @property
    def resp_json(self) -> dict:
        """

        :param value: New resp_type
        :type value: dict
        :return: Returns resp_type
        :rtype: dict
        """
        return self._resp_json

    @resp_json.setter
    def resp_json(self, value: dict) -> None:
        """"""
        self._resp_json = value

    @property
    def once(self) -> bool:
        """

        :param value: New once
        :type value: bool
        :return: Returns once
        :rtype: bool
        """
        return self._once

    @once.setter
    def once(self, value: bool) -> None:
        """"""
        self._once = value

    @property
    def expiration(self) -> timedelta:
        """

        :param value: New expiration
        :type value: timedelta
        :return: Returns expiration
        :rtype: timedelta
        """
        return self._expiration

    @expiration.setter
    def expiration(self, value: timedelta) -> None:
        """"""
        self._expiration = value


#
#
# Define a number of common handlers.
#
# These are NOT to be used as such, instead they HAVE TO BE copied.
#
#


def dummy_callback(message):
    return message


HANDLER_NOTIFY = Handler(
    handler_obj=dummy_callback,
    handler_name='Notification_Received',
    resp_json={
        'type': re.compile(r'^connect\.stateDigest\?notify$')
    },
    once=False
)

HANDLER_START_ACTIVITY_FINISHED = Handler(
    handler_obj=dummy_callback,
    handler_name='Activity_Changed',
    resp_json={
        'type': re.compile(r'^harmony\.engine\?startActivityFinished$')
    },
    once=False
)

HANDLER_RUN_ACTIVITY = Handler(
    handler_obj=dummy_callback,
    handler_name='runactivity',
    resp_json={
        'cmd': re.compile(r'^harmony\.activityengine\?runactivity$')
    },
    once=False,
    expiration=timedelta(seconds=DEFAULT_TIMEOUT * 5)
)

HANDLER_START_ACTIVITY_IN_PROGRESS = Handler(
    handler_obj=dummy_callback,
    handler_name='progress_startactivity',
    resp_json={
        'cmd':  re.compile(r'^harmony\.engine\?startActivity$'),
        'code': 100
    },
    once=False,
    expiration=timedelta(seconds=DEFAULT_TIMEOUT * 5)
)

HANDLER_HELPDISCRETES = Handler(
    handler_obj=dummy_callback,
    handler_name='progress_discrete',
    resp_json={
        'cmd':  re.compile(r'^harmony\.engine\?helpdiscretes$'),
        'data': {
            'isHelpDiscretes': 'true'
        }
    },
    once=False,
    expiration=timedelta(seconds=DEFAULT_TIMEOUT * 5)
)

HANDLER_START_ACTIVITY_COMPLETE = Handler(
    handler_obj=dummy_callback,
    handler_name='startactivity_or_discrete',
    resp_json={
        'cmd': re.compile(
            r'(^harmony\.engine\?helpdiscretes$)|'
            r'(^harmony\.engine\?startActivity$)'
        )
    },
    once=False,
    expiration=timedelta(seconds=DEFAULT_TIMEOUT * 5)
)
