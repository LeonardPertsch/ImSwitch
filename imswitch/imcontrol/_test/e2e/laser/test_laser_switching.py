"""Check that every configured laser/LED switches through the ImSwitch API."""

import os

import pytest
import requests


# ImSwitch runs on :8001 without the caddy prefix inside the container; from
# outside the Pi it is http://<pi>:8000/imswitch, so set IMSWITCH_URL then.
BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")

LASER_API = f"{BASE_URL}/api/LaserController"


def call(method, **params):
    """Call one LaserController endpoint and return its JSON."""
    response = requests.get(
        f"{LASER_API}/{method}",
        params=params,
        timeout=5,
    )

    assert response.status_code == 200, (
        f"{method} -> {response.status_code}: {response.text}"
    )

    return response.json()


def get_laser_params():
    """One pytest parameter per laser/LED of the active setup.

    Read at collection time. ImSwitch reads the setup at startup, so it must be
    restarted before a changed laser list appears here.
    """
    try:
        response = requests.get(
            f"{BASE_URL}/api/AcceptanceTestController/getAvailableLightSources",
            timeout=5,
        )
        response.raise_for_status()

        data = response.json()
        lasers = [
            source["name"]
            for source in data.get("light_sources", [])
        ]

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

    # The laser name becomes the test ID, so it is visible in the output.
    return [
        pytest.param(
            laser_name,
            id=str(laser_name),
        )
        for laser_name in lasers
    ]


@pytest.fixture
def safe_laser(request):
    """Hand over one laser and force it back to 0/inactive afterwards.

    Teardown runs even when the test fails, so none is left emitting.
    """
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


@pytest.mark.hardware
@pytest.mark.parametrize(
    "safe_laser",
    get_laser_params(),
    indirect=True,
)
def test_laser_reports_active(safe_laser):
    """Enable one laser, read active and value back, disable it again.

    This proves the API path and that ImSwitch updates its own state. It does
    not prove that light was emitted - the readbacks are ImSwitch-side, not an
    optical measurement; test_laser_photon.py covers that.
    """
    laser_name = safe_laser

    call(
        "setLaserValue",
        laserName=laser_name,
        value=1,
    )

    call(
        "setLaserActive",
        laserName=laser_name,
        active=True,
    )

    assert call(
        "getLaserActive",
        laserName=laser_name,
    ) is True, f"{laser_name}: did not become active"

    assert (
        call(
            "getLaserValue",
            laserName=laser_name,
        ) or 0
    ) > 0, f"{laser_name}: laser value is not positive"

    call(
        "setLaserActive",
        laserName=laser_name,
        active=False,
    )

    assert call(
        "getLaserActive",
        laserName=laser_name,
    ) is False, f"{laser_name}: did not become inactive"

    call(
        "setLaserValue",
        laserName=laser_name,
        value=0,
    )
