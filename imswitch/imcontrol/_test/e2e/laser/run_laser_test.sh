#!/usr/bin/env bash

# Run the laser hardware tests on a remote machine inside the ImSwitch container.
#
# This ships the whole folder, so both files run: test_laser_http.py drives the
# API and reads state back, test_laser_photon.py measures with the camera
# whether light actually arrived. Each discovers the lasers/LEDs from the active
# setup and makes one test case per reported laser.
#
# Usage:
#
#   ./run_laser_test.sh
#       Run all laser hardware tests.
#
#   ./run_laser_test.sh --measure
#       Print the measured brightness per laser with the threshold dropped, so
#       a dim light does not fail the run. Useful for setting the threshold
#       after moving a light or the optics. A light that produces no signal at
#       all still fails, because that is a result rather than a calibration
#       question.
#
# Configuration can be overridden through environment variables:
#
#   PI_HOST
#       SSH target of the machine running the ImSwitch container.
#
#   IMSWITCH_CONTAINER
#       Name of the Docker container running ImSwitch.
#
#   IMSWITCH_URL
#       ImSwitch API URL as seen from inside the container.
#
#   PYTHON_BIN
#       Python executable inside the container.
#
#   REMOTE_TEST_DIR
#       Temporary directory used inside the container for the tests.
#
# Example:
#
#   PI_HOST=pi@192.168.1.20 \
#   IMSWITCH_CONTAINER=imswitch-server-1 \
#   IMSWITCH_URL=http://localhost:8001 \
#   ./run_laser_test.sh

set -euo pipefail


# Remote machine running Docker.
PI="${PI_HOST:-pi@192.168.178.124}"

# Docker container running ImSwitch.
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"

# ImSwitch URL from the perspective of the container.
IMSWITCH_URL="${IMSWITCH_URL:-http://localhost:8001}"

# Python executable inside the ImSwitch container.
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Temporary location used inside the container.
REMOTE_TEST_DIR="${REMOTE_TEST_DIR:-/tmp/laser_tests}"

# Directory containing this script and the pytest files. The whole directory is
# shipped, so adding a test file here is enough to have it run.
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# pytest turns colour off when stdout is not a tty, and it never is here: both
# ssh and docker exec are run without one. Force it back on, but only while we
# are actually on a terminal, so redirecting to a file stays clean text.
COLOR=""
if [ -t 1 ]; then COLOR="--color=yes"; fi

# Environment for the tests inside the container. test_laser_photon.py reads
# the two photon knobs; test_laser_http.py ignores them.
ENVS="-e IMSWITCH_URL=$IMSWITCH_URL"

if [ -n "${UC2_LASER_VALUE:-}" ]; then
    ENVS="$ENVS -e UC2_LASER_VALUE=$UC2_LASER_VALUE"
fi

if [ -n "${PHOTON_MIN_DELTA:-}" ]; then
    ENVS="$ENVS -e PHOTON_MIN_DELTA=$PHOTON_MIN_DELTA"
fi

# Report the measured brightness without letting the threshold fail the run.
# Only meaningful for the photon test; the on/off test is unaffected.
EXTRA=""
if [ "${1:-}" = "--measure" ]; then
    ENVS="$ENVS -e PHOTON_MIN_DELTA=0"
    EXTRA="-s"
fi


# Pack the local laser-test directory and send it to the remote machine through
# the existing SSH connection.
#
# The archive is then copied into the ImSwitch container and extracted into a
# clean temporary directory. This makes the runner independent of the local
# absolute path of the repository.
# The shared conftest.py lives one level up and is picked up from the same
# directory as the tests, so it is packed alongside them.
tar --no-xattrs -czf - -C "$LOCAL_DIR/.." conftest.py -C "$LOCAL_DIR" . |
ssh "$PI" "
    set -e

    cat > /tmp/laser_tests.tgz

    docker cp \
        /tmp/laser_tests.tgz \
        '$CONTAINER:/tmp/laser_tests.tgz' \
        >/dev/null

    docker exec '$CONTAINER' sh -c \
        'rm -rf \"$REMOTE_TEST_DIR\" &&
         mkdir -p \"$REMOTE_TEST_DIR\" &&
         tar xzf /tmp/laser_tests.tgz -C \"$REMOTE_TEST_DIR\"'

    docker exec \
        $ENVS \
        '$CONTAINER' \
        '$PYTHON_BIN' -m pytest \
        '$REMOTE_TEST_DIR' \
        -v \
        -ra \
        --tb=line \
        $COLOR \
        $EXTRA \
        -m hardware \
        -p no:arkitekt_next \
        -p no:cacheprovider \
        -o 'markers=hardware: tests requiring real microscope hardware'
"
