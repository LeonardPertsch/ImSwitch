import base64
import os
import struct
from collections import namedtuple

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")
DETECTOR_NAME = os.environ.get("IMSWITCH_DETECTOR")

RESIZE_FACTOR = 0.1

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOI = b"\xff\xd8"

# JPEG start-of-frame markers; each carries the image height and width.
JPEG_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}

# One captured frame. resize_factor is the scaling the endpoint applied, so the
# resolution test knows what size to expect.
Frame = namedtuple("Frame", "raw width height resize_factor")


def png_dimensions(raw):
    assert raw[:8] == PNG_SIGNATURE, "response body is not a PNG"
    return struct.unpack(">II", raw[16:24])


def jpeg_dimensions(raw):
    assert raw[:2] == JPEG_SOI, "image is not a JPEG"

    offset = 2

    while offset + 9 <= len(raw):
        marker = raw[offset + 1]
        length = struct.unpack(">H", raw[offset + 2:offset + 4])[0]

        if marker in JPEG_SOF_MARKERS:
            height, width = struct.unpack(">HH", raw[offset + 5:offset + 9])
            return width, height

        offset += 2 + length

    raise AssertionError("JPEG has no start-of-frame header")


def is_observation_camera(detector_name):
    return "observ" in detector_name.lower()


# ---------------------------------------------------------------------------
# Detector discovery
# ---------------------------------------------------------------------------

def get_detector_names():
    """
    Get all detectors from the currently active ImSwitch setup.

    If IMSWITCH_DETECTOR is set, only that detector is tested.

    API:
        GET /api/SettingsController/getDetectorNames
    """

    response = requests.get(
        f"{BASE_URL}/api/SettingsController/getDetectorNames",
        timeout=5,
    )
    response.raise_for_status()

    detectors = response.json()

    if not detectors:
        return []

    if DETECTOR_NAME:
        if DETECTOR_NAME not in detectors:
            return []
        return [DETECTOR_NAME]

    return detectors


def pytest_generate_tests(metafunc):
    """
    Generate one pytest test case per configured detector.

    Example:

        test_camera_returns_image[HikCam]
        test_camera_returns_image[RPiCam]
    """

    if "detector_name" not in metafunc.fixturenames:
        return

    try:
        detectors = get_detector_names()

    except requests.RequestException as exc:
        metafunc.parametrize(
            "detector_name",
            [
                pytest.param(
                    None,
                    marks=pytest.mark.skip(
                        reason=f"ImSwitch not reachable at {BASE_URL}: {exc}"
                    ),
                )
            ],
        )
        return

    if not detectors:
        reason = "active setup has no detectors"

        if DETECTOR_NAME:
            reason = (
                f"detector {DETECTOR_NAME!r} not found in active setup"
            )

        metafunc.parametrize(
            "detector_name",
            [
                pytest.param(
                    None,
                    marks=pytest.mark.skip(reason=reason),
                )
            ],
        )
        return

    metafunc.parametrize(
        "detector_name",
        detectors,
        ids=detectors,
    )


# ---------------------------------------------------------------------------
# Camera status
# ---------------------------------------------------------------------------

@pytest.fixture
def camera_status(detector_name):
    """
    Full status of the detector under test.

    Also prevents tests from passing against an ImSwitch mock camera.

    API:
        GET /api/SettingsController/getCameraStatus
    """

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

    if (
        status.get("isMock")
        or str(status.get("model", "")).lower() == "mock"
    ):
        pytest.skip(
            f"{detector_name}: ImSwitch served a mock camera "
            f"(model={status.get('model')!r}), no hardware attached"
        )

    return status


# ---------------------------------------------------------------------------
# Snap
# ---------------------------------------------------------------------------

def snap(detector_name):
    """
    Request one camera frame from ImSwitch.

    The observation camera goes through snap_observation(); every other
    detector through RecordingController.

    API:
        GET /api/RecordingController/snapNumpyToFastAPI
    """

    if is_observation_camera(detector_name):
        return snap_observation()

    response = requests.get(
        f"{BASE_URL}/api/RecordingController/snapNumpyToFastAPI",
        params={
            "detectorName": detector_name,
            "resizeFactor": RESIZE_FACTOR,
        },
        timeout=10,
    )

    assert response.status_code == 200, response.text
    assert response.headers["Content-Type"].startswith("image/png")

    width, height = png_dimensions(response.content)

    return Frame(response.content, width, height, RESIZE_FACTOR)


def snap_observation():
    """
    Request one frame from the observation camera.

    snapNumpyToFastAPI only collects detectors with forAcquisition=true. The
    observation camera has it false, so that endpoint answers 500 (KeyError)
    for it. snapOverviewImage reads the camera's latest frame directly and
    returns it as a full-resolution JPEG, without touching the setup file.

    It picks the camera itself (experiment.overviewCameraName, else the
    detector named ObservationCamera). camera_name only names the folder the
    snapshot PNG is saved in on the Pi, kept separate from real overview
    registrations.

    API:
        POST /api/ExperimentController/snapOverviewImage
    """

    response = requests.post(
        f"{BASE_URL}/api/ExperimentController/snapOverviewImage",
        params={"slot_id": "1", "camera_name": "e2e_camera_test"},
        timeout=10,
    )

    assert response.status_code == 200, response.text

    body = response.json()

    assert body.get("imageMimeType") == "image/jpeg", body.get("imageMimeType")

    raw = base64.b64decode(body["imageBase64"])
    width, height = jpeg_dimensions(raw)

    return Frame(raw, width, height, 1.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.hardware
@pytest.mark.usefixtures("camera_status")
def test_camera_returns_image(detector_name):
    """
    Every real configured detector must return a valid image frame.
    """

    frame = snap(detector_name)

    assert frame.width > 0
    assert frame.height > 0
    assert len(frame.raw) > 100


@pytest.mark.hardware
def test_camera_matches_sensor_resolution(detector_name, camera_status):
    """
    Compare the returned image size against the resolution reported
    by the individual detector.
    """

    full_width = (
        camera_status.get("currentWidth")
        or camera_status.get("sensorWidth")
    )

    full_height = (
        camera_status.get("currentHeight")
        or camera_status.get("sensorHeight")
    )

    if not full_width or not full_height:
        pytest.skip(
            f"{detector_name}: getCameraStatus reports no usable frame size "
            f"({full_width}x{full_height})"
        )

    frame = snap(detector_name)

    expected_width = int(full_width * frame.resize_factor)
    expected_height = int(full_height * frame.resize_factor)

    assert (frame.width, frame.height) == (
        expected_width,
        expected_height,
    ), (
        f"{detector_name}: unexpected camera resolution: "
        f"got {frame.width}x{frame.height}, expected "
        f"{expected_width}x{expected_height} "
        f"({full_width}x{full_height} at "
        f"resizeFactor {frame.resize_factor})"
    )
