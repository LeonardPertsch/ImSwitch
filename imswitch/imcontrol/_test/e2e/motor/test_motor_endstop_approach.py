"""Actively drive an axis towards its home endstop and verify the switch trips.

DANGEROUS / ACTIVE HARDWARE TEST.

Disabled by default. Enable explicitly with:

    ENDSTOP_APPROACH=1

The test:
    1. reads the axis configuration and derives the approach direction from it
    2. verifies that the endstop can be read and is currently released
    3. approaches in small blocking increments, reading the input after each one
    4. verifies that the raw endstop signal reaches its pressed level
    5. retreats off the switch again

Direction is taken from configuration, never from the current coordinate. Two
reasons: coordinate 0 is not the switch (the configured homing path drives into
the reference and then backs off by endposRelease before declaring 0), and both
physical endstops of an axis may share one digital-input line
(FocusMotor.cpp: "Both physical endstops of a linear axis are wired in parallel
onto a single GPIO"), so a "pressed" reading alone does not prove which end was
reached. Driving the wrong way could therefore report success at the far end.
"""

import os

import pytest
import requests


BASE_URL = os.environ.get("IMSWITCH_URL", "http://localhost:8001")

# ---------------------------------------------------------------------------
# Safety switch. Set this to True (or ENDSTOP_APPROACH=1 in the environment) to
# let the test run. Off by default because it drives an axis into a hardware
# limit, which needs someone to have checked that the travel is clear.
ENABLED = os.environ.get(
    "ENDSTOP_APPROACH", ""
).strip().lower() in ("1", "true", "yes", "on")
# ---------------------------------------------------------------------------

# One approach increment. Small enough that the axis cannot gain much speed
# before the endstop is read again.
STEP_UM = float(os.environ.get("ENDSTOP_STEP_UM", "200"))

# Hard ceiling on the distance this test may travel, and the only bound on the
# approach. It is deliberately NOT derived from the current coordinate: nothing
# in the exposed configuration says how far the switch is, so an estimate could
# only ever be a guess that silently enlarges the budget.
MAX_TRAVEL_UM = float(os.environ.get("ENDSTOP_MAX_TRAVEL_UM", "20000"))

READ_TIMEOUT = float(os.environ.get("ENDSTOP_READ_TIMEOUT", "3"))

# Position the firmware documents for a tripped hard limit. Kept as a refusal
# condition even though the current firmware appears to only describe it in
# MotorJsonParser::parseSetHardLimits without writing it anywhere.
HARD_LIMIT_POSITION = 999999

# Firmware convention: DIGITAL_IN_1/2/3 are the X/Y/Z limit switches
# (HomeMotor::loop passes getDigitalVal(1/2/3) to X/Y/Z, and
# FocusMotor::checkHardLimits does the same). No API exposes this mapping.
# A is absent on purpose — there is no known DIGITAL_IN_4.
ENDSTOP_ID = {
    "X": 1,
    "Y": 2,
    "Z": 3,
}


pytestmark = pytest.mark.skipif(
    not ENABLED,
    reason="active hardware test — set ENDSTOP_APPROACH=1 to enable",
)


def sign(value):
    return (value > 0) - (value < 0)


def api(controller, method, **params):
    response = requests.get(
        f"{BASE_URL}/api/{controller}/{method}",
        params=params,
        timeout=60,
    )

    assert response.status_code == 200, (
        f"{controller}/{method} -> "
        f"{response.status_code}: {response.text}"
    )

    return response.json()


def endstop_value(payload, digitalinid):
    """Extract digitalinval from the UC2-REST response."""

    frames = payload if isinstance(payload, list) else [payload]

    for frame in frames:
        if not isinstance(frame, dict):
            continue

        digitalin = frame.get("digitalin")

        entries = (
            digitalin
            if isinstance(digitalin, list)
            else [digitalin]
        )

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            if entry.get("digitalinid") == digitalinid:
                return entry.get("digitalinval")

    return None


def read_endstop(digitalinid):
    """Fresh digital-input read using the normal qid-matched path."""

    payload = api(
        "UC2ConfigController",
        "getDigitalIn",
        digitalinid=digitalinid,
        timeout=READ_TIMEOUT,
    )

    return endstop_value(payload, digitalinid)


def motor_settings():
    return api(
        "UC2ConfigController",
        "getMotorSettings",
    )


def positioner_name():
    names = api(
        "PositionerController",
        "getPositionerNames",
    )

    return names[0] if names else None


def position_of(positioner, axis):
    positions = api(
        "PositionerController",
        "getPositionerPositions",
    )

    axes = positions.get(positioner)

    assert axes is not None, (
        f"positioner {positioner!r} missing from getPositionerPositions "
        f"({sorted(positions)})"
    )

    assert axis in axes, (
        f"{positioner!r} reports no axis {axis!r} ({sorted(axes)})"
    )

    return axes[axis]


