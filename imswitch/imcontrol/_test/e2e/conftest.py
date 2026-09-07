"""Shared pytest behaviour for the e2e suite."""
import io
import os
import time

import pytest
import requests
from PIL import Image, ImageChops, ImageStat

BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")

# How long setDetectorExposureOnce stays in auto before its timer restores
# manual mode. The wait below has to outlast it, or the detector is still in
# 'once' while the first frame is taken.
AUTO_EXPOSURE_RESET_MS = int(
    os.environ.get("AUTO_EXPOSURE_RESET_MS", "1500")
)

PHOTON_MIN_DELTA = float(
    os.environ.get("PHOTON_MIN_DELTA", "1.5")
)

PHOTON_NOISE_FACTOR = float(
    os.environ.get("PHOTON_NOISE_FACTOR", "1.5")
)

PHOTON_NOISE_SAMPLES = int(
    os.environ.get("PHOTON_NOISE_SAMPLES", "6")
)

PHOTON_NOISE_DELAY = float(
    os.environ.get("PHOTON_NOISE_DELAY", "0.5")
)

PHOTON_RESIZE = 0.25

# Colour the progress percentage yellow once something has been skipped.
#
# TerminalReporter._determine_main_color turns the percentage yellow for
# "warnings", "xpassed" and unknown outcomes and red for "failed" and "error".
# "skipped" is not in that list, so an otherwise passing run shows a green
# percentage next to a yellow SKIPPED. These tests skip whenever the hardware
# they need is absent, which is the difference between "the rig is fine" and
# "the rig was never asked", so it is worth seeing at a glance.
#
# The override applies only while the percentage is being written. The closing
# summary line goes through the same method and keeps pytest's verdict: a run
# of passes and skips with nothing failed is a green pass, because a skipped
# test is one that was not needed rather than a problem. So the percentage
# flags the individual skip while the bottom line still reads as success.
#
# Within the percentage the colour is cumulative, the way pytest already treats
# red: from the first skip onwards it stays yellow, and a later failure wins
# because only green is upgraded.
#
# Hooked at session start rather than configure: the terminal reporter
# registers itself during configure, and a conftest's pytest_configure runs
# before that, where get_plugin("terminalreporter") answers None.
#
# These are private pytest attributes. Every access is guarded, so a rename in
# a future pytest falls back to stock colouring instead of breaking the run.
def pytest_sessionstart(session):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")

    if reporter is None or not hasattr(reporter, "_determine_main_color"):
        return

    # True only for the duration of a progress-percentage write.
    writing_progress = {"active": False}

    original_determine = reporter._determine_main_color

    def determine_main_color(unknown_type_seen):
        color = original_determine(unknown_type_seen)

        if writing_progress["active"] and color == "green":
            if "skipped" in reporter.stats:
                return "yellow"

        return color

    reporter._determine_main_color = determine_main_color

    # _get_main_color caches its answer in _main_color and only recomputes when
    # that is None, so the flag alone would be read from a value computed
    # outside it. Clearing the cache on the way in and out makes both the
    # percentage and the summary line compute their own colour.
    def wrap_progress_writer(original_writer):
        def write(*args, **kwargs):
            writing_progress["active"] = True
            reporter._main_color = None
            try:
                return original_writer(*args, **kwargs)
            finally:
                writing_progress["active"] = False
                reporter._main_color = None

        return write

    for name in (
        "_write_progress_information_filling_space",
        "_write_progress_information_if_past_edge",
    ):
        writer = getattr(reporter, name, None)

        if writer is not None:
            setattr(reporter, name, wrap_progress_writer(writer))


# Read the detector's current exposure in ms, or None if it cannot be read.
#
# API: GET /api/SettingsController/getCameraStatus
def _exposure_ms(detector):
    try:
        response = requests.get(
            f"{BASE_URL}/api/SettingsController/getCameraStatus",
            params={"detectorName": detector},
            timeout=30,
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    parameters = response.json().get("parameters") or {}
    return (parameters.get("exposure") or {}).get("value")

@pytest.fixture(scope="session")
def take_image():
    """Capture one greyscale camera frame."""

    def run(detector):
        response = requests.get(
            f"{BASE_URL}/api/RecordingController/snapNumpyToFastAPI",
            params={
                "detectorName": detector,
                "resizeFactor": PHOTON_RESIZE,
            },
            timeout=60,
        )

        assert response.status_code == 200, response.text

        return Image.open(
            io.BytesIO(response.content)
        ).convert("L")

    return run


@pytest.fixture(scope="session")
def image_difference():
    """Mean absolute pixel difference between two frames."""

    def run(first, second):
        return ImageStat.Stat(
            ImageChops.difference(first, second)
        ).mean[0]

    return run


@pytest.fixture
def measure_dark_baseline(take_image, image_difference):
    """Measure current camera noise and return a dark reference frame."""

    def run(detector):
        previous = take_image(detector)
        drifts = []

        for _ in range(PHOTON_NOISE_SAMPLES - 1):
            time.sleep(PHOTON_NOISE_DELAY)

            current = take_image(detector)

            drift = image_difference(previous, current)
            drifts.append(drift)

            previous = current

        noise_floor = max(drifts)

        required_change = max(
            PHOTON_MIN_DELTA,
            noise_floor * PHOTON_NOISE_FACTOR,
        )

        print(
            f"\n{detector} dark noise: "
            f"drifts={[round(value, 2) for value in drifts]}, "
            f"noise_floor={noise_floor:.2f}, "
            f"required_change={required_change:.2f}"
        )

        return previous, noise_floor, required_change

    return run

# One-shot auto exposure, run at most once per pytest session.
#
# Returns a callable rather than doing the work itself, because the right
# moment is decided by the caller: live view has to be streaming and the scene
# has to be in the state that should be exposed for. A session-scoped fixture
# is created before the module-scoped one that starts live view, so doing the
# work here directly would expose a camera that is not yet acquiring.
#
# Session scope is what makes "once" mean the right thing in both directions.
# run_all.sh puts laser and ledmatrix in a single session, so the pass happens
# one time for both; running either folder on its own is its own session and
# gets its own pass. Repeat calls return the first result unchanged.
#
# Not autouse: the camera tests check frame geometry and do not care about
# exposure, so only the photon tests ask for this.
#
# Note that the endpoint swallows its own errors and answers 200 either way, so
# the only evidence it did anything is the exposure value itself - which is why
# both the before and after values are returned.
#
# API: GET /api/SettingsController/setDetectorExposureOnce
#      GET /api/SettingsController/getCameraStatus  (before and after)
@pytest.fixture(scope="session")
def auto_exposure():
    state = {}

    def run(detector):
        if "result" in state:
            return state["result"]

        before = _exposure_ms(detector)

        try:
            response = requests.get(
                f"{BASE_URL}/api/SettingsController/setDetectorExposureOnce",
                params={
                    "detectorName": detector,
                    "resetDelayMs": AUTO_EXPOSURE_RESET_MS,
                },
                timeout=30,
            )
            assert response.status_code == 200, response.text

            # Outlast the timer that restores manual mode, plus a margin for
            # the exposure to actually be applied to the running stream.
            time.sleep(AUTO_EXPOSURE_RESET_MS / 1000 + 0.1)

        except requests.RequestException:
            pass

        after = _exposure_ms(detector)
        state["result"] = (before, after)

        print(
            f"\nauto exposure on {detector}: "
            f"{before} ms -> {after} ms (once per session)"
        )

        return state["result"]

    return run
