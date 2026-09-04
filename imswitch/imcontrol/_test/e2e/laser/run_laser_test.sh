#!/usr/bin/env bash

# Run the laser hardware tests on a remote machine inside the ImSwitch container.
#
# The Python tests discover all lasers/LEDs from the active ImSwitch setup and
# create one separate pytest test case for every reported laser.
#
# Usage:
#
#   ./run_laser_test.sh
#       Run all laser hardware tests.
#
#   ./run_laser_test.sh --wire
#       Run the optional serial diagnostic script that shows the JSON sent
#       towards the ESP32.
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

# Directory containing this script, the pytest files and the optional
# show_wire_traffic.py diagnostic script.
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# pytest turns colour off when stdout is not a tty, and it never is here: both
# ssh and docker exec are run without one. Force it back on, but only while we
# are actually on a terminal, so redirecting to a file stays clean text.
COLOR=""
if [ -t 1 ]; then COLOR="--color=yes"; fi


# Pack the local laser-test directory and send it to the remote machine through
# the existing SSH connection.
#
# The archive is then copied into the ImSwitch container and extracted into a
# clean temporary directory. This makes the runner independent of the local
# absolute path of the repository.
tar --no-xattrs -czf - -C "$LOCAL_DIR" . |
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
        -e IMSWITCH_URL='$IMSWITCH_URL' \
        '$CONTAINER' \
        '$PYTHON_BIN' -m pytest \
        '$REMOTE_TEST_DIR' \
        -v \
        -ra \
        --tb=line \
        $COLOR \
        -m hardware \
        -p no:arkitekt_next \
        -p no:cacheprovider \
        -o 'markers=hardware: tests requiring real microscope hardware'
"