def approach_direction(homing, motion):
    """Sign of the move that leads towards the home endstop, in µm.

    Two separate quantities have to be combined, and neither alone is the
    answer:

    homing.direction is a HARDWARE-STEP direction. It is what ImSwitch puts
    into /home_act, and the firmware drives +direction to search for the switch
    (HomeMotor.cpp phase 1 "Fast approach": md->speed = +homeDirection * speed,
    while phase 0 releases with -homeDirection).

    motion.stepSize carries the wiring polarity in its SIGN. UC2-REST's
    setup_motor() splits a negative step size into magnitude plus
    direction = -1, and move_stepper() then converts µm to hardware steps as
    steps = µm * direction / |stepSize|. So on an axis with a negative step
    size, a positive µm move is a negative hardware-step move.

    Hence: µm direction towards the switch = sign(direction) * sign(stepSize).

    Returns (direction, problem). direction is +1/-1 and problem is None when
    the configuration is unambiguous; otherwise direction is None and problem
    describes why movement must be refused.
    """
    home_direction = homing.get("direction")
    step_size = motion.get("stepSize")

    if home_direction is None or step_size is None:
        return None, (
            f"incomplete configuration "
            f"(homing.direction={home_direction!r}, "
            f"motion.stepSize={step_size!r})"
        )

    if sign(home_direction) == 0 or sign(step_size) == 0:
        return None, (
            f"ambiguous configuration "
            f"(homing.direction={home_direction!r}, "
            f"motion.stepSize={step_size!r})"
        )

    direction = sign(home_direction) * sign(step_size)

    # Independent cross-check. homeSteps is fed straight into the manager's
    # move() and is therefore already a µm-space value, whose sign points at
    # the reference: ESP32StageManager.home_x drives +homeSteps and then backs
    # off -sign(homeSteps) * endposRelease. If the two sources disagree, one of
    # the assumptions above does not hold on this rig and moving is unsafe.
    home_steps = homing.get("homeSteps") or 0

    if home_steps and sign(home_steps) != direction:
        return None, (
            f"direction sources disagree: "
            f"sign(homing.direction={home_direction}) * "
            f"sign(motion.stepSize={step_size}) = {direction:+d}, "
            f"but sign(homing.homeSteps={home_steps}) = "
            f"{sign(home_steps):+d}"
        )

    return direction, None


def configured_axes():
    """Axes with homing, hard-limit protection and a known endstop input.

    Runs at import time, so it must not raise: pytest evaluates parametrize
    arguments while collecting, before the skipif above can take effect. With
    the test disabled nothing is asked of the server at all, and an
    unreachable ImSwitch yields an empty list rather than a collection error.
    """
    if not ENABLED:
        return []

    try:
        axes = motor_settings().get("axes", {})
    except Exception:
        return []

    result = []

    for axis in ENDSTOP_ID:
        config = axes.get(axis, {})

        homing = config.get("homing", {})
        limits = config.get("limits", {})

        if (
            homing.get("enabled")
            and limits.get("hardLimitsEnabled")
        ):
            result.append(axis)

    return result


