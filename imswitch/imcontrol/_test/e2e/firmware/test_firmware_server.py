"""Check the firmware server and what it offers the boards on the CAN bus.

Read-only: nothing here flashes anything. The OTA endpoints that would are
deliberately not touched.

- test_master_firmware_is_reported: the USB-connected master identifies itself
- test_firmware_server_is_configured: an OTA firmware server URL is set
- test_firmware_server_lists_binaries: that server answers with .bin files
- test_reachable_bus_devices_have_firmware: every CAN node the master can
  actually talk to has a firmware file mapped to its id
- test_reachable_bus_devices_run_current_firmware: and it runs that firmware
"""

import os
import re

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")

# Scanning the bus takes a few seconds on the firmware side.
SCAN_TIMEOUT = int(os.environ.get("FIRMWARE_SCAN_TIMEOUT", "5"))

# A firmware image is roughly 800 kB, and the server is only reachable from
# inside the container, where the runner executes this suite.
FETCH_TIMEOUT = int(os.environ.get("FIRMWARE_FETCH_TIMEOUT", "60"))

# The three literals that identify a build: "UC2-ESP v2.0", __TIME__, __DATE__.
IDENTITY_PATTERN = re.compile(
    rb"(UC2-ESP v[0-9.]+)\x00([0-9]{2}:[0-9]{2}:[0-9]{2})\x00"
    rb"([A-Z][a-z]{2} [ 0-9][0-9] 20[0-9]{2})\x00"
)

# Images are downloaded once per URL; several CAN ids share one file.
_identities = {}


def api(method, **params):
    """Call one UC2ConfigController endpoint, skipping when ImSwitch is down.

    A refused connection says nothing about the firmware, and the suite is
    meant to be runnable without a rig.
    """
    try:
        response = requests.get(
            f"{BASE_URL}/api/UC2ConfigController/{method}",
            params=params,
            timeout=60,
        )
    except requests.RequestException as exc:
        pytest.skip(f"ImSwitch not reachable at {BASE_URL}: {exc}")

    assert response.status_code == 200, (
        f"{method} -> {response.status_code}: {response.text}"
    )

    return response.json()


def firmware_files():
    """The flat .bin list from the server, skipping when it is unreachable.

    An unreachable or unset server is a missing service rather than a broken
    one, so it skips like every other absent-hardware case in this suite.
    """
    listing = api("listAllFirmwareFiles")

    if listing.get("status") != "success":
        pytest.skip(f"firmware server unusable: {listing.get('message')}")

    return listing


@pytest.mark.hardware
def test_master_firmware_is_reported():
    """The USB-connected ESP32 master must report its own identity."""
    info = api("getFirmwareInfo")

    if info.get("status") == "error":
        pytest.skip(f"getFirmwareInfo failed: {info.get('message')}")

    assert info.get("connected") is True, f"master board is not connected: {info}"

    # The build date and pindef are what actually tell two firmwares apart, so
    # a reply without them is not proof that a master firmware is running.
    for field in ("name", "version", "date", "pindef"):
        assert info.get(field), f"master firmware reports no {field}: {info}"

    print(
        f"\nmaster: {info.get('name')} {info.get('version')} "
        f"({info.get('pindef')}, built {info.get('date')}) "
        f"on {info.get('serialport')}"
    )


@pytest.mark.hardware
def test_firmware_server_is_configured():
    """ImSwitch must know where to fetch firmware from."""
    url = api("getOTAFirmwareServer").get("firmware_server_url")

    assert url, "no OTA firmware server configured"

    print(f"\nfirmware server: {url}")


@pytest.mark.hardware
def test_firmware_server_lists_binaries():
    """The server must answer with a usable list of .bin files."""
    listing = firmware_files()
    files = listing.get("files") or []

    assert files, f"firmware server {listing.get('firmware_server')} offers no .bin files"

    # Without a name and a URL an entry cannot be downloaded, which is the
    # only thing the list is good for.
    for entry in files:
        assert entry.get("filename", "").endswith(".bin"), entry
        assert entry.get("url"), entry

    print(f"\n{len(files)} firmware files on {listing.get('firmware_server')}")


