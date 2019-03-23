aioharmony
==========

Python library for programmatically using a Logitech Harmony Link or Ultimate Hub.

This library originated from `iandday/pyharmony <https://github.com/iandday/pyharmony>`__ which was a fork
of `bkanuka/pyharmony <https://github.com/bkanuka/pyharmony>`__ with the intent to:

- Make the harmony library asyncio
- Ability to provide one's own custom callbacks to be called
- Automatic reconnect, even if re-connection cannot be established for a time
- More easily get the HUB configuration through API call
- Additional callbacks: connect, disconnect, HUB configuration updated
- Using unique msgid's ensuring that responses from the HUB are correctly managed.

Protocol
--------

As the harmony protocol is being worked out, notes will be in PROTOCOL.md. Currently it is using web sockets
due to a change by Logitech. Logitech has informed they will re-open XMPP sometime in January/2019. Once re-opened
this library will be moved to use XMPP.

Status
------

* Retrieving current activity
* Querying for entire device information
* Querying for activity information only
* Querying for current activity
* Starting Activity
* Sending Command
* Changing channels
* Custom callbacks.

Usage
-----

.. code:: bash

    aioharmony - Harmony device control

    usage: aioharmony [-h] (--harmony_ip HARMONY_IP | --discover)
                      [--harmony_port HARMONY_PORT]
                      [--loglevel {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
                      [--show_responses | --no-show_responses] [--wait WAIT]
                      {show_config,show_detailed_config,show_current_activity,start_activity,power_off,sync,listen,send_command,change_channel}
                      ...

    aioharmony - Harmony device control

    positional arguments:
      {show_config,show_detailed_config,show_current_activity,start_activity,power_off,sync,listen,send_command,change_channel}
        show_config         Print the Harmony device configuration.
        show_detailed_config
                            Print the detailed Harmony device configuration.
        show_current_activity
                            Print the current activity config.
        start_activity      Switch to a different activity.
        power_off           Stop the activity.
        sync                Sync the harmony.
        listen              Output everything HUB sends out
        send_command        Send a simple command.
        change_channel      Change the channel

    optional arguments:
      -h, --help            show this help message and exit
      --harmony_ip HARMONY_IP
                            IP Address of the Harmony device. (default: None)
      --discover            Scan for Harmony devices. (default: False)
      --harmony_port HARMONY_PORT
                            Network port that the Harmony is listening on.
                            (default: 5222)
      --loglevel {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                            Logging level for all components to print to the
                            console. (default: INFO)
      --show_responses      Print out responses coming from HUB. (default: False)
      --no-show_responses   Do not print responses coming from HUB. (default:
                            False)
      --wait WAIT           How long to wait in seconds after completion, useful
                            in combination with --show-responses. Use -1 to wait
                            infinite, otherwise has to be a positive number.
                            (default: 0)


Release Notes
-------------

0.1.0. Initial Release

0.1.2. Fixed:
    - Enable callback connect only once initial connect and initialization is completed.
    - Fix exception when activity/device name/id is None when trying to retrieve name/id.
    - Fixed content type and name of README in setup.py
0.1.3. Fixed:
    - Retry connect on reconnect
0.1.4. Fixed:
    - Exception when retrieve_hub_info failed to retrieve information
    - call_callback helper would never return True on success.
    - Retry connect on reconnect (was not awaited upon)
0.1.5. Fixed:
    - Exception when an invalid command was sent to HUB (or sending command failed on HUB).
    - Messages for failed commands were not printed in main.
0.1.6. Fixed:
    - Ignore response code 200 when for sending commands
    - Upon reconnect, errors will be logged on 1st try only, any subsequent retry until connection is successful will
      only provide DEBUG log entries.
0.1.7. Fixed:
    - Fix traceback if no configuration retrieved or items missing from configuration (i.e. no activities)
    - Retrieve current activity only after retrieving configuration
0.1.8. Fixed:
    - Fix traceback if HUB info is not received.
    - Fix for new HUB version 4.15.250. (Thanks to `reneboer <https://github.com/reneboer>`__ for providing the quick fix).

     NOTE: This version will ONLY work with 4.15.250 or potentially higher. It will not work with lower versions!
0.1.9. Fixed:
    - Fixed "Network unreachable" or "Host unreachable" on certain installations (i.e. in Docker, HassIO)
0.1.10. Changed:
    - On reconnect the wait time will now start at 1 seconds and double every time with a maximum of 30 seconds.
    - Reconnect sometimes might not work if request to close was received over web socket but it never was closed.
0.1.11. Changed:
    - Timeout changed from 30 seconds to 5 seconds for network activity.
    - For reconnect, first wait for 1 second before starting reconnects.

TODO
----

* Redo discovery for asyncio. This will be done once XMPP is re-implemented by Logitech
* More items can be done from the Harmony iOS app; determining what could be done within the library as well
* Is it possible to update device configuration?
