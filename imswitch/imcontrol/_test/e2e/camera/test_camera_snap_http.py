import os
import struct

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")
DETECTOR_NAME = os.environ.get("IMSWITCH_DETECTOR")

SENSOR_WIDTH = 4056
SENSOR_HEIGHT = 3040
RESIZE_FACTOR = 0.1

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#Read width and height from the PNG IHDR header.
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

#Request one camera frame from ImSwitch.
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
        timeout=30,
    )

    assert response.status_code == 200, response.text
    return response




#   Generic camera test.
#   Proves that ImSwitch can retrieve a PNG frame from the configured detector.
#   Makes no assumption about the specific camera model or resolution.
#
# API: GET /api/SettingsController/getDetectorNames        (via detector_name)
#      GET /api/RecordingController/snapNumpyToFastAPI     (via snap)
@pytest.mark.hardware
def test_camera_returns_image(detector_name):
    response = snap(detector_name)

    assert response.headers["Content-Type"].startswith("image/png")
    assert response.content.startswith(PNG_SIGNATURE)

    width, height = png_dimensions(response.content)

    assert width > 0
    assert height > 0
    assert len(response.content) > 100



#  Hardware-specific resolution test.
#  Checks that the returned frame matches an IMX477 sensor with
#  4056x3040 pixels, scaled by RESIZE_FACTOR.
#
# API: GET /api/SettingsController/getDetectorNames        (via detector_name)
#      GET /api/RecordingController/snapNumpyToFastAPI     (via snap)
@pytest.mark.hardware
def test_camera_has_expected_imx477_resolution(detector_name):
    response = snap(detector_name)

    assert response.headers["Content-Type"].startswith("image/png")

    width, height = png_dimensions(response.content)

    expected_width = int(SENSOR_WIDTH * RESIZE_FACTOR)
    expected_height = int(SENSOR_HEIGHT * RESIZE_FACTOR)

    assert (width, height) == (
        expected_width,
        expected_height,
    ), (
        f"unexpected camera resolution: got {width}x{height}, "
        f"expected {expected_width}x{expected_height}"
    )