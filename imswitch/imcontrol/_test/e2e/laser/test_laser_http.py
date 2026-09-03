import os

import pytest
import requests


# Base URL of the ImSwitch HTTP API.
# IMSWITCH_URL can override the default when the test runs in another environment.
BASE_URL = os.environ.get(
    "IMSWITCH_URL",
    "http://localhost:8000/imswitch",
)

# Base endpoint for all LaserController API calls.
LASER_API = f"{BASE_URL}/api/LaserController"


# Call one LaserController endpoint.
# The request must return HTTP 200; otherwise the current test fails immediately.
# The JSON response from ImSwitch is returned to the caller.
def call(method, **params):
    response = requests.get(
        f"{LASER_API}/{method}",
        params=params,
        timeout=5,
    )

    assert response.status_code == 200, (
        f"{method} -> {response.status_code}: {response.text}"
    )

    return response.json()


# Read all lasers/LEDs from the currently active ImSwitch setup.
# This happens when pytest collects the tests so that pytest can create one
# separate test case for every reported laser.
#
# The setup is read by ImSwitch at startup. If the setup file was changed,
# ImSwitch must be restarted before the new laser list appears here.
def get_laser_params():
    try:
        response = requests.get(
            f"{LASER_API}/getLaserNames",
            timeout=5,
        )
        response.raise_for_status()
        lasers = response.json()

    except requests.RequestException as exc:
        return [
            pytest.param(
                None,
                marks=pytest.mark.skip(
                    reason=f"ImSwitch not reachable at {BASE_URL}: {exc}"
                ),
                id="ImSwitch-unreachable",
            )
        ]

    if not lasers:
        return [
            pytest.param(
                None,
                marks=pytest.mark.skip(
                    reason="active setup has no lasers/LEDs"
                ),
                id="no-lasers",
            )
        ]

    # Give every laser its own pytest parameter and use the laser name as the
    # test ID so that it is directly visible in the terminal output.
    return [
        pytest.param(
            laser_name,
            id=str(laser_name),
        )
        for laser_name in lasers
    ]


# Provide one laser to one test invocation.
# After that individual test finishes, the same laser is forced to value 0
# and disabled so that a failed test cannot intentionally leave it active.
@pytest.fixture
def safe_laser(request):
    laser_name = request.param

    yield laser_name

    if laser_name is not None:
        try:
            call(
                "setLaserValue",
                laserName=laser_name,
                value=0,
            )
        finally:
            call(
                "setLaserActive",
                laserName=laser_name,
                active=False,
            )


# Test every laser/LED reported by the active ImSwitch setup as a separate
# pytest test case.
#
# For each laser:
# 1. Set a positive laser value.
# 2. Enable the laser through the HTTP API.
# 3. Verify that ImSwitch reports it as active.
# 4. Verify that ImSwitch reports a positive value.
# 5. Disable the laser.
# 6. Verify that ImSwitch reports it as inactive.
#
# This proves that the requests pass through the ImSwitch API without an error
# and that ImSwitch updates its internal state.
#
# It does not prove that physical light was emitted because getLaserActive and
# getLaserValue read back ImSwitch-side state rather than an independent optical
# measurement.
@pytest.mark.hardware
@pytest.mark.parametrize(
    "safe_laser",
    get_laser_params(),
    indirect=True,
)
def test_laser_on_off_over_http(safe_laser):
    laser_name = safe_laser

    # Set a positive value for this laser before enabling it.
    call(
        "setLaserValue",
        laserName=laser_name,
        value=500,
    )

    # Enable this laser through the ImSwitch HTTP API.
    call(
        "setLaserActive",
        laserName=laser_name,
        active=True,
    )

    # Verify that ImSwitch reports this laser as active.
    assert call(
        "getLaserActive",
        laserName=laser_name,
    ) is True, f"{laser_name}: did not become active"

    # Verify that ImSwitch reports a positive value for this laser.
    assert (
        call(
            "getLaserValue",
            laserName=laser_name,
        ) or 0
    ) > 0, f"{laser_name}: laser value is not positive"

    # Disable this laser again before completing its individual test.
    call(
        "setLaserActive",
        laserName=laser_name,
        active=False,
    )

    # Verify that ImSwitch now reports this laser as inactive.
    assert call(
        "getLaserActive",
        laserName=laser_name,
    ) is False, f"{laser_name}: did not become inactive"

    # Reset the value to zero after the functional checks have passed.
    call(
        "setLaserValue",
        laserName=laser_name,
        value=0,
    )