@pytest.mark.hardware
def test_reachable_bus_devices_have_firmware():
    """Every CAN node the master can talk to must have a firmware mapped.

    The mapping in UC2ConfigController._get_can_id_firmware_mapping is a fixed
    table, so a node whose id is missing from it cannot be updated through the
    CAN OTA wizard even when its binary sits on the server.

    Only nodes that answered the scan are required: an unreachable one may be
    powered down or unrouted, which says nothing about the mapping. Those are
    printed instead, because they are worth a look.
    """
    scan = api("scan_canbus", timeout=SCAN_TIMEOUT)
    devices = scan.get("scan") or []

    if not devices:
        pytest.skip("no devices on the CAN bus")

    # listAvailableFirmware keys its result by CAN id, but JSON object keys are
    # strings, so they have to be converted before comparing with the scan.
    mapped = {
        int(can_id) for can_id in (api("listAvailableFirmware").get("firmware") or {})
    }

    reachable = [
        device for device in devices
        if device.get("statusStr") != "unreachable"
    ]

    if not reachable:
        pytest.skip(f"no reachable CAN node: {scan.get('detected_ids')}")

    missing = [
        device["canId"] for device in reachable
        if device["canId"] not in mapped
    ]

    unreachable_unmapped = [
        device["canId"] for device in devices
        if device.get("statusStr") == "unreachable"
        and device["canId"] not in mapped
    ]

    print(
        f"\nbus: {scan.get('detected_ids')}, "
        f"reachable: {[d['canId'] for d in reachable]}, "
        f"firmware mapped for: {sorted(mapped)}"
    )

    if unreachable_unmapped:
        print(
            f"unreachable and unmapped (not asserted): {unreachable_unmapped}"
        )

    assert not missing, (
        f"CAN nodes {missing} answer on the bus but have no firmware mapped; "
        f"add them to _get_can_id_firmware_mapping in UC2ConfigController"
    )


def firmware_build_identity(url):
    """The (fwVersion, build) pair a node running this .bin would report.

    The build string is not stored in the image. CANopenModule.cpp assembles it
    at startup with snprintf("%s %s", __DATE__, __TIME__) into OD 0x2508, while
    OD 0x2500 receives the literal "UC2-ESP v2.0" (populateSystemOD, same
    function). Those three literals therefore sit next to each other in rodata,
    so the pair can be read back out and joined the same way the firmware does.

    Anchored on "UC2-ESP v2.0" because it occurs exactly once in the firmware
    source. Anything other than exactly one match means that assumption no
    longer holds for this image, and None makes the caller skip that device
    rather than guess.

    Do not be tempted to use the server's mod_time or the esp_app_desc
    timestamp instead: both belong to a different translation unit and are
    seconds apart from the string the node actually reports.
    """
    if url not in _identities:
        try:
            response = requests.get(url, timeout=FETCH_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            pytest.skip(f"firmware server not reachable from this host: {exc}")

        found = {
            (
                match.group(1).decode(),
                f"{match.group(3).decode()} {match.group(2).decode()}",
            )
            for match in IDENTITY_PATTERN.finditer(response.content)
        }

        _identities[url] = found.pop() if len(found) == 1 else None

    return _identities[url]


@pytest.mark.hardware
def test_reachable_bus_devices_run_current_firmware():
    """Every reachable CAN node must run the firmware the server offers it.

    Compares the node's own OD 0x2500/0x2508 strings with the identity read out
    of its mapped .bin. Both sides are exact strings from the same two
    literals, so this is an equality check, not a date comparison.

    A node without a mapping is left to test_reachable_bus_devices_have_firmware.
    A node that answers but reports no build, or an image whose identity is not
    unique, is reported and skipped rather than counted as a mismatch.
    """
    scan = api("scan_canbus", timeout=SCAN_TIMEOUT)
    devices = scan.get("scan") or []

    if not devices:
        pytest.skip("no devices on the CAN bus")

    reachable = [
        device for device in devices
        if device.get("statusStr") != "unreachable"
    ]

    if not reachable:
        pytest.skip(f"no reachable CAN node: {scan.get('detected_ids')}")

    # Keys are CAN ids, but JSON object keys are strings.
    mapped = api("listAvailableFirmware").get("firmware") or {}

    mismatches = []
    mismatch_ids = []
    undetermined = []
    compared = 0

    for device in reachable:
        can_id = device["canId"]
        entry = mapped.get(str(can_id))

        if entry is None:
            continue

        running = (device.get("fwVersion"), device.get("build"))

        if not all(running):
            undetermined.append(
                f"CAN {can_id}: answers, but reports no build/fwVersion"
            )
            continue

        expected = firmware_build_identity(entry["url"])

        if expected is None:
            undetermined.append(
                f"CAN {can_id}: no unique identity in {entry['filename']}"
            )
            continue

        compared += 1

        print(
            f"\nCAN {can_id} ({device.get('deviceTypeStr')}): "
            f"running {running[0]!r} {running[1]!r} | "
            f"expected {expected[0]!r} {expected[1]!r} "
            f"from {entry['filename']}"
        )

        if running != expected:
            mismatch_ids.append(can_id)
            mismatches.append(
                f"CAN {can_id} ({device.get('deviceTypeStr')}): "
                f"running {running[1]!r} [{running[0]}], "
                f"expected {expected[1]!r} [{expected[0]}] "
                f"from {entry['filename']}"
            )

    for line in undetermined:
        print(f"\nnot compared - {line}")

    if not compared:
        pytest.skip(
            "no reachable node whose firmware identity could be determined: "
            + "; ".join(undetermined or ["none had a firmware mapping"])
        )

    # Everything on one line, ids first: the runners pass --tb=line, which
    # shows nothing but the first line of the message.
    assert not mismatches, (
        f"CAN {mismatch_ids} outdated ({len(mismatches)} of {compared} "
        f"reachable nodes do not run the offered firmware) | "
        + " | ".join(mismatches)
    )
