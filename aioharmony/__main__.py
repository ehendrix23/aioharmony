#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Module for querying and controlling Logitech Harmony devices."""

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from typing import Optional

import aioharmony.exceptions
from aioharmony.harmonyapi import HarmonyAPI, SendCommandDevice
from aioharmony.responsehandler import Handler
from aioharmony.const import ClientCallbackType, WEBSOCKETS, XMPP

# TODO: Add docstyle comments
# TODO: Clean up code styling

hub_client = None

_ROOTLOGGER = logging.getLogger()
_LOGGER = logging.getLogger(__name__)

class LoggingFilter(logging.Filter):
    def __init__(self, modules):
        self._modules = modules

    def filter(self, record):
        for module in self._modules:
            if record.name == module or re.search(module, record.name) is not None:
                return True
        return False

async def get_client(ip_address, protocol, show_responses) -> Optional[HarmonyAPI]:
    client = HarmonyAPI(ip_address=ip_address, protocol=protocol)

    def output_response(message):
        print(f"{client.name}: {message}")

    if show_responses:
        listen_callback = Handler(handler_obj=output_response,
                                  handler_name='output_response',
                                  once=False
                                  )
        client.register_handler(handler=listen_callback)

    print(f"Trying to connect to HUB with IP {ip_address}.")
    try:
        if await client.connect():
            print("Connected to HUB {} ({}) with firmware version {} and HUB ID {} using protocol {}".format(
                client.name,
                ip_address,
                client.fw_version,
                client.hub_id,
                client.protocol))
            return client
    except ConnectionRefusedError:
        print(f"Failed to connect to HUB {ip_address}.")

    print("An issue occurred trying to connect")

    return None


async def just_listen(client, args):
    # Create handler to output everything.
    def output_response(message):
        print(f"{datetime.today().strftime('%Y-%m-%d %H:%M:%S')} {client.name}: {message}")

    print("Starting to listen on HUB {} with firmware version {}".format(
        client.name,
        client.fw_version))

    # Register callback to show messages if not already done.
    if not args.show_responses:
        listen_callback = Handler(handler_obj=output_response,
                                  handler_name='output_response',
                                  once=False
                                  )
        client.register_handler(handler=listen_callback)

    return

async def listen_for_new_activities(client, _):

    def new_activity_starting(activity_info: tuple):
        activity_id, activity_name = activity_info
        if activity_id == -1:
            print(f"{datetime.today().strftime('%Y-%m-%d %H:%M:%S')} {client.name}: Powering off is starting.")
        else:
            print(f"{datetime.today().strftime('%Y-%m-%d %H:%M:%S')} {client.name}: New activity ID {activity_id} with name {activity_name} is starting.")

    def new_activity_started(activity_info: tuple):
        activity_id, activity_name = activity_info
        if activity_id == -1:
            print(f"{datetime.today().strftime('%Y-%m-%d %H:%M:%S')} {client.name}: Powering off completed.")
        else:
            print(f"{datetime.today().strftime('%Y-%m-%d %H:%M:%S')} {client.name}: New activity ID {activity_id} with name {activity_name} has started.")

    activity_id, activity_name = client.current_activity
    print(f"{datetime.today().strftime('%Y-%m-%d %H:%M:%S')} {client.name}: Current activity ID {activity_id} with name {activity_name}")

    callbacks = {
        "config_updated": client.callbacks.config_updated,
        "connect": client.callbacks.connect,
        "disconnect": client.callbacks.disconnect,
        "new_activity_starting": new_activity_starting,
        "new_activity": new_activity_started,
    }
    client.callbacks = ClientCallbackType(**callbacks)


# Functions for use on command line
async def show_config(client, _):
    """Connects to the Harmony and return current configuration.
    """
    config = client.config

    if config:
        print(f"HUB: {client.name}")
        print(f"\t {json.dumps(client.json_config, sort_keys=True, indent=4)}")
    else:
        print(f"HUB: {client.name} There was a problem retrieving the configuration")


