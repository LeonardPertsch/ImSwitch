"""Switch the LED on and off through the ImSwitch HTTP API.

The chain the request travels:

    GET /api/LaserController/setLaserActive?laserName=LED&active=true
      -> LaserController.toggleLaser
      -> ESP32LEDLaserManager.setEnabled       (channel_index == LASERid)
      -> uc2rest -> /dev/ttyUSB0
      -> ESP32   {"task":"/laser_act","LASERid":3,...}

Needs a setup where a laser is mapped onto the ESP32, and an ImSwitch restart
after changing it - the setup file is only read at startup. See README.md.

Note what this does and does not prove: getLaserActive returns self.enabled,
which setLaserActive assigned a moment earlier, so a pass means the request got
through ImSwitch without error. It does not mean any light was emitted. That is
what ../photon/ is for.
"""
import os

import pytest
import requests

# Inside the imswitch container ImSwitch listens on :8001; from outside it is
# http://192.168.178.124:8000/imswitch, where caddy adds the /imswitch prefix.
BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8000/imswitch")
LASER_NAME = os.environ.get("IMSWITCH_LASER")  # default: first one reported

# APIExport methods without an explicit request type are registered as GET
# under /api/<Controller>/<method>, which is why the setters are GETs too.
LASER_API = f"{BASE_URL}/api/LaserController"


# Call a LaserController endpoint, fail on anything but 200, return the JSON.
def call(method, **params):
    response = requests.get(f"{LASER_API}/{method}", params=params, timeout=5)
    assert response.status_code == 200, f"{method} -> {response.status_code}: {response.text}"
    return response.json()


# Hand out a laser name from the active setup, switching it off again afterwards.
@pytest.fixture
def laser_name():
    try:
        lasers = requests.get(f"{LASER_API}/getLaserNames", timeout=5).json()
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    if not lasers:
        pytest.skip("active setup has no lasers/LEDs - has ImSwitch been restarted?")
    if LASER_NAME and LASER_NAME not in lasers:
        pytest.skip(f"laser {LASER_NAME!r} not in setup (have: {lasers})")

    name = LASER_NAME or lasers[0]
    yield name
    call("setLaserValue", laserName=name, value=0)
    call("setLaserActive", laserName=name, active=False)


# ImSwitch reports the laser active after enabling it, inactive after disabling.
@pytest.mark.hardware
def test_laser_on_off_over_http(laser_name):
    call("setLaserValue", laserName=laser_name, value=500)
    call("setLaserActive", laserName=laser_name, active=True)
    assert call("getLaserActive", laserName=laser_name) is True
    assert (call("getLaserValue", laserName=laser_name) or 0) > 0

    call("setLaserActive", laserName=laser_name, active=False)
    assert call("getLaserActive", laserName=laser_name) is False
