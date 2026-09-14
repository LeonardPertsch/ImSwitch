"""Verify physical stage motion using the observation camera.

The test does not trust the motor position reported by the firmware.
Instead, it checks whether the observation camera sees a real physical change.

Flow:

    1. Verify that the observation camera exists.
    2. Move the stage to the transport position once.
    3. For every reported axis:
        a. Take two images without moving to measure normal camera noise.
        b. Move the axis forward.
        c. Wait for everything to settle.
        d. Take another image.
        e. Move the axis back.
        f. Compare the image change caused by the move with the normal noise.

THIS TEST MOVES REAL HARDWARE.
"""

import base64
import io
import os
import time

import numpy as np
import pytest
import requests
from PIL import Image

from move_to_transport import move_to_transport


BASE_URL = os.environ.get(
    "IMSWITCH_URL",
    "http://localhost:8001",
)

# Distance moved for every axis.
DISTANCE_UM = int(
    os.environ.get(
        "MOTION_CAMERA_DISTANCE_UM",
        "3000",
    )
)

# Wait after movement or between baseline images.
SETTLE_MS = int(
    os.environ.get(
        "MOTION_CAMERA_SETTLE_MS",
        "500",
    )
)

# The image change caused by the motor movement must be this many times larger
# than the normal camera-to-camera variation.
MIN_RATIO = float(
    os.environ.get(
        "MOTION_CAMERA_MIN_RATIO",
        "1.8",
    )
)


def api(controller, method, **params):
    """Call an ImSwitch GET endpoint and return its JSON response."""

    response = requests.get(
        f"{BASE_URL}/api/{controller}/{method}",
        params=params,
        timeout=60,
    )

    assert response.status_code == 200, (
        f"{controller}/{method}: "
        f"HTTP {response.status_code}: {response.text}"
    )

    return response.json()


def grab():
    """Take one frame from the observation camera.

    The observation camera is not an acquisition camera, therefore this uses
    ExperimentController/snapOverviewImage instead of
    RecordingController/snapNumpyToFastAPI.
    """

    response = requests.post(
        f"{BASE_URL}/api/ExperimentController/snapOverviewImage",
        params={
            "slot_id": "1",
            "camera_name": "e2e_motion_test",
        },
        timeout=60,
    )

    assert response.status_code == 200, (
        f"snapOverviewImage: "
        f"HTTP {response.status_code}: {response.text}"
    )

    image_bytes = base64.b64decode(
        response.json()["imageBase64"]
    )

    frame = np.asarray(
        Image.open(
            io.BytesIO(image_bytes)
        ).convert("L"),
        dtype=np.float32,
    )

    print("SNAPSHOT", flush=True)

    return frame


def image_difference(before, after):
    """Return mean absolute greyscale difference between two frames."""

    assert before.shape == after.shape, (
        f"camera image shape changed: "
        f"{before.shape} -> {after.shape}"
    )

    return float(
        np.mean(
            np.abs(after - before)
        )
    )


def axes():
    """Return every positioner/axis pair reported by ImSwitch."""

    try:
        positions = api(
            "PositionerController",
            "getPositionerPositions",
        )
    except Exception:
        return []

    return [
        (positioner, axis)
        for positioner, axis_map in (positions or {}).items()
        for axis in axis_map
    ]


@pytest.fixture(scope="module")
def observation_camera():
    """Verify the camera and move to transport once before all tests."""

    names = api(
        "SettingsController",
        "getDetectorNames",
    )

    camera = next(
        (
            name
            for name in names
            if "observ" in name.lower()
        ),
        None,
    )

    if camera is None:
        pytest.skip(
            f"no observation camera found; "
            f"available detectors: {names}"
        )

    status = api(
        "SettingsController",
        "getCameraStatus",
        detectorName=camera,
    )

    if (
        status.get("isMock")
        or str(
            status.get("model", "")
        ).lower() == "mock"
    ):
        pytest.fail(
            f"{camera} is a mock camera"
        )

    print(
        f"\nObservation camera: {camera}"
    )

    # Make sure the camera works before moving any hardware.
    try:
        grab()
    except Exception as exc:
        pytest.fail(
            f"{camera} cannot deliver an image: {exc}"
        )

    print(
        "\nMoving stage to transport position..."
    )

    position = move_to_transport()

    print(
        f"Transport position reached: {position}"
    )

    time.sleep(
        SETTLE_MS / 1000
    )

    return camera


@pytest.mark.hardware
@pytest.mark.parametrize(
    "positioner,axis",
    axes() or [(None, None)],
)
def test_axis_motion_is_visible(
    positioner,
    axis,
    observation_camera,
):
    """Move one axis and verify that the camera sees more than normal noise."""

    if positioner is None:
        pytest.skip(
            "no positioners reported"
        )

    settle = SETTLE_MS / 1000

    print(
        f"\nTesting {positioner} {axis}"
    )

    # ------------------------------------------------------------
    # 1. Measure normal camera variation WITHOUT moving anything.
    # ------------------------------------------------------------

    baseline_1 = grab()

    time.sleep(settle)

    baseline_2 = grab()

    baseline = image_difference(
        baseline_1,
        baseline_2,
    )

    print(
        f"{axis}: baseline difference = "
        f"{baseline:.3f}"
    )

    moved_forward = False

    try:
        # --------------------------------------------------------
        # 2. Move the axis forward.
        # --------------------------------------------------------

        print(
            f"{axis}: moving +{DISTANCE_UM} um"
        )

        api(
            "PositionerController",
            "movePositioner",
            positionerName=positioner,
            axis=axis,
            dist=DISTANCE_UM,
            isBlocking=True,
        )

        moved_forward = True

        time.sleep(settle)

        # --------------------------------------------------------
        # 3. Take image AFTER physical movement.
        # --------------------------------------------------------

        moved_image = grab()

        # --------------------------------------------------------
        # 4. Compare with the image directly before movement.
        # --------------------------------------------------------

        movement_difference = image_difference(
            baseline_2,
            moved_image,
        )

        ratio = (
            movement_difference
            / max(baseline, 1e-6)
        )

        print(
            f"{axis}: movement difference = "
            f"{movement_difference:.3f}"
        )

        print(
            f"{axis}: baseline difference = "
            f"{baseline:.3f}"
        )

        print(
            f"{axis}: movement / baseline = "
            f"{ratio:.2f}x"
        )

    finally:
        # --------------------------------------------------------
        # 5. Always return the axis to its original position.
        # --------------------------------------------------------

        if moved_forward:
            print(
                f"{axis}: moving -{DISTANCE_UM} um"
            )

            api(
                "PositionerController",
                "movePositioner",
                positionerName=positioner,
                axis=axis,
                dist=-DISTANCE_UM,
                isBlocking=True,
            )

            time.sleep(settle)

    # ------------------------------------------------------------
    # 6. Decide whether real motion was visible.
    # ------------------------------------------------------------

    assert ratio >= MIN_RATIO, (
        f"{positioner} {axis}: physical movement was not clearly visible. "
        f"Movement difference={movement_difference:.3f}, "
        f"baseline={baseline:.3f}, "
        f"ratio={ratio:.2f}x, "
        f"required={MIN_RATIO:.2f}x"
    )