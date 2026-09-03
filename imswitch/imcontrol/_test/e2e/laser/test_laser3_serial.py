"""Switch the LED on LASER3 (GPIO2) on and off and check what the firmware reports.

Talks straight to the ESP32 on /dev/ttyUSB0 - ImSwitch is not involved, and must
not be, because only one process can hold the port. Run it as a script for a
visible blink, or under pytest for the assertions.

The firmware answers in framed JSON:

    ++
    {"qid":1,"success":1}
    --
"""
import json
import os
import time

import pytest

try:
    import serial
except ImportError:
    serial = None

PORT = os.environ.get("UC2_PORT", "/dev/ttyUSB0")
BAUD_RATE = 115200

# This rig has the LED wired to LASER3, which the firmware drives on GPIO2.
LASER_ID = 3

# The board reboots when the port is opened, so give it time to come back up.
BOOT_SECONDS = 3.0


# Yield every complete top-level JSON object found in the firmware's output.
def parse_frames(text):
    depth = 0
    start = 0
    for index, character in enumerate(text):
        if character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                try:
                    yield json.loads(text[start:index + 1])
                except ValueError:
                    pass  # a truncated or interleaved frame, skip it


# A serial connection to the UC2 firmware, speaking its framed JSON protocol.
class UC2Board:

    # Open the port, reboot the board and throw away its boot log.
    def __init__(self, port=PORT, baud_rate=BAUD_RATE):
        self.connection = serial.Serial(port, baud_rate, timeout=0.2, write_timeout=1)
        self.reboot()
        self.read_for(BOOT_SECONDS)
        self.connection.reset_input_buffer()

    # Pulse the reset line, the same way esptool does.
    def reboot(self):
        self.connection.dtr = False
        self.connection.rts = True
        time.sleep(0.1)
        self.connection.rts = False
        time.sleep(0.05)

    # Collect everything the board sends for `seconds` and return it as text.
    def read_for(self, seconds):
        deadline = time.time() + seconds
        received = b""
        while time.time() < deadline:
            waiting = self.connection.in_waiting
            if waiting:
                received += self.connection.read(waiting)
            else:
                time.sleep(0.02)
        return received.decode("utf-8", "replace")

    # Send one command and return the last reply frame that contains `expected_key`.
    #
    # The board also emits unsolicited frames (stepper positions, for one), so we
    # cannot simply take the first thing that comes back.
    def ask(self, task, expected_key, wait=1.5, **payload):
        self.connection.reset_input_buffer()
        command = json.dumps({"task": task, **payload}) + "\n"
        self.connection.write(command.encode())
        self.connection.flush()

        replies = [frame for frame in parse_frames(self.read_for(wait))
                   if expected_key in frame]
        return replies[-1] if replies else None

    # Set this rig's laser channel to `value`, where 0 means off.
    def set_laser(self, value):
        return self.ask("/laser_act", "success",
                        LASERid=LASER_ID, LASERval=value, qid=1)

    # Return the PWM value the firmware currently reports for that channel.
    def get_laser_value(self):
        reply = self.ask("/laser_get", "laser", qid=2) or {}
        return reply.get("laser", {}).get(f"LASER{LASER_ID}val")

    # Switch the laser off, then release the port.
    def close(self):
        try:
            self.set_laser(0)
        finally:
            self.connection.close()


# Open the board, or skip the test if it is missing or already in use.
@pytest.fixture
def board():
    if serial is None:
        pytest.skip("pyserial not installed")
    if not os.path.exists(PORT):
        pytest.skip(f"{PORT} not present")
    try:
        connected_board = UC2Board()
    except serial.SerialException as exc:
        pytest.skip(f"cannot open {PORT}, is ImSwitch holding it? {exc}")

    yield connected_board
    connected_board.close()


# The firmware reports a non-zero PWM value after switching on, and zero after off.
@pytest.mark.hardware
def test_laser3_on_off(board):
    board.set_laser(500)
    assert (board.get_laser_value() or 0) > 0, "firmware did not report LASER3 on"

    board.set_laser(0)
    assert board.get_laser_value() == 0, "firmware did not report LASER3 off"


if __name__ == "__main__":
    uc2 = UC2Board()
    try:
        print("LASER3 ON  ->", uc2.set_laser(500), "| read back:", uc2.get_laser_value())
        time.sleep(3)
        print("LASER3 OFF ->", uc2.set_laser(0), "| read back:", uc2.get_laser_value())
    finally:
        uc2.close()
