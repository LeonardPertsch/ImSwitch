"""Grab a frame off the real camera sensor over HTTP and check its dimensions.

The laser tests only read back an ImSwitch-internal attribute - getLaserActive
hands back self.enabled, set one line earlier. A PNG in sensor resolution has to
have come from the physical sensor, so this test reaches real hardware. It does
not look at the pixels though; for that see ../photon/.
"""
import os
import struct

import pytest
import requests

# Inside the imswitch container ImSwitch listens on :8001; from outside it is
# http://192.168.178.124:8000/imswitch, where caddy adds the /imswitch prefix.
BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")
DETECTOR_NAME = os.environ.get("IMSWITCH_DETECTOR")  # default: first one reported

# The sensor is an IMX477. resizeFactor scales that resolution exactly, so
# 0.1 has to come back as 405x304.
SENSOR_WIDTH = 4056
SENSOR_HEIGHT = 3040
RESIZE_FACTOR = 0.1

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# Read width and height out of a PNG's IHDR header, avoiding a Pillow dependency.
#
# The layout is fixed: 8 bytes of signature, a 4-byte chunk length, the 4 bytes
# "IHDR", then width and height as 4-byte big-endian integers at offset 16.
def png_dimensions(raw):
    assert raw[:8] == PNG_SIGNATURE, "response body is not a PNG"
    return struct.unpack(">II", raw[16:24])


# A snap returns a PNG sized resizeFactor x the full sensor resolution.
@pytest.mark.hardware
def test_snap_returns_png_at_sensor_resolution():
    try:
        detectors = requests.get(
            f"{BASE_URL}/api/SettingsController/getDetectorNames", timeout=5
        ).json()
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    if not detectors:
        pytest.skip("active setup has no detectors")
    if DETECTOR_NAME and DETECTOR_NAME not in detectors:
        pytest.skip(f"detector {DETECTOR_NAME!r} not in setup (have: {detectors})")

    response = requests.get(
        f"{BASE_URL}/api/RecordingController/snapNumpyToFastAPI",
        params={
            "detectorName": DETECTOR_NAME or detectors[0],
            "resizeFactor": RESIZE_FACTOR,
        },
        timeout=30,
    )

    assert response.status_code == 200, response.text
    assert response.headers["Content-Type"] == "image/png"
    assert png_dimensions(response.content) == (
        int(SENSOR_WIDTH * RESIZE_FACTOR),
        int(SENSOR_HEIGHT * RESIZE_FACTOR),
    )
