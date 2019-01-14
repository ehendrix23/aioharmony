# -*- coding: utf-8 -*-

"""
Constants used throughout the modules
"""

import asyncio
from typing import Any, Callable, List, NamedTuple, Optional, Union

#
# DEFAULT values
#
DEFAULT_CMD = 'vnd.logitech.connect'
DEFAULT_DISCOVER_STRING = '_logitech-reverse-bonjour._tcp.local.'
DEFAULT_HUB_PORT = '8088'
DEFAULT_HARMONY_MIME = 'vnd.logitech.harmony/vnd.logitech.harmony.engine'

#
# The HUB commands that can be send
#
HUB_COMMANDS = {
    'change_channel':       {
        'mime':    'harmony.engine',
        'command': 'changeChannel'
    },
    'get_current_state':    {
        'mime': 'vnd.logitech.connect/vnd.logitech.statedigest',
        'command': 'get'
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

#
# Different types used within aioharmony
#

# Type for callback parameters
CallbackType = Union[
    asyncio.Future,
    asyncio.Event,
    Callable[[object, Optional[Any]], Any]
]

ClientConfigType = NamedTuple('ClientConfigType',
                              [('config', dict),
                               ('info', dict),
                               ('config_version', Optional[int]),
                               ('activities', List[dict]),
                               ('devices', List[dict])
                               ])

# Type for a command to send to the HUB
SendCommandDevice = NamedTuple('SendCommandDevice',
                               [('device', int),
                                ('command', str),
                                ('delay', float)
                                ])

# Type for send command to aioharmony,
SendCommand = Union[SendCommandDevice, Union[float, int]]
SendCommandArg = Union[SendCommand, List[SendCommand]]

# Response from send commands.
SendCommandResponse = NamedTuple('SendCommandResponse',
                                 [('command', SendCommandDevice),
                                  ('code', str),
                                  ('msg', str)
                                  ])
