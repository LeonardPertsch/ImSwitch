#!/usr/bin/env bash
# Run the photon test on the Pi, inside the imswitch container.
# One SSH connection, so one password prompt.
#
#   ./run_photon_test.sh              # pytest run
#   ./run_photon_test.sh --measure    # just print dark/bright means, no assert
#
# Override with PI_HOST / IMSWITCH_CONTAINER / PHOTON_MIN_RATIO / UC2_LASER_VALUE.
set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
TEST="$(cd "$(dirname "$0")" && pwd)/test_led_photon.py"

ENVS="-e IMSWITCH_URL=http://localhost:8001"
[ -n "${PHOTON_MIN_RATIO:-}" ] && ENVS="$ENVS -e PHOTON_MIN_RATIO=$PHOTON_MIN_RATIO"
[ -n "${UC2_LASER_VALUE:-}" ] && ENVS="$ENVS -e UC2_LASER_VALUE=$UC2_LASER_VALUE"

# --measure: report the numbers instead of asserting, to calibrate the threshold
if [ "${1:-}" = "--measure" ]; then
    ENVS="$ENVS -e PHOTON_MIN_RATIO=0"
    EXTRA="-v -s"
else
    EXTRA="-v"
fi

ssh "$PI" "cat > /tmp/test_led_photon.py \
    && docker cp /tmp/test_led_photon.py $CONTAINER:/tmp/ >/dev/null \
    && docker exec $ENVS $CONTAINER python3 -m pytest /tmp/test_led_photon.py $EXTRA -p no:arkitekt_next" \
    < "$TEST"
