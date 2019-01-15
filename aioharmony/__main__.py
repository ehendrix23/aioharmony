#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Module for querying and controlling Logitech Harmony devices."""

import argparse
import asyncio
import json
import logging
import sys
from typing import Optional

import aioharmony.exceptions
from aioharmony.harmonyapi import HarmonyAPI, SendCommandDevice
from aioharmony.responsehandler import Handler

# TODO: Add docstyle comments
# TODO: Clean up code styling

hub_client = None


async def get_client(ip_address, show_responses) -> Optional[HarmonyAPI]:
    client = HarmonyAPI(ip_address)

    def output_response(message):
        print(message)

    listen_callback = Handler(handler_obj=output_response,
                              handler_name='output_response',
                              once=False
                              )
    if show_responses:
        client.register_handler(handler=listen_callback)

    if await client.connect():
        print("Connected to HUB {} with firmware version {}".format(
            client.name,
            client.fw_version))
        return client

    print("An issue occured trying to connect")

    return None


async def just_listen(client, _):
    # Create handler to output everything.
    print("Starting to listen on HUB {} with firmware version {}".format(
        client.name,
        client.fw_version))


# Functions for use on command line
async def show_config(client, _):
    """Connects to the Harmony and return current configuration.
    """
    config = client.config

    if config:
        print(json.dumps(client.json_config, sort_keys=True, indent=4))
    else:
        print("There was a problem retrieving the configuration")


async def show_detailed_config(client, _):
    """Connects to the Harmony and return current configuration.
    """
    config = client.config

    if config:
        print(json.dumps(config, sort_keys=True, indent=4,
                         separators=(',', ': ')))
    else:
        print("There was a problem retrieving the configuration")


async def show_current_activity(client, _):
    """Returns Harmony hub's current activity.
    """
    activity_id, activity_name = client.current_activity

    if activity_name:
        print("{} ({})".format(activity_name, activity_id))
    elif activity_id:
        print(activity_id)
    else:
        print('Unable to retrieve current activity')


async def start_activity(client, args):
    """Connects to Harmony Hub and starts an activity

    Args:
        args (argparse): Argparse object containing required variables from
        command line

    """
    if args.activity is None:
        print("No activity provided to start")
        return

    if (args.activity.isdigit()) or (args.activity == '-1'):
        activity_id = args.activity
    else:
        activity_id = client.get_activity_id(args.activity)
        if activity_id:
            print('Found activity named %s (id %s)' % (args.activity,
                                                       activity_id))

    if activity_id:
        status = await client.start_activity(activity_id)

        if status[0]:
            print('Started Actvivity, message: ', status[1])
        else:
            print('Activity start failed: ', status[1])


async def power_off(client, _):
    """Power off Harmony Hub.
    """
    status = await client.power_off()

    if status:
        print('Powered Off')
    else:
        print('Power off failed')


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
        print("Device {} is invalid.".format(args.device_id))
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
            print("Sending of command {} to device {} failed with code {}: "
                  "{}".format(
                      result.command.command,
                      result.command.device,
                      result.code,
                      result.msg))
    else:
        print('Command Sent')


async def change_channel(client, args):
    """Change channel

    Args:
        args (argparse): Argparse object containing required variables from
        command line

    """
    status = await client.change_channel(args.channel)

    if status:
        print('Changed to channel {}'.format(args.channel))
    else:
        print('Change to channel {} failed'.format(args.channel))


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
        print('Sync complete')
    else:
        print("Sync failed")


async def run():
    """Main method for the script."""
    global hub_client

    parser = argparse.ArgumentParser(
        description='aioharmony - Harmony device control',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    required_flags = parser.add_mutually_exclusive_group(required=True)

    # Required flags go here.
    required_flags.add_argument('--harmony_ip',
                                help='IP Address of the Harmony device.')
    required_flags.add_argument('--discover',
                                action='store_true',
                                help='Scan for Harmony devices.')

    # Flags with default values go here.
    loglevels = dict((logging.getLevelName(level), level)
                     for level in [10, 20, 30, 40, 50])
    parser.add_argument('--harmony_port',
                        required=False,
                        default=5222,
                        type=int,
                        help=('Network port that the Harmony is listening'
                              ' on.'))
    parser.add_argument('--loglevel',
                        default='INFO',
                        choices=list(loglevels.keys()),
                        help='Logging level for all components to '
                             'print to the console.')

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
                                                         'HUB sends out')
    listen_parser.set_defaults(func=just_listen)

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

    logging.basicConfig(
        level=loglevels[args.loglevel],
        format='%(asctime)s:%(levelname)s:\t%(name)s\t%(message)s')

    if args.wait < 0 and args.wait != -1:
        print("Invalid value provided for --wait.")
        parser.print_help()
        return

    if args.discover:
        # discover(args)
        pass
    else:
        coroutine = None
        if not hasattr(args, 'func') and not args.show_responses:
            parser.print_help()
            return

        # Connect to the HUB
        try:
            hub_client = await get_client(args.harmony_ip,
                                          args.show_responses)
            if hub_client is None:
                return
        except aioharmony.exceptions.TimeOut:
            print("Action did not complete within a reasonable time.")
            raise

        if hasattr(args, 'func'):
            coroutine = args.func(hub_client, args)

        # Execute provided request.
        try:
            if coroutine is not None:
                await coroutine
        except aioharmony.exceptions.TimeOut:
            print("Action did not complete within a reasonable time.")
            raise

        # Now sleep for provided time.
        if args.wait >= 0:
            await asyncio.sleep(args.wait)
        else:
            while True:
                await asyncio.sleep(60)

        if hub_client:
            await asyncio.wait_for(hub_client.close(), timeout=10)
            hub_client = None


def main() -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run())
        while asyncio.all_tasks(loop):
            loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop)))
        loop.close()
    except KeyboardInterrupt:
        print("Exit requested.")
        if hub_client is not None:
            loop.run_until_complete(
                asyncio.wait_for(hub_client.close(), timeout=10)
            )
        print("Closed.")


if __name__ == '__main__':
    sys.exit(main())