async def show_detailed_config(client, _):
    """Connects to the Harmony and return current configuration.
    """
    config = client.hub_config

    if config:
        print(f"HUB: {client.name}")
        print(f"\t {json.dumps(client.hub_config, sort_keys=True, indent=4,separators=(',', ': '))}")
    else:
        print(f"HUB: {client.name} There was a problem retrieving the configuration")


async def show_current_activity(client, _):
    """Returns Harmony hub's current activity.
    """
    activity_id, activity_name = client.current_activity

    if activity_name:
        print(f"HUB: {client.name} {activity_name} ({activity_id})")
    elif activity_id:
        print(f"HUB: {client.name} activity_id")
    else:
        print(f"HUB: {client.name} Unable to retrieve current activity")


async def start_activity(client, args):
    """Connects to Harmony Hub and starts an activity

    Args:
        args (argparse): Argparse object containing required variables from
        command line

    """
    if args.activity is None:
        print(f"HUB: {client.name} No activity provided to start")
        return

    if (args.activity.isdigit()) or (args.activity == '-1'):
        activity_id = args.activity
    else:
        activity_id = client.get_activity_id(args.activity)
        if activity_id:
            print(f"HUB: {client.name} Found activity named {args.activity} ({activity_id})")
    if activity_id:
        status = await client.start_activity(activity_id)

        if status[0]:
            print(f"HUB: {client.name} Started Activity {args.activity}")
        else:
            print(f"HUB: {client.name} Activity start failed: {status[1]}")
    else:
        print(f"HUB: {client.name} Invalid activity: {args.activity}")

async def power_off(client, _):
    """Power off Harmony Hub.
    """
    status = await client.power_off()

    if status:
        print(f"HUB: {client.name} Powered Off")
    else:
        print(f"HUB: {client.name} Power off failed")


async def send_command(client, args):
    """Connects to the Harmony and send a simple command.

    Args:
        args (argparse): Argparse object containing required variables from
        command line

    """
    device_id = None
    if args.device_id.isdigit():
        if client.get_device_name(int(args.device_id)):
            device_id = args.device_id

    if device_id is None:
        device_id = client.get_device_id(str(args.device_id).strip())

    if device_id is None:
        print(f"HUB: {client.name} Device {args.device_id} is invalid.")
        return

    snd_cmmnd = SendCommandDevice(
        device=device_id,
        command=args.command,
        delay=args.hold_secs)

    snd_cmmnd_list = []
    for _ in range(args.repeat_num):
        snd_cmmnd_list.append(snd_cmmnd)
        if args.delay_secs > 0:
            snd_cmmnd_list.append(args.delay_secs)

    result_list = await client.send_commands(snd_cmmnd_list)

    if result_list:
        for result in result_list:
            print("HUB: {} Sending of command {} to device {} failed with code {}: "
                  "{}".format(
                      client.name,
                      result.command.command,
                      result.command.device,
                      result.code,
                      result.msg))
    else:
        print(f"HUB: {client.name} Command Sent")


async def change_channel(client, args):
    """Change channel

    Args:
        args (argparse): Argparse object containing required variables from
        command line

    """
    status = await client.change_channel(args.channel)

    if status:
        print(f"HUB: {client.name} Changed to channel {args.channel}")
    else:
        print(f"HUB: {client.name} Change to channel {args.channel} failed")


# def discover(args):
#     hubs = harmony_discovery.discover()
#     pprint(hubs)


async def sync(client, _):
    """Syncs Harmony hub to web service.
    Args:
        args (argparse): Argparse object containing required variables from
        command line

    Returns:
        Completion status
    """
    status = await client.sync()

    if status:
        print(f"HUB: {client.name} Sync complete")
    else:
        print(f"HUB: {client.name} Sync failed")

