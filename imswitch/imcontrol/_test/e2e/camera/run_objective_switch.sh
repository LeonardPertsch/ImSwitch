#!/usr/bin/env bash
# Run the objective switch test on the Pi, inside the imswitch container.
# One SSH connection, so one password prompt.
#
#   ./run_objective_switch.sh
#   OBJECTIVE_SKIP_Z=1 ./run_objective_switch.sh    # turret only, no Z offset
#
# MOVES REAL HARDWARE: turns the objective turret, applies the configured Z
# offset and runs autofocus twice. Skips on any rig that has fewer than two
# configured objectives or no objective motor.
#
# Override with PI_HOST / IMSWITCH_CONTAINER / IMSWITCH_URL, plus any knob in
# KNOBS below.
set -euo pipefail

# conftest.py and colors.sh live one level up, in the suite root.
DIR="$(cd "$(dirname "$0")" && pwd)"

. "$DIR/../colors.sh"

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"

ENVS="-e IMSWITCH_URL=${IMSWITCH_URL:-http://localhost:8001}"

# Test knobs, forwarded only when set so an unset one keeps the test default.
# Names must match the os.environ lookups exactly: an unread name is handed to
# docker and then silently ignored.
KNOBS="IMSWITCH_DETECTOR UC2_LASER_VALUE
       OBJECTIVE_SETTLE_MS OBJECTIVE_MOVE_TIMEOUT OBJECTIVE_AUTOFOCUS_TIMEOUT
       OBJECTIVE_AUTOFOCUS_RANGE OBJECTIVE_AUTOFOCUS_STEP OBJECTIVE_SKIP_Z
       AUTO_EXPOSURE_RESET_MS
       PHOTON_MIN_DELTA PHOTON_NOISE_FACTOR PHOTON_NOISE_SAMPLES"

for knob in $KNOBS; do
    [ -n "${!knob:-}" ] && ENVS="$ENVS -e $knob=${!knob}"
done

# Ship the shared conftest.py alongside the test: pytest reads it from the same
# directory, so both land in one temporary folder. ustar carries no pax
# headers, so GNU tar on the Pi does not warn about macOS SCHILY.fflags.
tar --no-xattrs --format=ustar -czf - -C "$DIR/.." conftest.py -C "$DIR" test_objective_switch.py |
ssh "$PI" "cat > /tmp/objective_tests.tgz \
    && docker cp /tmp/objective_tests.tgz $CONTAINER:/tmp/ >/dev/null \
    && docker exec $CONTAINER sh -c 'rm -rf /tmp/objective_tests \
        && mkdir -p /tmp/objective_tests \
        && tar xzf /tmp/objective_tests.tgz -C /tmp/objective_tests' \
    && docker exec $ENVS $CONTAINER python3 -m pytest /tmp/objective_tests \
        -v -s -ra --tb=line $COLOR -p no:arkitekt_next -p no:cacheprovider -o markers=hardware"
