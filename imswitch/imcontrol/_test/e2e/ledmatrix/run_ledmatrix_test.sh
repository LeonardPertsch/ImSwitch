#!/usr/bin/env bash
# Run the LED matrix photon test on the Pi, inside the imswitch container.
# One SSH connection, so one password prompt.
#
#   ./run_ledmatrix_test.sh              # pytest run
#   ./run_ledmatrix_test.sh --measure    # print dark/bright/change without asserting
#
# Override with PI_HOST / IMSWITCH_CONTAINER / IMSWITCH_URL, plus any of the
# test knobs in KNOBS below.
set -euo pipefail

# Directory of this script. The shared conftest.py and colors.sh live one level
# up, in the suite root.
DIR="$(cd "$(dirname "$0")" && pwd)"

. "$DIR/../colors.sh"

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"

# Report the measured numbers without letting the threshold fail the run.
#
# This overwrites the knob before it is forwarded, rather than appending a
# second -e after: one name then carries one value, so nothing depends on how
# docker resolves the same name given twice.
if [ "${1:-}" = "--measure" ]; then
    PHOTON_MIN_DELTA=0
    EXTRA="-v -s"
else
    EXTRA="-v"
fi

ENVS="-e IMSWITCH_URL=${IMSWITCH_URL:-http://localhost:8001}"

# Knobs test_ledmatrix_photon.py and the shared conftest.py read, forwarded
# only when actually set so an unset one keeps the default the test defines.
# The names have to match the os.environ lookups in those files exactly: an
# unread name is handed to docker and then silently ignored.
KNOBS="IMSWITCH_DETECTOR LEDMATRIX_INTENSITY PHOTON_MIN_DELTA
       PHOTON_SETTLE_TOLERANCE AUTO_EXPOSURE_RESET_MS"

for knob in $KNOBS; do
    [ -n "${!knob:-}" ] && ENVS="$ENVS -e $knob=${!knob}"
done

# Ship the test together with the shared conftest.py from one level up, which
# colours the progress percentage for skips. pytest reads it from the same
# directory as the test, so both land in one temporary folder.
tar --no-xattrs -czf - -C "$DIR/.." conftest.py -C "$DIR" test_ledmatrix_photon.py |
ssh "$PI" "cat > /tmp/ledmatrix_tests.tgz \
    && docker cp /tmp/ledmatrix_tests.tgz $CONTAINER:/tmp/ >/dev/null \
    && docker exec $CONTAINER sh -c 'rm -rf /tmp/ledmatrix_tests \
        && mkdir -p /tmp/ledmatrix_tests \
        && tar xzf /tmp/ledmatrix_tests.tgz -C /tmp/ledmatrix_tests' \
    && docker exec $ENVS $CONTAINER python3 -m pytest \
        /tmp/ledmatrix_tests $EXTRA -ra --tb=line $COLOR \
        -p no:arkitekt_next -p no:cacheprovider -o markers=hardware"
