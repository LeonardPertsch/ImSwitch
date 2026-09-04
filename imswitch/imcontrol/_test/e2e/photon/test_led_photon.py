
"""Check that every configured light source is visible to the camera."""

import io
import os
import time

import pytest
import requests
from PIL import Image, ImageChops, ImageStat


# Base URL of the ImSwitch HTTP API, as seen from wherever this test runs.
# The runners execute pytest inside the container, where ImSwitch is on its
# own port without the caddy prefix. From outside the Pi it is
# http://<pi>:8000/imswitch instead, so set IMSWITCH_URL when running locally.
BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")
DETECTOR = os.environ.get("IMSWITCH_DETECTOR")
LASER_VALUE = int(os.environ.get("UC2_LASER_VALUE", "1000"))
MIN_CHANGE = float(os.environ.get("PHOTON_MIN_DELTA", "1.5"))

SETTLE = 1.0
RESIZE = 0.25


# Call an ImSwitch API endpoint and return JSON.
#
# API: GET {BASE_URL}/api/{controller}/{method}
#      every call in this file except take_image goes through here
def api(controller, method, **params):
    response = requests.get(
        f"{BASE_URL}/api/{controller}/{method}",
        params=params,
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return response.json()


# Get all configured light sources.
#
# API: GET /api/LaserController/getLaserNames
def get_lights():
    try:
        return api("LaserController", "getLaserNames")
    except requests.RequestException:
        return []


LIGHTS = get_lights()


# Switch one light source on or off.
#
# API: GET /api/LaserController/setLaserValue   (LASER_VALUE when on, else 0)
#      GET /api/LaserController/setLaserActive
def set_light(name, on):
    api(
        "LaserController",
        "setLaserValue",
        laserName=name,
        value=LASER_VALUE if on else 0,
    )
    api(
        "LaserController",
        "setLaserActive",
        laserName=name,
        active=on,
    )
    time.sleep(SETTLE)


# Switch every configured light source off.
#
# API: GET /api/LaserController/setLaserValue   (via set_light, once per light)
#      GET /api/LaserController/setLaserActive
def all_lights_off():
    for name in LIGHTS:
        set_light(name, False)


# Take one frame and reject empty camera frames.
#
# API: GET /api/RecordingController/snapNumpyToFastAPI
#      params: detectorName, resizeFactor -> 200, image/png
#      called directly, not through api(), because the body is PNG and not JSON
def take_image(detector):
    response = requests.get(
        f"{BASE_URL}/api/RecordingController/snapNumpyToFastAPI",
        params={"detectorName": detector, "resizeFactor": RESIZE},
        timeout=30,
    )
    assert response.status_code == 200, response.text

    image = Image.open(io.BytesIO(response.content)).convert("L")
    assert image.getextrema()[1] > 0, (
        "camera returned an all-black frame; acquisition may not be running"
    )
    return image


# Start camera acquisition for the photon tests.
#
# API (setup):    GET /api/ViewController/setLiveViewActive   (active=True)
# API (teardown): GET /api/LaserController/setLaserValue      (via all_lights_off)
#                 GET /api/LaserController/setLaserActive
#                 GET /api/ViewController/setLiveViewActive   (active=False)
@pytest.fixture(scope="module", autouse=True)
def camera_acquisition():
    api("ViewController", "setLiveViewActive", active=True)
    time.sleep(1)

    yield

    all_lights_off()
    api("ViewController", "setLiveViewActive", active=False)


# Use the requested detector or the first configured one.
#
# API: GET /api/SettingsController/getDetectorNames
@pytest.fixture(scope="module")
def detector_name():
    detectors = api("SettingsController", "getDetectorNames")

    if not detectors:
        pytest.skip("no detector configured")
    if DETECTOR and DETECTOR not in detectors:
        pytest.skip(f"detector {DETECTOR!r} not found")

    return DETECTOR or detectors[0]


# Keep all light sources off before and after each test.
#
# API (setup and teardown): GET /api/LaserController/setLaserValue
#                           GET /api/LaserController/setLaserActive
#                           both via all_lights_off, once per light
@pytest.fixture
def light_source(request):
    all_lights_off()
    yield request.param
    all_lights_off()


# Check each configured light source as a separate pytest test.
#
# API: GET /api/RecordingController/snapNumpyToFastAPI  (dark frame, via take_image)
#      GET /api/LaserController/setLaserValue           (light on, via set_light)
#      GET /api/LaserController/setLaserActive
#      GET /api/RecordingController/snapNumpyToFastAPI  (bright frame, via take_image)
@pytest.mark.hardware
@pytest.mark.parametrize(
    "light_source",
    [pytest.param(name, id=name) for name in LIGHTS],
    indirect=True,
)
def test_light_source_is_visible_to_camera(light_source, detector_name):
    dark = take_image(detector_name)

    set_light(light_source, True)
    bright = take_image(detector_name)

    change = ImageStat.Stat(
        ImageChops.difference(dark, bright)
    ).mean[0]

    print(f"\n{light_source}: pixel_change={change:.2f}")

    assert change >= MIN_CHANGE, (
        f"{light_source}: image changed only by {change:.2f} "
        f"(need >= {MIN_CHANGE})"
    )
