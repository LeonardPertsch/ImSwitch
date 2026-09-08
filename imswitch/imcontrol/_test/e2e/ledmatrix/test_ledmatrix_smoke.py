import os

import pytest
import requests


BASE_URL = os.environ.get(
    "IMSWITCH_URL",
    "http://localhost:8001",
)


def get_available_controllers():
    response = requests.get(
        f"{BASE_URL}/api/getAvailableControllers",
        timeout=5,
    )

    assert response.status_code == 200, (
        f"getAvailableControllers -> "
        f"{response.status_code}: {response.text}"
    )

    return response.json()


@pytest.mark.hardware
def test_led_matrix_http():
    controllers = get_available_controllers()

    # Depending on the ImSwitch response format, controllers may be returned
    # directly as a list or inside a dictionary.
    if isinstance(controllers, dict):
        controllers = controllers.get(
            "controllers",
            controllers.get("availableControllers", []),
        )

    if "LEDMatrixController" not in controllers:
        pytest.skip("LEDMatrixController not available in this setup")

    response = requests.get(
        f"{BASE_URL}/api/LEDMatrixController/setAllLEDOff",
        timeout=5,
    )

    assert response.status_code == 200, (
        f"LEDMatrixController/setAllLEDOff -> "
        f"{response.status_code}: {response.text}"
    )