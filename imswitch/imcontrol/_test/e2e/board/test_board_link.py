"""Check communication with the UC2 board."""

import os

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")


# GET an endpoint, skipping the test when ImSwitch cannot be reached at all.
#
# A refused connection says nothing about the board, so it has to skip rather
# than fail — the suite is meant to be runnable without a rig.
def get(path):
    try:
        return requests.get(f"{BASE_URL}/api/{path}", timeout=5)
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")


@pytest.mark.hardware
def test_uc2_board_connected():
    response = get("UC2ConfigController/is_connected")

    assert response.status_code == 200, response.text

    assert response.json() is True, (
        "UC2/ESP32 board is not reported as connected"
    )


@pytest.mark.hardware
def test_uc2_board_responds():
    response = get("UC2ConfigController/getFirmwareInfo")

    if response.status_code == 404:
        pytest.skip(
            "getFirmwareInfo endpoint not available in this ImSwitch version"
        )

    # Status before body: a non-JSON error page would otherwise raise a decode
    # error instead of the assertion that says what actually went wrong.
    assert response.status_code == 200, response.text

    assert response.json(), "UC2 board returned no firmware information"

