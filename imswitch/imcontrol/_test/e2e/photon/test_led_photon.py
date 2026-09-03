"""The one test that cannot pass without real photons.

Every other test here reads back state that software set: the laser test gets an
ImSwitch attribute assigned a moment earlier, and the camera test proves a PNG
arrived but says nothing about what is in it. This one switches the LED on,
photographs the result, and asserts the sensor actually got brighter.

    LED off -> snap -> mean brightness   (dark)
    LED on  -> snap -> mean brightness   (bright)
    assert bright / dark >= PHOTON_MIN_RATIO

Everything goes through the ImSwitch HTTP API, so the LED must be mapped in the
active setup and the camera must be able to see it. Measured on this rig:
dark 0.02, bright 115.47 - a ratio of roughly 7500.
"""
import io
import os
import time

import pytest
import requests

try:
    from PIL import Image, ImageStat
except ImportError:
    Image = None

# Inside the imswitch container ImSwitch listens on :8001; from outside it is
# http://192.168.178.124:8000/imswitch, where caddy adds the /imswitch prefix.
BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8000/imswitch")

LASER_NAME = os.environ.get("IMSWITCH_LASER")        # default: first one reported
DETECTOR_NAME = os.environ.get("IMSWITCH_DETECTOR")  # default: first one reported
LASER_VALUE = int(os.environ.get("UC2_LASER_VALUE", "1000"))
MIN_RATIO = float(os.environ.get("PHOTON_MIN_RATIO", "5.0"))

# How long to let the LED settle before photographing it.
SETTLE_SECONDS = 1.0

# A quarter of the sensor resolution is plenty for a brightness average.
RESIZE_FACTOR = 0.25


# GET an ImSwitch API endpoint, fail on anything but 200, return the parsed JSON.
def get_json(controller, method, **params):
    response = requests.get(
        f"{BASE_URL}/api/{controller}/{method}", params=params, timeout=10
    )
    assert response.status_code == 200, f"{method} -> {response.status_code}: {response.text}"
    return response.json()


# Switch the LED on at LASER_VALUE, or off, then wait for it to settle.
def set_led(laser_name, on):
    get_json("LaserController", "setLaserValue",
             laserName=laser_name, value=LASER_VALUE if on else 0)
    get_json("LaserController", "setLaserActive", laserName=laser_name, active=on)
    time.sleep(SETTLE_SECONDS)


# Photograph the current scene and return its mean brightness (0-255).
def mean_brightness(detector_name):
    response = requests.get(
        f"{BASE_URL}/api/RecordingController/snapNumpyToFastAPI",
        params={"detectorName": detector_name, "resizeFactor": RESIZE_FACTOR},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    greyscale = Image.open(io.BytesIO(response.content)).convert("L")
    return ImageStat.Stat(greyscale).mean[0]


# Look up the LED and the camera, skipping the test if either is missing.
@pytest.fixture
def led_and_camera():
    if Image is None:
        pytest.skip("Pillow not installed")
    try:
        lasers = get_json("LaserController", "getLaserNames")
        detectors = get_json("SettingsController", "getDetectorNames")
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    if not lasers:
        pytest.skip("active setup has no lasers/LEDs - has ImSwitch been restarted?")
    if not detectors:
        pytest.skip("active setup has no detectors")

    laser_name = LASER_NAME or lasers[0]
    yield laser_name, DETECTOR_NAME or detectors[0]
    set_led(laser_name, on=False)


# The camera measures more light with the LED on than with it off.
@pytest.mark.hardware
def test_led_is_visible_to_the_camera(led_and_camera):
    laser_name, detector_name = led_and_camera

    set_led(laser_name, on=False)
    dark = mean_brightness(detector_name)

    set_led(laser_name, on=True)
    bright = mean_brightness(detector_name)

    ratio = bright / dark if dark else float("inf")
    print(f"\ndark={dark:.2f}  bright={bright:.2f}  ratio={ratio:.1f}  (need >= {MIN_RATIO})")

    assert ratio >= MIN_RATIO, (
        f"the sensor saw no extra light when {laser_name!r} was switched on "
        f"(dark={dark:.2f}, bright={bright:.2f}, ratio={ratio:.1f}). "
        f"Is the LED in the camera's field of view, and is auto-exposure "
        f"compensating for it?"
    )
