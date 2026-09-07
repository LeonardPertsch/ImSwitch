"""Check that the LED matrix physically lights up the camera."""

import os
import time

import pytest
import requests
from PIL import ImageStat


# Base URL of the ImSwitch HTTP API, as seen from wherever this test runs.
# The runners execute pytest inside the container, where ImSwitch is on its
# own port without the caddy prefix. From outside the Pi it is
# http://<pi>:8000/imswitch instead, so set IMSWITCH_URL when running locally.
BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")
DETECTOR = os.environ.get("IMSWITCH_DETECTOR")

INTENSITY = int(os.environ.get("LEDMATRIX_INTENSITY", "500"))

SETTLE = 0.5



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



#helpermethod for getting the available controllers from the ImSwitch API
#because there is no way to know if the LEDMatrixController is available otherwise
def get_available_controllers():
    response = requests.get(
        f"{BASE_URL}/api/getAvailableControllers",
        timeout=30,
    )

    assert response.status_code == 200, (
        f"getAvailableControllers -> "
        f"{response.status_code}: {response.text}"
    )

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


@pytest.fixture(scope="module")
def camera_status(detector_name):
    status = api(
        "SettingsController",
        "getCameraStatus",
        detectorName=detector_name,
    )

    if "error" in status:
        pytest.skip(
            f"{detector_name}: getCameraStatus failed: {status['error']}"
        )

    if status.get("isMock") or str(status.get("model", "")).lower() == "mock":
        pytest.skip(
            f"{detector_name}: ImSwitch served a mock camera "
            f"(model={status.get('model')!r}), no hardware attached"
        )

    return status

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
    """Skip the module unless this setup actually has an LED matrix."""

    try:
        controllers = get_available_controllers()
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    # Support either a plain list or a dictionary response.
    if isinstance(controllers, dict):
        controllers = controllers.get(
            "controllers",
            controllers.get("availableControllers", []),
        )

    if "LEDMatrixController" not in controllers:
        pytest.skip("LEDMatrixController not available in this setup")

        

@pytest.fixture(scope="module", autouse=True)
def camera_acquisition(led_matrix_available, detector_name, camera_status):
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
def test_led_matrix_is_visible_to_camera(
    dark_rig,
    detector_name,
    auto_exposure,
    measure_dark_baseline,
    take_image,
    image_difference,
):
    auto_exposure(detector_name)

    # Measure the current camera noise while everything is dark.
    dark, noise_floor, required_change = measure_dark_baseline(
        detector_name
    )

    # Now switch on only the LED matrix.
    matrix_on()

    bright = take_image(detector_name)

    change = image_difference(dark, bright)

    print(
        f"\nLED matrix @ intensity {INTENSITY}: "
        f"dark_mean={ImageStat.Stat(dark).mean[0]:.2f} "
        f"bright_mean={ImageStat.Stat(bright).mean[0]:.2f} "
        f"noise_floor={noise_floor:.2f} "
        f"pixel_change={change:.2f} "
        f"required={required_change:.2f}"
    )

    assert bright.getextrema()[1] > 0, (
        "the matrix is on but every pixel is still 0; "
        "no light reached the sensor"
    )

    assert change >= required_change, (
        f"LED matrix: image changed only by {change:.2f}; "
        f"camera noise floor is {noise_floor:.2f}, "
        f"need >= {required_change:.2f}"
    )