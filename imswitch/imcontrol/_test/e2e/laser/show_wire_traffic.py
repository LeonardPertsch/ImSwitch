#!/usr/bin/env python3
"""Show exactly what goes over the wire to the ESP32 when a laser is toggled.

Uses the same library and the same call ImSwitch uses, one layer below the
HTTP API, so the payload printed here is byte-for-byte what ImSwitch sends:

    LaserController.setLaserActive
      -> ESP32LEDLaserManager.setEnabled
      -> uc2rest  laser.set_laser(channel, value)     <-- this script starts here
      -> /dev/ttyUSB0
      -> ESP32

With DEBUG=True uc2rest logs both directions:
    [SendingCommands]:{...}   what we sent      (mserial.py:577)
    [ProcessLines]:...        raw ESP32 reply   (mserial.py:419)
    [ProcessCommands]: {...}  parsed reply      (mserial.py:462)

ImSwitch must NOT be connected to the board - only one process owns the port.
"""
import logging
import os
import sys
import time

import uc2rest

PORT = os.environ.get("UC2_PORT", "/dev/ttyUSB0")
CHANNEL = int(os.environ.get("UC2_LASER_ID", "3"))  # channel_index == LASERid

logging.basicConfig(level=logging.DEBUG, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("wire")

client = uc2rest.UC2Client(
    host=None, port=80, identity="UC2_Feather",
    serialport=PORT, baudrate=115200,
    DEBUG=True, logger=log, skipFirmwareCheck=True,
)

try:
    print(f"\n===== set_laser(channel={CHANNEL}, value=500) =====")
    client.laser.set_laser(channel=CHANNEL, value=500)
    time.sleep(2)

    print(f"\n===== set_laser(channel={CHANNEL}, value=0) =====")
    client.laser.set_laser(channel=CHANNEL, value=0)
    time.sleep(1)
finally:
    client.close()
