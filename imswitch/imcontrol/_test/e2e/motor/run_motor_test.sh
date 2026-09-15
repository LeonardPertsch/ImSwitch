#!/usr/bin/env bash

# Run the motor tests on the remote Pi inside the ImSwitch container.
#
#   ./run_motor_test.sh                 # every motor test
#   ./run_motor_test.sh '/tmp/motor_tests/test_motor_motion.py'
#   ./run_motor_test.sh '/tmp/motor_tests/test_motor_endstop_approach.py::test_axis_reaches_endstop[Y]'
#
# The argument is a pytest target *as seen inside the container*, so it starts
# with REMOTE_TEST_DIR. Quote it: the [Y] of a parametrised id is a glob.
#
# test_motor_endstop_approach.py drives an axis into its endstop and stays
# disabled unless ENDSTOP_APPROACH is set, so a plain run never moves into a
# limit:
#
#   ENDSTOP_APPROACH=1 ENDSTOP_MAX_TRAVEL_UM=500 ENDSTOP_STEP_UM=100 \
#     ./run_motor_test.sh '/tmp/motor_tests/test_motor_endstop_approach.py::test_axis_reaches_endstop[Y]'
#
# test_motor_motion_camera.py is NOT gated: a plain run parks the stage at the
# transport position and moves every axis. run_motor_motion_camera.sh runs only
# that test.

set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
IMSWITCH_URL="${IMSWITCH_URL:-http://localhost:8001}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REMOTE_TEST_DIR="${REMOTE_TEST_DIR:-/tmp/motor_tests}"

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

. "$LOCAL_DIR/../colors.sh"

# Every knob the tests in this folder read, forwarded only when it is actually
# set in the environment, so an unset one keeps the default the test defines.
# Repeating the defaults here instead would mean two places to keep in sync.
#
# ENVS has to stay a SINGLE LINE: it is interpolated into the ssh command
# below, where an embedded newline would end the `docker exec` line early and
# leave it without arguments.
KNOBS="ENDSTOP_APPROACH ENDSTOP_MAX_TRAVEL_UM ENDSTOP_STEP_UM ENDSTOP_READ_TIMEOUT
       MOTION_CAMERA_DISTANCE_UM MOTION_CAMERA_MIN_PIXELS MOTION_CAMERA_SETTLE_MS
       MOTION_CAMERA_ROI_PERCENT MOTION_CAMERA_SPEED MOTION_CAMERA_TOP_CROP_PERCENT
       MOTION_CAMERA_Z_DIRECTION
       TRANSPORT_TIMEOUT
       TRANSPORT_SPEED"

ENVS="-e IMSWITCH_URL=$IMSWITCH_URL"

for knob in $KNOBS; do
    value="${!knob:-}"

    if [ -n "$value" ]; then
        ENVS="$ENVS -e $knob=$value"
    fi
done

# Optional pytest target, defaulting to the whole folder.
TARGET="${1:-$REMOTE_TEST_DIR}"

# ustar carries no pax extended headers, so GNU tar on the Pi does not
# warn about the SCHILY.fflags that macOS bsdtar would otherwise write.
tar --no-xattrs --format=ustar -czf - \
    -C "$LOCAL_DIR/.." conftest.py \
    -C "$LOCAL_DIR" . |
ssh "$PI" "
    set -e

    cat > /tmp/motor_tests.tgz

    docker cp \
        /tmp/motor_tests.tgz \
        '$CONTAINER:/tmp/motor_tests.tgz' \
        >/dev/null

    docker exec '$CONTAINER' sh -c \
        'rm -rf \"$REMOTE_TEST_DIR\" &&
         mkdir -p \"$REMOTE_TEST_DIR\" &&
         tar xzf /tmp/motor_tests.tgz -C \"$REMOTE_TEST_DIR\"'

    docker exec \
        $ENVS \
        '$CONTAINER' \
        '$PYTHON_BIN' -m pytest \
        '$TARGET' \
        -s \
        -v \
        -ra \
        --tb=line \
        $COLOR \
        -m hardware \
        -p no:arkitekt_next \
        -p no:cacheprovider \
        -o 'markers=hardware: tests requiring real microscope hardware'
"
