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
# test_motor_endstop_approach.py homes X and Y into their endstops, only on a
# FRAME setup and only while ENABLED = True is set in that file:
#
#   ./run_motor_test.sh '/tmp/motor_tests/test_motor_endstop_approach.py::test_axis_reaches_endstop[Y]'
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
KNOBS="MOTION_CAMERA_DISTANCE_UM MOTION_CAMERA_MIN_PIXELS MOTION_CAMERA_SETTLE_MS
       MOTION_CAMERA_ROI_PERCENT MOTION_CAMERA_SPEED MOTION_CAMERA_TOP_CROP_PERCENT
       MOTION_CAMERA_Z_DIRECTION MOTION_CAMERA_Z_SCALE_MIN MOTION_CAMERA_Z_SCALE_MAX
       MOTION_CAMERA_Z_SCALE_STEP MOTION_CAMERA_Z_MIN_SCALE_CHANGE
       MOTION_CAMERA_Z_SCALE_NOISE_RATIO
       TRANSPORT_TIMEOUT
       TRANSPORT_SPEED"

ENVS="-e IMSWITCH_URL=$IMSWITCH_URL"

for knob in $KNOBS; do
    value="${!knob:-}"

    if [ -n "$value" ]; then
        ENVS="$ENVS -e $knob=$value"
    fi
done

# Optional pytest targets, defaulting to the whole folder. Each one is
# single-quoted for the remote shell, so a [Y] parametrised id is not globbed.
TARGETS=""
for target in "${@:-$REMOTE_TEST_DIR}"; do
    TARGETS="$TARGETS '$target'"
done

# ustar carries no pax extended headers, so GNU tar on the Pi does not
# warn about the SCHILY.fflags that macOS bsdtar would otherwise write.
#
# Upload and test run are two ssh calls sharing one master connection, so it
# is still one password prompt. The test run needs its own call: the upload's
# stdin is the tar stream, so it cannot have a terminal.
SSH_OPTS=(-o ControlMaster=auto -o ControlPath="/tmp/motor-ssh-$$" -o ControlPersist=60)
trap 'ssh -o ControlPath="/tmp/motor-ssh-$$" -O exit "$PI" 2>/dev/null || true' EXIT

tar --no-xattrs --format=ustar -czf - \
    -C "$LOCAL_DIR/.." conftest.py \
    -C "$LOCAL_DIR" . |
ssh "${SSH_OPTS[@]}" "$PI" "
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
"

# -t (ssh) and -it (docker exec) give pytest a terminal, so Ctrl+C reaches
# pytest inside the container. Without them Ctrl+C only killed the local ssh
# client and the tests kept moving the stage on the Pi. Only when we are on a
# terminal: docker exec -it refuses to start without one.
SSH_TTY_FLAG="" DOCKER_TTY_FLAG=""
if [ -t 0 ]; then SSH_TTY_FLAG="-t" DOCKER_TTY_FLAG="-it"; fi

ssh $SSH_TTY_FLAG "${SSH_OPTS[@]}" "$PI" "
    docker exec \
        $DOCKER_TTY_FLAG \
        $ENVS \
        '$CONTAINER' \
        '$PYTHON_BIN' -m pytest \
        $TARGETS \
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
