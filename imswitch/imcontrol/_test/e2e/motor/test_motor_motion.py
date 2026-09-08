"""Check motor movement over HTTP."""

import os
import time

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")

STEP = 5
SETTLE = 0.5


def api(method, **params):
    response = requests.get(
        f"{BASE_URL}/api/PositionerController/{method}",
        params=params,
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return response.json()


def get_axes():
    positions = api("getPositionerPositions")

    return [
        (positioner, axis)
        for positioner, axes in positions.items()
        for axis in axes
    ]


@pytest.mark.hardware
@pytest.mark.parametrize(
    "positioner,axis",
    get_axes(),
)
def test_axis_moves_by_step(positioner, axis):
    before = api("getPositionerPositions")[positioner][axis]

    try:
        api(
            "movePositioner",
            positionerName=positioner,
            axis=axis,
            dist=STEP,
        )

        time.sleep(SETTLE)

        after = api("getPositionerPositions")[positioner][axis]

        assert after == pytest.approx(before + STEP, abs=0.1), (
            f"{positioner} {axis}: "
            f"{before} -> {after}, expected {before + STEP}"
        )

    finally:
        api(
            "movePositioner",
            positionerName=positioner,
            axis=axis,
            dist=-STEP,
        )