import os

import pytest
import requests


BASE_URL = os.environ.get(
    "IMSWITCH_URL",
    "http://localhost:8001",
)


# GET an endpoint, skipping the test when ImSwitch cannot be reached at all.
#
# A refused connection says nothing about the LED matrix, so it has to skip
# rather than fail — the suite is meant to be runnable without a rig.
def get(path):
    try:
        return requests.get(f"{BASE_URL}/api/{path}", timeout=5)
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")


def get_available_controllers():
    response = get("getAvailableControllers")

    assert response.status_code == 200, (
        f"getAvailableControllers -> "
        f"{response.status_code}: {response.text}"
    )

    return response.json()


@pytest.mark.hardware
def test_all_leds_off_is_accepted():
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

    response = get("LEDMatrixController/setAllLEDOff")

    assert response.status_code == 200, (
        f"LEDMatrixController/setAllLEDOff -> "
        f"{response.status_code}: {response.text}"
    )