@pytest.mark.hardware
@pytest.mark.parametrize(
    "axis",
    configured_axes() or [None],
)
def test_axis_reaches_endstop(axis):
    if axis is None:
        pytest.skip(
            "no axis with homing, hard limits and a known endstop input"
        )

    config = motor_settings()["axes"][axis]

    homing = config["homing"]
    motion = config["motion"]
    limits = config["limits"]

    assert homing.get("enabled") is True, (
        f"{axis}: refusing active approach because homing is disabled"
    )

    assert limits.get("hardLimitsEnabled") is True, (
        f"{axis}: refusing active approach because hard limits are disabled"
    )

    digitalinid = ENDSTOP_ID[axis]

    # Which logic level the switch reads when pressed. This says nothing about
    # which way to travel — the firmware compares the same way in
    # HomeMotor.cpp: endstopTriggered = (endstopState == homeEndStopPolarity).
    pressed_level = homing.get("endstopPolarity")

    assert pressed_level in (0, 1), (
        f"{axis}: homing.endstopPolarity is {pressed_level!r}, "
        "cannot tell which level means pressed"
    )

    direction, problem = approach_direction(homing, motion)

    assert direction is not None, (
        f"{axis}: refusing to move — {problem}"
    )

    positioner = positioner_name()

    if positioner is None:
        pytest.skip("no positioner available")

    start_position = position_of(positioner, axis)

    assert start_position != HARD_LIMIT_POSITION, (
        f"{axis}: axis already reports hard-limit error position "
        f"{HARD_LIMIT_POSITION}"
    )

    # Never start a drive towards a switch that cannot be read — without a
    # reading there is nothing to stop the approach.
    start_endstop = read_endstop(digitalinid)

    assert start_endstop in (0, 1), (
        f"{axis}: endstop cannot be read ({start_endstop!r}); refusing to move"
    )

    if start_endstop == pressed_level:
        pytest.skip(
            f"{axis}: endstop is already pressed — "
            "back the axis off first, there is no transition to observe"
        )

    print(
        f"\n{axis}:\n"
        f"  start position           = {start_position} µm\n"
        f"  digital input            = {digitalinid}\n"
        f"  initial endstop state    = {start_endstop}\n"
        f"  pressed level            = {pressed_level}\n"
        f"  homing direction (hw)    = {homing.get('direction'):+d}\n"
        f"  stepSize                 = {motion.get('stepSize')}\n"
        f"  derived approach dir     = {direction:+d} µm\n"
        f"  homeSteps                = {homing.get('homeSteps')}\n"
        f"  endposRelease            = {homing.get('endposRelease')}\n"
        f"  travel ceiling           = {MAX_TRAVEL_UM:.0f} µm\n"
        f"  increment                = {STEP_UM:.0f} µm"
    )

    travelled = 0.0
    hit = False

    try:
        while travelled < MAX_TRAVEL_UM:
            step = min(STEP_UM, MAX_TRAVEL_UM - travelled)

            api(
                "PositionerController",
                "movePositioner",
                positionerName=positioner,
                axis=axis,
                dist=direction * step,
                isAbsolute=False,
                isBlocking=True,
            )

            travelled += step

            value = read_endstop(digitalinid)

            assert value in (0, 1), (
                f"{axis}: lost endstop readback during approach "
                f"after {travelled:.0f} µm"
            )

            if value == pressed_level:
                hit = True

                print(
                    f"{axis}: endstop pressed after "
                    f"approximately {travelled:.0f} µm"
                )

                break

        assert hit, (
            f"{axis}: endstop never became pressed within the "
            f"{MAX_TRAVEL_UM:.0f} µm ceiling (travelled {travelled:.0f} µm)"
        )

    finally:
        if travelled:
            recover(positioner, axis, direction, travelled,
                    digitalinid, pressed_level, start_position)


def recover(positioner, axis, direction, travelled,
            digitalinid, pressed_level, start_position):
    """Drive back off the switch. Never raises.

    A plain relative move opposite to the approach is the escape the firmware
    intends. A hard-limit trip sets a DIRECTIONAL lockout
    (FocusMotor::setHardLimitLockoutDir, persisted to NVS) and
    FocusMotor::directionAllowed only rejects motion in that same direction —
    the opposite direction stays permitted. Leaving the switch then clears the
    lockout on its own, on the falling edge of the signal.

    Deliberately not done here:

    homeAxis() — on a rig where homing.homeSteps is non-zero,
    ESP32StageManager.home_x takes the open-loop branch, which issues plain
    move() calls and never touches the lockout. Its first move goes towards the
    reference, i.e. into the locked-out direction, so it would be rejected by
    startStepper() and clear nothing. Only the firmware's endstop-homing state
    machine calls clearHardLimitTriggered.

    An absolute move back to start — after a trip the axis has lost an unknown
    number of steps, so the coordinate system is no longer trustworthy. A
    relative retreat by the distance travelled keeps the hardware safe without
    pretending the coordinate is exact.
    """
    print(f"{axis}: retreating {travelled:.0f} µm (direction {-direction:+d})")

    try:
        api(
            "PositionerController",
            "movePositioner",
            positionerName=positioner,
            axis=axis,
            dist=-direction * travelled,
            isAbsolute=False,
            isBlocking=True,
        )
    except Exception as error:
        print(
            f"{axis}: RECOVERY FAILED, axis may still be against the switch "
            f"({error}) — stop and check the stage before moving it again"
        )
        return

    # Confirm the axis actually left the switch, so a stuck signal or a failed
    # escape is visible rather than assumed.
    try:
        released = read_endstop(digitalinid)
    except Exception as error:
        print(f"{axis}: endstop unreadable after retreat ({error})")
        return

    if released == pressed_level:
        print(
            f"{axis}: RECOVERY REQUIRED — endstop still reads pressed "
            f"({released}) after retreating {travelled:.0f} µm. Not moving "
            "further; free the axis manually, then home it."
        )
        return

    try:
        position = position_of(positioner, axis)
    except Exception as error:
        print(f"{axis}: position unreadable after retreat ({error})")
        return

    print(
        f"{axis}: endstop released, position {position} µm "
        f"(started at {start_position} µm, "
        f"difference {position - start_position:+.1f} µm)"
    )

    if position == HARD_LIMIT_POSITION:
        print(
            f"{axis}: position reads {HARD_LIMIT_POSITION} — coordinate "
            "system is invalid, home the axis before relying on it"
        )
