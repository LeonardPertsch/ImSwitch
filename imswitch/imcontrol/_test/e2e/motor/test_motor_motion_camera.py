"""Verify physical stage motion with the observation camera.

Flow:
1. Check observation camera.
2. Move to transport position once.
3. For every axis:
   - measure camera noise
   - take image before movement
   - move forward
   - take image after movement
   - detect image change
   - for X/Y: detect rough translation and direction using phase correlation
   - move back

Tests:
- test_axis_motion_is_visible (A, X, Y, Z): grey value change above noise
- test_axis_motion_direction (X, Y): phase correlation shows right / down

Every axis is moved only once; both tests share that measurement.

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

DISTANCE_UM = int(
    os.environ.get(
        "MOTION_CAMERA_DISTANCE_UM",
        "3000",
    )
)

# Direction of the first move for Z: +1 or -1.
#
# Z moves negative first and then back, so it does not run towards
# the Z endstop (homeDirectionZ is +1) right after the transport
# move switched the Z hard limits off.
Z_DIRECTION = int(
    os.environ.get(
        "MOTION_CAMERA_Z_DIRECTION",
        "-1",
    )
)

# Motor speed passed to movePositioner.
#
# Without an explicit speed, PositionerController uses 5000.
# 10000 is twice as fast.
SPEED = int(
    os.environ.get(
        "MOTION_CAMERA_SPEED",
        "15000",
    )
)

SETTLE_MS = int(
    os.environ.get(
        "MOTION_CAMERA_SETTLE_MS",
        "500",
    )
)

MIN_RATIO = float(
    os.environ.get(
        "MOTION_CAMERA_MIN_RATIO",
        "1.8",
    )
)


# Region of interest used for X/Y motion detection.
#
# Default values are chosen for the current 640 x 360 observation image.
#
# NumPy indexing:
#
#     frame[y1:y2, x1:x2]
#
# ROI:
#
#     x = 150 .. 340
#     y = 150 .. 355
#
ROI_X1 = int(
    os.environ.get(
        "MOTION_CAMERA_ROI_X1",
        "150",
    )
)

ROI_X2 = int(
    os.environ.get(
        "MOTION_CAMERA_ROI_X2",
        "340",
    )
)

ROI_Y1 = int(
    os.environ.get(
        "MOTION_CAMERA_ROI_Y1",
        "150",
    )
)

ROI_Y2 = int(
    os.environ.get(
        "MOTION_CAMERA_ROI_Y2",
        "355",
    )
)

# Z/A analysis: remove this percentage from the top of the image,
# only the lower part is used for the grey value difference.
TOP_CROP_PERCENT = int(
    os.environ.get(
        "MOTION_CAMERA_TOP_CROP_PERCENT",
        "25",
    )
)


def api(controller, method, **params):
    response = requests.get(
        f"{BASE_URL}/api/{controller}/{method}",
        params=params,
        timeout=60,
    )

    assert response.status_code == 200, response.text

    return response.json()


def grab():
    response = requests.post(
        f"{BASE_URL}/api/ExperimentController/snapOverviewImage",
        params={
            "slot_id": "1",
            "camera_name": "e2e_motion_test",
        },
        timeout=60,
    )

    assert response.status_code == 200, response.text

    image_bytes = base64.b64decode(
        response.json()["imageBase64"]
    )

    frame = np.asarray(
        Image.open(
            io.BytesIO(image_bytes)
        ).convert("L"),
        dtype=np.float32,
    )

    print(
        f"SNAPSHOT shape={frame.shape}",
        flush=True,
    )

    return frame


def image_difference(before, after):
    """Return the mean absolute pixel difference."""

    assert before.shape == after.shape

    return float(
        np.mean(
            np.abs(after - before)
        )
    )


def crop_motion_roi(frame):
    """Return the ROI containing the moving stage/object."""

    height, width = frame.shape

    assert 0 <= ROI_X1 < ROI_X2 <= width, (
        f"invalid ROI x-range "
        f"{ROI_X1}:{ROI_X2} "
        f"for image width {width}"
    )

    assert 0 <= ROI_Y1 < ROI_Y2 <= height, (
        f"invalid ROI y-range "
        f"{ROI_Y1}:{ROI_Y2} "
        f"for image height {height}"
    )

    return frame[
        ROI_Y1:ROI_Y2,
        ROI_X1:ROI_X2,
    ]


def crop_top(frame):
    """Remove the top TOP_CROP_PERCENT of the image."""

    height = frame.shape[0]

    top = (
        height
        * TOP_CROP_PERCENT
        // 100
    )

    assert 0 <= top < height, (
        f"invalid top crop "
        f"{TOP_CROP_PERCENT}% "
        f"for image height {height}"
    )

    return frame[top:, :]


def phase_shift(before, after):
    """Estimate translation of AFTER relative to BEFORE as (dy, dx).

    Positive dx means the image moved right.
    Negative dx means left.

    Positive dy means down.
    Negative dy means up.
    """

    assert before.shape == after.shape

    before = before.astype(
        np.float64,
        copy=True,
    )

    after = after.astype(
        np.float64,
        copy=True,
    )

    # Remove the DC component.
    #
    # We care about structures and their displacement,
    # not the average brightness of the image.
    before -= before.mean()
    after -= after.mean()

    # Apply a Hann window.
    #
    # FFT assumes that the image repeats periodically.
    # Without a window, the transition from one image edge
    # to the opposite edge can create strong artificial
    # frequencies.
    window = (
        np.hanning(before.shape[0])[:, None]
        * np.hanning(before.shape[1])[None, :]
    )

    before *= window
    after *= window

    # Fourier transforms.
    f_before = np.fft.fft2(before)
    f_after = np.fft.fft2(after)

    # Cross Power Spectrum.
    #
    # conj(F_before) * F_after gives the displacement
    # of AFTER relative to BEFORE with the sign convention
    # documented above.
    cross_power = (
        np.conj(f_before)
        * f_after
    )

    magnitude = np.abs(
        cross_power
    )

    # Remove amplitude information.
    #
    # Only phase information remains.
    cross_power /= np.maximum(
        magnitude,
        1e-12,
    )

    # Transform the phase correlation back into image space.
    correlation = np.fft.ifft2(
        cross_power
    )

    correlation = np.abs(
        correlation
    )

    # Move zero displacement to the centre.
    correlation = np.fft.fftshift(
        correlation
    )

    # Locate the strongest correlation peak.
    peak_y, peak_x = np.unravel_index(
        np.argmax(correlation),
        correlation.shape,
    )

    center_y = (
        correlation.shape[0] // 2
    )

    center_x = (
        correlation.shape[1] // 2
    )

    dy = peak_y - center_y
    dx = peak_x - center_x

    peak = float(
        correlation[
            peak_y,
            peak_x,
        ]
    )

    return (
        float(dy),
        float(dx),
        peak,
    )


def describe_direction(
    dy,
    dx,
    min_shift=2,
):
    """Convert the measured translation into a rough direction."""

    distance = float(
        np.hypot(
            dx,
            dy,
        )
    )

    if distance < min_shift:
        return "no clear translation"

    if abs(dx) > abs(dy):
        return (
            "right"
            if dx > 0
            else "left"
        )

    return (
        "down"
        if dy > 0
        else "up"
    )


def axes():
    try:
        positions = api(
            "PositionerController",
            "getPositionerPositions",
        )

    except Exception:
        return []

    return [
        (positioner, axis)
        for positioner, axis_map
        in (positions or {}).items()
        for axis in axis_map
    ]


@pytest.fixture(scope="module")
def observation_camera():
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

    # The fixture is module scoped: a skip here skips every test
    # in this file, before any hardware is moved.
    if camera is None:
        pytest.skip(
            f"no observation camera found; "
            f"detectors: {names}"
        )

    status = api(
        "SettingsController",
        "getCameraStatus",
        detectorName=camera,
    )

    if status.get("status") == "error":
        pytest.skip(
            f"{camera} status unavailable: "
            f"{status.get('error')}"
        )

    # A camera that fails to open usually falls back to a mock camera.
    if (
        status.get("isMock")
        or str(
            status.get(
                "model",
                "",
            )
        ).lower() == "mock"
    ):
        pytest.skip(
            f"{camera} is a mock camera "
            f"(not connected or turned off)"
        )

    if status.get("isConnected") is False:
        pytest.skip(
            f"{camera} is not connected"
        )

    # Verify that the camera works before moving hardware.
    try:
        frame = grab()

    except Exception as exc:
        pytest.skip(
            f"{camera} cannot deliver "
            f"an image: {exc}"
        )

    # Validate the ROI before moving hardware.
    try:
        roi = crop_motion_roi(frame)

    except AssertionError as exc:
        pytest.fail(
            f"invalid motion ROI: {exc}"
        )

    print(
        f"\nObservation camera: {camera}"
    )

    print(
        f"Image shape: {frame.shape}"
    )

    print(
        "Motion ROI: "
        f"x={ROI_X1}:{ROI_X2}, "
        f"y={ROI_Y1}:{ROI_Y2}, "
        f"shape={roi.shape}"
    )

    print(
        "Moving stage to transport position..."
    )

    position = move_to_transport()

    print(
        f"Transport position reached: "
        f"{position}"
    )

    time.sleep(
        SETTLE_MS / 1000
    )

    return camera


# Expected image direction when an axis moves by +DISTANCE_UM.
EXPECTED_DIRECTION = {
    "X": "right",
    "Y": "down",
}


def translation_axes():
    """Return only the axes checked with phase correlation."""

    return [
        (positioner, axis)
        for positioner, axis in axes()
        if axis.upper() in EXPECTED_DIRECTION
    ]


def measure_axis_motion(
    positioner,
    axis,
):
    """Move one axis forward and back and analyse the camera images.

    Returns a dict with the image difference results and, for X/Y,
    the phase correlation results.
    """

    settle = (
        SETTLE_MS / 1000
    )

    use_translation_roi = (
        axis.upper() in EXPECTED_DIRECTION
    )

    # First move is positive for every axis except Z.
    distance = (
        DISTANCE_UM * Z_DIRECTION
        if axis.upper() == "Z"
        else DISTANCE_UM
    )

    print(
        f"\nMeasuring {positioner} {axis}"
    )

    if use_translation_roi:
        print(
            f"{axis}: using motion ROI "
            f"x={ROI_X1}:{ROI_X2}, "
            f"y={ROI_Y1}:{ROI_Y2}"
        )

    else:
        print(
            f"{axis}: using lower "
            f"{100 - TOP_CROP_PERCENT}% of the image "
            f"for image difference"
        )

    # For X/Y, analyse exactly the ROI.
    #
    # For Z/A, use the image without its top TOP_CROP_PERCENT,
    # because those axes do not necessarily produce a clean
    # translation.
    def analysis_region(frame):
        if use_translation_roi:
            return crop_motion_roi(frame)

        return crop_top(frame)

    # ---------------------------------------------------------
    # Camera noise baseline
    # ---------------------------------------------------------

    baseline_1 = analysis_region(
        grab()
    )

    time.sleep(
        settle
    )

    before = analysis_region(
        grab()
    )

    baseline = image_difference(
        baseline_1,
        before,
    )

    print(
        f"{axis}: baseline difference = "
        f"{baseline:.3f}"
    )

    moved_forward = False

    try:
        # -----------------------------------------------------
        # Move stage
        # -----------------------------------------------------

        print(
            f"{axis}: moving "
            f"{distance:+d} um"
        )

        api(
            "PositionerController",
            "movePositioner",
            positionerName=positioner,
            axis=axis,
            dist=distance,
            isBlocking=True,
            speed=SPEED,
        )

        moved_forward = True

        time.sleep(
            settle
        )

        after = analysis_region(
            grab()
        )

    finally:
        # -----------------------------------------------------
        # Always try to return to the starting position
        # -----------------------------------------------------

        if moved_forward:
            print(
                f"{axis}: moving "
                f"{-distance:+d} um"
            )

            api(
                "PositionerController",
                "movePositioner",
                positionerName=positioner,
                axis=axis,
                dist=-distance,
                isBlocking=True,
                speed=SPEED,
            )

            time.sleep(
                settle
            )

    # ---------------------------------------------------------
    # Grey value difference
    # ---------------------------------------------------------

    movement_difference = image_difference(
        before,
        after,
    )

    ratio = (
        movement_difference
        / max(
            baseline,
            1e-6,
        )
    )

    print(
        f"{axis}: movement difference = "
        f"{movement_difference:.3f}, "
        f"ratio={ratio:.2f}x"
    )

    result = {
        "baseline": baseline,
        "movement_difference": movement_difference,
        "ratio": ratio,
    }

    # ---------------------------------------------------------
    # X/Y phase correlation
    # ---------------------------------------------------------

    if use_translation_roi:
        dy, dx, peak = phase_shift(
            before,
            after,
        )

        magnitude = float(
            np.hypot(
                dx,
                dy,
            )
        )

        direction = describe_direction(
            dy,
            dx,
        )

        print(
            f"{axis}: image shift = "
            f"dx={dx:+.1f}px, "
            f"dy={dy:+.1f}px, "
            f"magnitude={magnitude:.1f}px, "
            f"direction={direction}, "
            f"peak={peak:.4f}"
        )

        result.update(
            dx=dx,
            dy=dy,
            peak=peak,
            direction=direction,
        )

    return result


@pytest.fixture(scope="module")
def axis_motion(observation_camera):
    """Measure every axis only once per test run.

    Both the grey value test and the direction test read from
    the same measurement, so the hardware is not moved twice.
    """

    cache = {}

    def get(positioner, axis):
        key = (positioner, axis)

        if key not in cache:
            cache[key] = measure_axis_motion(
                positioner,
                axis,
            )

        return cache[key]

    return get


@pytest.mark.hardware
@pytest.mark.parametrize(
    "positioner,axis",
    axes() or [(None, None)],
)
def test_axis_motion_is_visible(
    positioner,
    axis,
    axis_motion,
):
    """A, X, Y, Z: grey value change must be above camera noise."""

    if positioner is None:
        pytest.skip(
            "no positioners reported"
        )

    result = axis_motion(
        positioner,
        axis,
    )

    assert result["ratio"] >= MIN_RATIO, (
        f"{positioner} {axis}: "
        f"physical movement was not clearly visible. "
        f"movement={result['movement_difference']:.3f}, "
        f"baseline={result['baseline']:.3f}, "
        f"ratio={result['ratio']:.2f}x, "
        f"required={MIN_RATIO:.2f}x"
    )


@pytest.mark.hardware
@pytest.mark.parametrize(
    "positioner,axis",
    translation_axes() or [(None, None)],
)
def test_axis_motion_direction(
    positioner,
    axis,
    axis_motion,
):
    """X, Y: phase correlation must show the expected direction."""

    if positioner is None:
        pytest.skip(
            "no X/Y positioners reported"
        )

    result = axis_motion(
        positioner,
        axis,
    )

    expected_direction = EXPECTED_DIRECTION[
        axis.upper()
    ]

    assert result["direction"] == expected_direction, (
        f"{positioner} {axis}: "
        f"image moved in the wrong direction. "
        f"expected={expected_direction}, "
        f"measured={result['direction']}, "
        f"dx={result['dx']:+.1f}px, "
        f"dy={result['dy']:+.1f}px, "
        f"peak={result['peak']:.4f}"
    )
