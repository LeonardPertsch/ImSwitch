
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

SETTLE = 0.1
RESIZE = 0.25


# Call an ImSwitch API endpoint and return JSON.
#
# Most endpoints are GET; LiveViewController/startLiveView is the one POST, so
# the verb is a parameter rather than a second copy of this function.
#
# A 200 does not always mean the call did what was asked - LiveViewController
# reports refusals in the body with status 200 - so callers that care have to
# check the returned status field as well.
#
# API: GET|POST {BASE_URL}/api/{controller}/{method}
#      every call in this file except take_image goes through here
def api(controller, method, http="GET", **params):
    send = requests.post if http == "POST" else requests.get

    response = send(
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


# Switch off the LED matrix, which LIGHTS does not cover.
#
# The matrix has its own controller and getLaserNames does not report it, so
# without this it stays lit through the whole module and the "dark" frame is
# not dark. It also exposes setters only - there is no way to ask whether it is
# on - so this fires unconditionally rather than checking first.
#
# A setup without the matrix answers 404, which is not a failure here; anything
# else is, because a matrix that refuses to switch off invalidates the
# measurement rather than merely being absent.
#
# API: GET /api/LEDMatrixController/setAllLEDOff
def led_matrix_off():
    try:
        response = requests.get(
            f"{BASE_URL}/api/LEDMatrixController/setAllLEDOff",
            timeout=30,
        )
    except requests.RequestException:
        return

    if response.status_code == 404:
        return

    assert response.status_code == 200, response.text


# Switch every configured light source off.
#
# API: GET /api/LaserController/setLaserValue   (via set_light, once per light)
#      GET /api/LaserController/setLaserActive
#      GET /api/LEDMatrixController/setAllLEDOff  (via led_matrix_off)
def all_lights_off():
    for name in LIGHTS:
        set_light(name, False)

    led_matrix_off()


# Take one frame as greyscale.
#
# An all-black frame is deliberately not rejected here. Since all_lights_off
# also switches off the LED matrix, the dark frame on a rig in a closed
# enclosure really is every pixel 0, and treating that as a fault would fail
# the test on exactly the baseline it needs. The brightness check belongs on
# the bright frame instead, where it is done.
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

    return Image.open(io.BytesIO(response.content)).convert("L")


# Start camera acquisition for the photon tests.
#
# This uses LiveViewController, not ViewController/setLiveViewActive. The
# latter is the older path and is broken on these rigs: its _acqHandle is
# already set at boot while LiveViewController owns the actual stream, so
# setLiveViewActive(True) returns 200 without starting anything and
# setLiveViewActive(False) always answers 500 "Invalid or already used
# handle". LiveViewController reports real state and tolerates a double stop.
#
# API (setup):    POST /api/LiveViewController/startLiveView
#                 GET  /api/LiveViewController/getLiveViewActive
# API (teardown): GET  /api/LaserController/setLaserValue      (via all_lights_off)
#                 GET  /api/LaserController/setLaserActive
#                 GET  /api/LiveViewController/stopLiveView
@pytest.fixture(scope="module", autouse=True)
def camera_acquisition(detector_name):
    started = api(
        "LiveViewController",
        "startLiveView",
        http="POST",
        detectorName=detector_name,
    )
    status = started.get("status")

    # startLiveView answers 200 even when it declines, so the body decides.
    # A long exposure is a refusal rather than a failure: passing force=True
    # would start the stream anyway, but frames would then be slower than the
    # settle time this test assumes, so measuring light would be unreliable.
    if status == "long_exposure":
        pytest.skip(
            f"{detector_name}: exposure too long for live view: {started}"
        )

    # "already_running" is fine - the frontend or an earlier run may hold the
    # stream, and an active stream is all this module needs.
    assert status in ("success", "already_running"), (
        f"{detector_name}: startLiveView did not start a stream: {started}"
    )

    assert api("LiveViewController", "getLiveViewActive") is True, (
        f"{detector_name}: getLiveViewActive is False right after "
        f"startLiveView returned {status!r}"
    )

    time.sleep(0.4)

    yield

    all_lights_off()

    # Only hand back what was taken. A stream that was already running before
    # this module belongs to whoever started it, so leave it alone.
    if status == "success":
        api("LiveViewController", "stopLiveView", detectorName=detector_name)


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
# API: GET /api/SettingsController/setDetectorExposureOnce  (via auto_exposure)
#      GET /api/RecordingController/snapNumpyToFastAPI  (dark frame, via take_image)
#      GET /api/LaserController/setLaserValue           (light on, via set_light)
#      GET /api/LaserController/setLaserActive
#      GET /api/RecordingController/snapNumpyToFastAPI  (bright frame, via take_image)
@pytest.mark.hardware
@pytest.mark.parametrize(
    "light_source",
    [pytest.param(name, id=name) for name in LIGHTS],
    indirect=True,
)
def test_light_source_is_visible_to_camera(light_source, detector_name, auto_exposure):
    # Before the baseline, never between the two frames: dark and bright have
    # to be taken at the same exposure, otherwise the measured change is partly
    # the exposure changing rather than light arriving. The session fixture
    # runs the pass once, so with several lights only the first test pays for
    # it and every light is then measured at the same exposure.
    auto_exposure(detector_name)

    dark = take_image(detector_name)

    set_light(light_source, True)
    bright = take_image(detector_name)

    change = ImageStat.Stat(
        ImageChops.difference(dark, bright)
    ).mean[0]

    print(
        f"\n{light_source}: "
        f"dark_mean={ImageStat.Stat(dark).mean[0]:.2f} "
        f"bright_mean={ImageStat.Stat(bright).mean[0]:.2f} "
        f"pixel_change={change:.2f}"
    )

    assert bright.getextrema()[1] > 0, (
        f"{light_source} is on but every pixel is still 0; "
        f"no light reached the sensor"
    )

    assert change >= MIN_CHANGE, (
        f"{light_source}: image changed only by {change:.2f} "
        f"(need >= {MIN_CHANGE})"
    )
