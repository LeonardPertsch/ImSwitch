"""Read back the endstop digital inputs the setup declares. Read-only.

Nothing here moves a motor, homes an axis or switches a light on: the test only
asks the board what its limit switches currently read. Run it as a script
(``python test_motor_endstops.py``) for a live view while pressing the switches.
"""

import os
import time

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")

# Firmware convention: DIGITAL_IN_1/2/3 are the X/Y/Z limit switches. Both the
# homing state machine (HomeMotor::loop -> getDigitalVal(1/2/3) for X/Y/Z) and
# the runtime hard-limit check (FocusMotor::evaluateHardLimitForAxis(X, 1),
# (Y, 2), (Z, 3)) hard-code it, and pinConfig maps those onto TCA9535 pins
# 5/6/7 = X_LIMIT/Y_LIMIT/Z_LIMIT. No API exposes the mapping, so it is the one
# thing the test has to know.
ENDSTOP_ID = {"X": 1, "Y": 2, "Z": 3}

READ_TIMEOUT = float(os.environ.get("ENDSTOP_READ_TIMEOUT", "3"))

MONITOR_SECONDS = float(os.environ.get("ENDSTOP_MONITOR_SECONDS", "10"))


def api(controller, method, **params):
    """GET an ImSwitch API method, or None if this build does not have it."""
    response = requests.get(
        f"{BASE_URL}/api/{controller}/{method}",
        params=params,
        timeout=15,
    )

    if response.status_code == 404:
        return None

    assert response.status_code == 200, response.text

    return response.json()


def declared_axes():
    """Axes the setup configures homing for — those are the ones with endstops.

    Returns [] rather than raising, so collection still works with no rig.
    """
    try:
        axes = (api("UC2ConfigController", "getMotorSettings") or {}).get("axes")
    except Exception:
        return []

    return [
        axis
        for axis in ENDSTOP_ID
        if ((axes or {}).get(axis, {}).get("homing") or {}).get("enabled")
    ]


def endstop_value(payload, digitalinid):
    """Pull digitalinval for this input out of whatever getDigitalIn returned.

    UC2-REST answers with a list of serial frames when it could match the
    response to the request, and with a plain string when it timed out, so
    anything unexpected simply yields None.
    """
    for frame in payload if isinstance(payload, list) else [payload]:
        inner = frame.get("digitalin") if isinstance(frame, dict) else None

        if isinstance(inner, dict) and inner.get("digitalinid") == digitalinid:
            return inner.get("digitalinval")

    return None


def read_endstop(digitalinid):
    """Read one endstop. Returns (value, detail), value being 0, 1 or None.

    getDigitalInReliable triggers a fresh read and waits for the serial
    reader's digitalin callback, which also works on firmware that answers
    /digitalin_get without echoing the request's qid. getDigitalIn only reads
    back where that qid does come back, so it is the fallback, not the default.
    """
    reliable = api(
        "UC2ConfigController",
        "getDigitalInReliable",
        digitalinid=digitalinid,
        timeout=READ_TIMEOUT,
    )

    if isinstance(reliable, dict) and reliable.get("fresh"):
        return reliable.get("value"), reliable

    legacy = api(
        "UC2ConfigController",
        "getDigitalIn",
        digitalinid=digitalinid,
        timeout=READ_TIMEOUT,
    )

    return endstop_value(legacy, digitalinid), reliable if reliable is not None else legacy


@pytest.mark.hardware
@pytest.mark.parametrize("axis", declared_axes() or [None])
def test_endstop_is_readable(axis):
    if axis is None:
        pytest.skip("no homing axes declared — ImSwitch or setup unavailable")

    digitalinid = ENDSTOP_ID[axis]

    value, detail = read_endstop(digitalinid)

    print(f"\n{axis} endstop (digital in {digitalinid}) = {value}")

    assert value in (0, 1), (
        f"{axis} endstop (digital in {digitalinid}) did not read back: {detail!r}"
    )


if __name__ == "__main__":
    # Live view for wiring checks: press each switch and watch its value flip.
    deadline = time.time() + MONITOR_SECONDS

    while time.time() < deadline:
        print({
            axis: read_endstop(digitalinid)[0]
            for axis, digitalinid in ENDSTOP_ID.items()
        })

        time.sleep(0.5)
