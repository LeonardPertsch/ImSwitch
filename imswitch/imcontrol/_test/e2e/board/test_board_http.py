"""Check communication with the UC2 board."""

import os

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")

@pytest.mark.hardware
def test_uc2_board_connected():
    response = requests.get(
        f"{BASE_URL}/api/UC2ConfigController/is_connected",
        timeout=5,
    )

    assert response.status_code == 200, response.text

    assert response.json() is True, (
        "UC2/ESP32 board is not reported as connected"
    )


@pytest.mark.hardware
def test_uc2_board_responds():
    response = requests.get(
        f"{BASE_URL}/api/UC2ConfigController/getFirmwareInfo",
        timeout=5,
    )

    

    if response.status_code == 404:
        (pytest.skip("getFirmwareInfo endpoint not available in this ImSwitch version"))
    result = response.json()

    assert response.status_code == 200, response.text
    assert result, "UC2 board returned no firmware information"