async def execute_per_hub(hub, args):

    # Connect to the HUB
    try:
        _LOGGER.debug("%s: Connecting to HUB", hub)
        hub_client = await get_client(hub,
                                      args.protocol,
                                      args.show_responses)
        if hub_client is None:
            return
    except aioharmony.exceptions.TimeOut:
        print("Action did not complete within a reasonable time.")
        return

    coroutine = None
    if hasattr(args, 'func'):
        coroutine = args.func(hub_client, args)

    # Execute provided request.
    if coroutine is not None:
        _LOGGER.debug("%s: Executing function.", hub)
        try:
            await coroutine
        except aioharmony.exceptions.TimeOut:
            print("Action did not complete within a reasonable time.")

    # Now sleep for provided time.
    if args.wait >= 0:
        _LOGGER.debug("%s: Waiting for %s seconds.", hub, args.wait)
        await asyncio.sleep(args.wait)
    else:
        _LOGGER.debug("%s: Waiting till cancelled", hub)
        while True:
            await asyncio.sleep(60)

    if hub_client:
        _LOGGER.debug("%s: Closing connection to HUB.", hub)
        try:
            await asyncio.wait_for(hub_client.close(), timeout=60)
        except aioharmony.exceptions.TimeOut:
            _LOGGER.debug("%s: Timeout trying to close connection to HUB.", hub)

    _LOGGER.debug("%s: All done with HUB.", hub)
