#!/usr/bin/env bash
# Run the LED matrix photon test on the Pi, inside the imswitch container.
# One SSH connection, so one password prompt.
#
#   ./run_ledmatrix_test.sh              # pytest run
#   ./run_ledmatrix_test.sh --measure    # print dark/bright/change without asserting
#
# Override with PI_HOST / IMSWITCH_CONTAINER / IMSWITCH_URL /
# LEDMATRIX_INTENSITY / PHOTON_MIN_DELTA.
set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
TEST="$(cd "$(dirname "$0")" && pwd)/test_ledmatrix_photon.py"

# pytest turns colour off when stdout is not a tty, and it never is here: both
# ssh and docker exec are run without one. Force it back on, but only while we
# are actually on a terminal, so redirecting to a file stays clean text.
COLOR=""
if [ -t 1 ]; then COLOR="--color=yes"; fi

ENVS="-e IMSWITCH_URL=${IMSWITCH_URL:-http://localhost:8001}"

if [ -n "${LEDMATRIX_INTENSITY:-}" ]; then
    ENVS="$ENVS -e LEDMATRIX_INTENSITY=$LEDMATRIX_INTENSITY"
fi

if [ -n "${PHOTON_MIN_DELTA:-}" ]; then
    ENVS="$ENVS -e PHOTON_MIN_DELTA=$PHOTON_MIN_DELTA"
fi

# Report the measured numbers without letting the threshold fail the run.
if [ "${1:-}" = "--measure" ]; then
    ENVS="$ENVS -e PHOTON_MIN_DELTA=0"
    EXTRA="-v -s"
else
    EXTRA="-v"
fi

# Ship the test together with the shared conftest.py from one level up, which
# colours the progress percentage for skips. pytest reads it from the same
# directory as the test, so both land in one temporary folder.
DIR="$(cd "$(dirname "$0")" && pwd)"

tar --no-xattrs -czf - -C "$DIR/.." conftest.py -C "$DIR" test_ledmatrix_photon.py |
ssh "$PI" "cat > /tmp/ledmatrix_tests.tgz \
    && docker cp /tmp/ledmatrix_tests.tgz $CONTAINER:/tmp/ >/dev/null \
    && docker exec $CONTAINER sh -c 'rm -rf /tmp/ledmatrix_tests \
        && mkdir -p /tmp/ledmatrix_tests \
        && tar xzf /tmp/ledmatrix_tests.tgz -C /tmp/ledmatrix_tests' \
    && docker exec $ENVS $CONTAINER python3 -m pytest \
        /tmp/ledmatrix_tests $EXTRA -ra --tb=line $COLOR \
        -p no:arkitekt_next -p no:cacheprovider -o markers=hardware"
