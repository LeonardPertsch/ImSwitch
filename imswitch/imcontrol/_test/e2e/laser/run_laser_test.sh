#!/usr/bin/env bash

# Run the laser hardware tests on a remote machine inside the ImSwitch container.
#
# This ships the whole folder, so both files run: test_laser_switching.py drives the
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
# The test knobs themselves are listed in KNOBS below and documented with their
# defaults in README.md.
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

# Shared colour setup, from the suite root one level up.
. "$LOCAL_DIR/../colors.sh"

# Report the measured brightness without letting the threshold fail the run.
# Only meaningful for the photon test; the on/off test is unaffected.
#
# This overwrites the knob before it is forwarded, rather than appending a
# second -e after: one name then carries one value, so nothing depends on how
# docker resolves the same name given twice.
EXTRA=""
if [ "${1:-}" = "--measure" ]; then
    PHOTON_MIN_DELTA=0
    EXTRA="-s"
fi

# Environment for the tests inside the container.
ENVS="-e IMSWITCH_URL=$IMSWITCH_URL"

# Knobs test_laser_photon.py and the shared conftest.py read, forwarded only
# when actually set so an unset one keeps the default the test defines.
# test_laser_switching.py ignores all of them. The names have to match the
# os.environ lookups in those files exactly: an unread name is handed to docker
# and then silently ignored.
KNOBS="IMSWITCH_DETECTOR UC2_LASER_VALUE PHOTON_MIN_DELTA
       AUTO_EXPOSURE_RESET_MS PHOTON_SETTLE_TOLERANCE"

for knob in $KNOBS; do
    [ -n "${!knob:-}" ] && ENVS="$ENVS -e $knob=${!knob}"
done


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
