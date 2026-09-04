"""Check that the LED matrix physically lights up the camera."""

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

# Measured on this rig: intensity 20 lifts the frame mean from 0.00 to roughly
# 3.6, comfortably clear of MIN_CHANGE. Raise it if the matrix sits further
# from the sensor or behind more optics.
INTENSITY = int(os.environ.get("LEDMATRIX_INTENSITY", "20"))
MIN_CHANGE = float(os.environ.get("PHOTON_MIN_DELTA", "1.5"))

# The baseline counts as quiet when two consecutive dark frames differ by less
# than this. A separate knob from MIN_CHANGE because --measure sets MIN_CHANGE
# to 0, and a shared constant would then make the settle check unsatisfiable.
SETTLE_TOLERANCE = float(os.environ.get("PHOTON_SETTLE_TOLERANCE", "1"))

SETTLE = 0.5
RESIZE = 0.25

# How many times to re-read the baseline while waiting for the camera to go
# quiet before giving up on it.
SETTLE_ATTEMPTS = 5


# Call an ImSwitch API endpoint and return JSON.
#
# Most endpoints are GET; LiveViewController/startLiveView is the one POST, so
# the verb is a parameter rather than a second copy of this function.
#
# API: GET|POST {BASE_URL}/api/{controller}/{method}
def api(controller, method, http="GET", **params):
    send = requests.post if http == "POST" else requests.get

    response = send(
        f"{BASE_URL}/api/{controller}/{method}",
        params=params,
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return response.json()


# Call one LEDMatrixController endpoint and hand back the raw response.
#
# The controller exposes setters only, with no way to ask whether the matrix is
# lit, so every caller writes rather than reads.
#
# This never skips. matrix_off runs from fixture teardown as well, and a
# pytest.skip raised during teardown reports the test a second time. Skipping
# on a missing controller happens once, in led_matrix_available.
#
# API: GET /api/LEDMatrixController/{method}
def matrix(method, **params):
    return requests.get(
        f"{BASE_URL}/api/LEDMatrixController/{method}",
        params=params,
        timeout=30,
    )


# Switch the matrix on at INTENSITY, white.
#
# API: GET /api/LEDMatrixController/setAllLED
def matrix_on():
    response = matrix(
        "setAllLED",
        intensity_r=INTENSITY,
        intensity_g=INTENSITY,
        intensity_b=INTENSITY,
    )

    assert response.status_code == 200, response.text
    time.sleep(SETTLE)


# Switch the matrix off. Safe to call from teardown: it never skips, and a
# setup without the controller (404) is not an error here.
#
# API: GET /api/LEDMatrixController/setAllLEDOff
def matrix_off():
    try:
        response = matrix("setAllLEDOff")
    except requests.RequestException:
        return

    if response.status_code == 404:
        return

    assert response.status_code == 200, response.text
    time.sleep(SETTLE)


# Switch off every laser/LED the setup reports, so that the matrix is the only
# thing that can change the image.
#
# API: GET /api/LaserController/getLaserNames
#      GET /api/LaserController/setLaserValue
#      GET /api/LaserController/setLaserActive
def lasers_off():
    try:
        names = api("LaserController", "getLaserNames")
    except requests.RequestException:
        return

    for name in names:
        api("LaserController", "setLaserValue", laserName=name, value=0)
        api("LaserController", "setLaserActive", laserName=name, active=False)


# Take one frame as greyscale.
#
# An all-black frame is valid input. With the matrix off in a closed enclosure
# every pixel is 0, which is the baseline the test needs rather than a fault.
# The brightness check sits on the bright frame instead.
#
# API: GET /api/RecordingController/snapNumpyToFastAPI
#      params: detectorName, resizeFactor -> 200, image/png
def take_image(detector):
    response = requests.get(
        f"{BASE_URL}/api/RecordingController/snapNumpyToFastAPI",
        params={"detectorName": detector, "resizeFactor": RESIZE},
        timeout=60,
    )
    assert response.status_code == 200, response.text

    return Image.open(io.BytesIO(response.content)).convert("L")


# Mean absolute difference between two frames.
#
# API: none, pure image arithmetic
def difference(first, second):
    return ImageStat.Stat(ImageChops.difference(first, second)).mean[0]


# Return a baseline frame only once two consecutive dark frames agree.
#
# A stream that has just started still drifts: two frames taken right after
# startLiveView differ by about 1.66 with no light at all. Measuring against
# such a baseline would credit that drift to the matrix.
#
# Frame-to-frame noise never falls below about 0.52 on this rig, and waiting
# does not lower it - it is sensor read noise, not settling. SETTLE_TOLERANCE
# has to sit between the two numbers; below 0.52 nothing ever satisfies it and
# the module skips every run.
#
# API: GET /api/RecordingController/snapNumpyToFastAPI  (via take_image)
def settled_dark_frame(detector):
    previous = take_image(detector)

    for _ in range(SETTLE_ATTEMPTS):
        time.sleep(SETTLE)
        current = take_image(detector)

        drift = difference(previous, current)
        if drift < SETTLE_TOLERANCE:
            return current

        previous = current

    pytest.skip(
        f"{detector}: camera never settled, two dark frames still differ by "
        f"{drift:.2f} (need < {SETTLE_TOLERANCE})"
    )


# Use the requested detector or the first configured one.
#
# API: GET /api/SettingsController/getDetectorNames
@pytest.fixture(scope="module")
def detector_name():
    try:
        detectors = api("SettingsController", "getDetectorNames")
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    if not detectors:
        pytest.skip("active setup has no detectors")
    if DETECTOR and DETECTOR not in detectors:
        pytest.skip(f"detector {DETECTOR!r} not found (available: {detectors})")

    return DETECTOR or detectors[0]


# Keep the camera streaming for the duration of this module.
#
# LiveViewController owns the stream and reports real state. The older
# ViewController/setLiveViewActive cannot be used on these rigs: switching on
# returns 200 without starting anything and switching off answers 500.
#
# API (setup):    POST /api/LiveViewController/startLiveView
#                 GET  /api/LiveViewController/getLiveViewActive
# API (teardown): GET  /api/LEDMatrixController/setAllLEDOff
#                 GET  /api/LiveViewController/stopLiveView
@pytest.fixture(scope="module")
def led_matrix_available():
    """Skip the module unless this setup actually has an LED matrix.

    The single place allowed to skip on a missing controller. Probing once here
    keeps pytest.skip out of matrix_off, which also runs from teardown, where
    skipping would report the test a second time. It runs before live view is
    started so a rig without a matrix does not get a stream it will not use.
    """
    try:
        response = matrix("setAllLEDOff")
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    if response.status_code == 404:
        pytest.skip("no LEDMatrixController in this setup (setAllLEDOff -> 404)")

    assert response.status_code == 200, response.text


@pytest.fixture(scope="module", autouse=True)
def camera_acquisition(led_matrix_available, detector_name):
    started = api(
        "LiveViewController",
        "startLiveView",
        http="POST",
        detectorName=detector_name,
    )
    status = started.get("status")

    # startLiveView answers 200 even when it declines, so the body decides.
    if status == "long_exposure":
        pytest.skip(f"{detector_name}: exposure too long for live view: {started}")

    # "already_running" is fine - an active stream is all this module needs.
    assert status in ("success", "already_running"), (
        f"{detector_name}: startLiveView did not start a stream: {started}"
    )

    assert api("LiveViewController", "getLiveViewActive") is True, (
        f"{detector_name}: getLiveViewActive is False right after "
        f"startLiveView returned {status!r}"
    )

    time.sleep(0.4)

    yield

    # The matrix must go dark even if stopping the stream fails.
    try:
        matrix_off()
    finally:
        # Only hand back what was taken; a stream that was already running
        # belongs to whoever started it.
        if status == "success":
            api(
                "LiveViewController",
                "stopLiveView",
                detectorName=detector_name,
            )


# Leave the rig dark before and after the test, matrix included.
#
# API: GET /api/LaserController/*                    (via lasers_off)
#      GET /api/LEDMatrixController/setAllLEDOff     (via matrix_off)
@pytest.fixture
def dark_rig():
    lasers_off()
    matrix_off()

    yield

    lasers_off()
    matrix_off()


# The one test that proves light from the matrix physically reached the sensor.
#
# Everything else in e2e/ reads back state that software set a call earlier.
# This compares two actual frames, so it cannot pass without photons.
#
# API: GET /api/SettingsController/setDetectorExposureOnce  (via auto_exposure)
#      GET /api/RecordingController/snapNumpyToFastAPI  (dark, via settled_dark_frame)
#      GET /api/LEDMatrixController/setAllLED           (matrix on, via matrix_on)
#      GET /api/RecordingController/snapNumpyToFastAPI  (bright, via take_image)
@pytest.mark.hardware
def test_led_matrix_is_visible_to_camera(dark_rig, detector_name, auto_exposure):
    # Before the baseline, never between the two frames: dark and bright have
    # to be taken at the same exposure, otherwise the measured change is partly
    # the exposure changing rather than light arriving. The session fixture
    # makes this a no-op if another photon module already ran it.
    auto_exposure(detector_name)

    dark = settled_dark_frame(detector_name)

    matrix_on()
    bright = take_image(detector_name)

    change = difference(dark, bright)

    print(
        f"\nLED matrix @ intensity {INTENSITY}: "
        f"dark_mean={ImageStat.Stat(dark).mean[0]:.2f} "
        f"bright_mean={ImageStat.Stat(bright).mean[0]:.2f} "
        f"pixel_change={change:.2f}"
    )

    assert bright.getextrema()[1] > 0, (
        "the matrix is on but every pixel is still 0; no light reached the sensor"
    )

    assert change >= MIN_CHANGE, (
        f"LED matrix: image changed only by {change:.2f} "
        f"(need >= {MIN_CHANGE}) at intensity {INTENSITY}"
    )
