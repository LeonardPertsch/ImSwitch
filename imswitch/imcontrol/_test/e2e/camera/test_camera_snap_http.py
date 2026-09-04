import os
import struct

import pytest
import requests


# Base URL of the ImSwitch HTTP API, as seen from wherever this test runs.
# The runners execute pytest inside the container, where ImSwitch is on its
# own port without the caddy prefix. From outside the Pi it is
# http://<pi>:8000/imswitch instead, so set IMSWITCH_URL when running locally.
BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")
DETECTOR_NAME = os.environ.get("IMSWITCH_DETECTOR")

RESIZE_FACTOR = 0.1

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Read width and height from the PNG IHDR header.
#
# API: none, pure byte parsing on a response body
def png_dimensions(raw):
    assert raw[:8] == PNG_SIGNATURE, "response body is not a PNG"
    return struct.unpack(">II", raw[16:24])

#Return a detector from the currently active ImSwitch setup.
#
# API: GET /api/SettingsController/getDetectorNames
@pytest.fixture
def detector_name():
    try:
        response = requests.get(
            f"{BASE_URL}/api/SettingsController/getDetectorNames",
            timeout=5,
        )
        response.raise_for_status()
        detectors = response.json()

    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    if not detectors:
        pytest.skip("active setup has no detectors")

    if DETECTOR_NAME and DETECTOR_NAME not in detectors:
        pytest.skip(
            f"detector {DETECTOR_NAME!r} not in setup "
            f"(available: {detectors})"
        )

    return DETECTOR_NAME or detectors[0]


# Full status of the detector under test, and the gate that keeps the rest of
# this file off a camera that is not physically there.
#
# A detector name does not prove hardware: when the real driver fails to start,
# HikCamManager catches that and substitutes MockCameraTIS, which serves a black
# frame with "The camera is not connected" drawn on it. That frame is a valid
# PNG, so every format check below would happily pass on it.
#
# Three signals are checked, because no single one covers every manager:
#
#   isMock        set by HikCamManager, OpenCV, Tucsen and ToupCam from the
#                 camera's model string
#   isConnected   whether the manager still holds a live driver handle
#   model         "mock" is what MockCameraTIS reports; kept as its own check
#                 so this also works for managers that report neither flag
#
# isConnected is only trusted when the manager actually reports it. GXPIPY and
# PiCam build it from their own driver attributes, and a manager that omits it
# would otherwise look permanently disconnected and skip every run.
#
# API: GET /api/SettingsController/getCameraStatus
@pytest.fixture
def camera_status(detector_name):
    response = requests.get(
        f"{BASE_URL}/api/SettingsController/getCameraStatus",
        params={"detectorName": detector_name},
        timeout=10,
    )

    assert response.status_code == 200, response.text
    status = response.json()

    if "error" in status:
        pytest.skip(
            f"{detector_name}: getCameraStatus failed: {status['error']}"
        )

    if status.get("isMock") or str(status.get("model", "")).lower() == "mock":
        pytest.skip(
            f"{detector_name}: ImSwitch served a mock camera "
            f"(model={status.get('model')!r}), no hardware attached"
        )

    # isConnected is deliberately not used as a skip condition. On this rig it
    # is False even while the camera is delivering frames, because HikCamManager
    # probes an attribute CameraHIK does not have. Skipping on it would disable
    # the camera tests on working hardware.

    return status


# Request one camera frame from ImSwitch.
#
# API: GET /api/RecordingController/snapNumpyToFastAPI
#      params: detectorName, resizeFactor -> 200, image/png
def snap(detector_name):
    response = requests.get(
        f"{BASE_URL}/api/RecordingController/snapNumpyToFastAPI",
        params={
            "detectorName": detector_name,
            "resizeFactor": RESIZE_FACTOR,
        },
        timeout=10,
    )

    assert response.status_code == 200, response.text
    return response




#   Generic camera test.
#   Proves that ImSwitch can retrieve a PNG frame from the configured detector.
#   Makes no assumption about the specific camera model or resolution.
#
# API: GET /api/SettingsController/getDetectorNames        (via detector_name)
#      GET /api/SettingsController/getCameraStatus         (via camera_status)
#      GET /api/RecordingController/snapNumpyToFastAPI     (via snap)
@pytest.mark.hardware
@pytest.mark.usefixtures("camera_status")
def test_camera_returns_image(detector_name):
    response = snap(detector_name)

    assert response.headers["Content-Type"].startswith("image/png")
    assert response.content.startswith(PNG_SIGNATURE)

    width, height = png_dimensions(response.content)

    assert width > 0
    assert height > 0
    assert len(response.content) > 100



#  Resolution test against whatever sensor is actually attached.
#
#  The expected size comes from getCameraStatus rather than from a constant, so
#  this follows the rig instead of assuming one specific camera. currentWidth
#  and currentHeight are preferred over sensorWidth/sensorHeight because they
#  already account for a ROI or binning; the sensor values are the fallback.
#
#  Note that the setup file is not the right source here. Its
#  managerProperties.hikcam.image_width/image_height describe what ImSwitch
#  asks the driver for, not what the driver delivers - on this rig it declares
#  1000x1000 while the camera returns 3072x2048.
#
# API: GET /api/SettingsController/getDetectorNames        (via detector_name)
#      GET /api/SettingsController/getCameraStatus         (via camera_status)
#      GET /api/RecordingController/snapNumpyToFastAPI     (via snap)
@pytest.mark.hardware
def test_camera_matches_sensor_resolution(detector_name, camera_status):
    full_width = camera_status.get("currentWidth") or camera_status.get("sensorWidth")
    full_height = camera_status.get("currentHeight") or camera_status.get("sensorHeight")

    if not full_width or not full_height:
        pytest.skip(
            f"{detector_name}: getCameraStatus reports no usable frame size "
            f"({full_width}x{full_height})"
        )

    response = snap(detector_name)

    assert response.headers["Content-Type"].startswith("image/png")

    width, height = png_dimensions(response.content)

    expected_width = int(full_width * RESIZE_FACTOR)
    expected_height = int(full_height * RESIZE_FACTOR)

    assert (width, height) == (
        expected_width,
        expected_height,
    ), (
        f"unexpected camera resolution: got {width}x{height}, expected "
        f"{expected_width}x{expected_height} "
        f"({full_width}x{full_height} at resizeFactor {RESIZE_FACTOR})"
    )