async def run():
    """Main method for the script."""
    global hub_client

    parser = argparse.ArgumentParser(
        description='aioharmony - Harmony device control',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    required_flags = parser.add_mutually_exclusive_group(required=True)

    # Required flags go here.
    required_flags.add_argument('--harmony_ip',
                        help='IP Address of the Harmony device, multiple IPs can be specified as a comma separated'
                             ' list without spaces.')
    required_flags.add_argument('--discover',
                        action='store_true',
                        help='Scan for Harmony devices.')

    # Flags with default values go here.
    loglevels = dict((logging.getLevelName(level), level)
                     for level in [10, 20, 30, 40, 50])
    parser.add_argument('--protocol',
                        required=False,
                        choices=[WEBSOCKETS,XMPP],
                        help=('Protocol to use to connect to HUB. Note for XMPP one has to ensure that XMPP is enabled'
                              'on the hub.'))
    parser.add_argument('--loglevel',
                        default='ERROR',
                        choices=list(loglevels.keys()),
                        help='Logging level for all components to '
                             'print to the console.')
    parser.add_argument('--logmodules',
                        required=False,
                        type=str,
                        help='Restrict logging to modules specified. Multiple can be provided as a '
                             'comma separated list without any spaces. Use * to include any further submodules.')

    show_responses_parser = parser.add_mutually_exclusive_group(
        required=False)
    show_responses_parser.add_argument('--show_responses',
                                       dest='show_responses',
                                       action='store_true',
                                       help='Print out responses coming from '
                                            'HUB.')
    show_responses_parser.add_argument('--no-show_responses',
                                       dest='show_responses',
                                       action='store_false',
                                       help='Do not print responses coming '
                                            'from HUB.')
    show_responses_parser.set_defaults(show_responses=False)

    parser.add_argument('--wait',
                        required=False,
                        default=0,
                        type=int,
                        help='How long to wait in seconds after completion, '
                             'useful in combination with --show-responses.\n'
                             'Use -1 to wait infinite, otherwise has to be a '
                             'positive number.')

    subparsers = parser.add_subparsers()

    show_config_parser = subparsers.add_parser(
        'show_config', help='Print the Harmony device configuration.')
    show_config_parser.set_defaults(func=show_config)

    show_detailed_config_parser = subparsers.add_parser(
        'show_detailed_config', help='Print the detailed Harmony device'
                                     ' configuration.')
    show_detailed_config_parser.set_defaults(func=show_detailed_config)

    show_activity_parser = subparsers.add_parser(
        'show_current_activity', help='Print the current activity config.')
    show_activity_parser.set_defaults(func=show_current_activity)

    start_activity_parser = subparsers.add_parser(
        'start_activity', help='Switch to a different activity.')
    start_activity_parser.add_argument(
        '--activity', help='Activity to switch to, id or label.')
    start_activity_parser.set_defaults(func=start_activity)

    power_off_parser = subparsers.add_parser(
        'power_off', help='Stop the activity.')
    power_off_parser.set_defaults(func=power_off)

    sync_parser = subparsers.add_parser('sync', help='Sync the harmony.')
    sync_parser.set_defaults(func=sync)

    listen_parser = subparsers.add_parser('listen', help='Output everything '
                                                         'HUB sends out. Use in combination with --wait.')
    listen_parser.set_defaults(func=just_listen)

    new_activity_parser = subparsers.add_parser(
        'activity_monitor',
        help='Monitor and show when an activity is changing. Use in combination with --wait to keep monitoring for'
             'activities otherwise only current activity will be shown.')
    new_activity_parser.set_defaults(func=listen_for_new_activities)

    command_parser = subparsers.add_parser(
        'send_command', help='Send a simple command.')
    command_parser.add_argument(
        '--device_id',
        help='Specify the device id to which we will send the command.')
    command_parser.add_argument(
        '--command', help='IR Command to send to the device.')
    command_parser.add_argument(
        '--repeat_num', type=int, default=1,
        help='Number of times to repeat the command. Defaults to 1')
    command_parser.add_argument(
        '--delay_secs', type=float, default=0.4,
        help='Delay between sending repeated commands. Not used if only '
             'sending a single command. Defaults to 0.4 seconds')
    command_parser.add_argument(
        '--hold_secs', type=float, default=0,
        help='Number of seconds to "hold" before releasing. Defaults to 0.4'
             'seconds')
    command_parser.set_defaults(func=send_command)

    change_channel_parser = subparsers.add_parser(
        'change_channel', help='Change the channel')
    change_channel_parser.add_argument(
        '--channel', help='Channel to switch to.')
    change_channel_parser.set_defaults(func=change_channel)

    args = parser.parse_args()

    log_formatter = logging.Formatter('%(asctime)s:%(levelname)s:\t%(name)s\t%(message)s')
    log_stream = logging.StreamHandler()
    log_stream.setFormatter(log_formatter)
    _ROOTLOGGER.setLevel(loglevels[args.loglevel])
    _ROOTLOGGER.addHandler(log_stream)

    if args.logmodules is not None:
        log_modules = args.logmodules.split(",")
        log_filter = LoggingFilter(log_modules)
        log_stream.addFilter(log_filter)

    if args.wait < 0 and args.wait != -1:
        print("Invalid value provided for --wait.")
        parser.print_help()
        return

    if args.discover:
        # discover(args)
        pass
    else:
        if not hasattr(args, 'func') and not args.show_responses:
            parser.print_help()
            return

        hub_tasks = []
        hub_ips = args.harmony_ip.split(",")
        for hub in hub_ips:
            # Connect to the HUB
            hub_tasks.append(asyncio.ensure_future(execute_per_hub(hub, args)))

        results = await asyncio.gather(*hub_tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(
                    result,
                    Exception):
                raise result


def cancel_tasks(loop):

    _LOGGER.debug("Cancelling any tasks still running.")
    loop.run_until_complete(asyncio.sleep(1))
    for task in asyncio.all_tasks(loop):
        task.cancel()

    # Allow cancellations to be processed
    for x in range(10):
        loop.run_until_complete(asyncio.sleep(1))
        if len(asyncio.all_tasks(loop)) == 0:
            break

def main() -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run())
        cancel_tasks(loop)
        loop.close()

    except KeyboardInterrupt:
        print("Exit requested.")
        cancel_tasks(loop)
        loop.close()
        print("Closed.")

if __name__ == '__main__':
    sys.exit(main())
