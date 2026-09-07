#!/usr/bin/env bash
# Run the whole e2e suite on the Pi, inside the imswitch container.
# Ships this folder over one SSH connection, so one password prompt.
#
#   ./run_all.sh                 # everything
#   ./run_all.sh camera          # only camera/
#   ./run_all.sh laser photon    # several folders
#
# Override with PI_HOST / IMSWITCH_CONTAINER, plus any of the test knobs in
# KNOBS below.
set -euo pipefail
PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
DIR="$(cd "$(dirname "$0")" && pwd)"

. "$DIR/colors.sh"

# inside the container ImSwitch is on :8001 without the caddy prefix
ENVS="-e IMSWITCH_URL=http://localhost:8001"

# Every knob the tests in this folder read, forwarded only when it is actually
# set in the environment, so an unset one keeps the default the test defines.
#
# The names have to match the os.environ lookups in the test files exactly. A
# name that matches nothing is still handed to docker and then silently ignored
# by the tests, which is how PHOTON_MIN_RATIO survived here: no test ever read
# it, so the photon threshold could not be set through this script at all.
KNOBS="IMSWITCH_DETECTOR UC2_LASER_VALUE PHOTON_MIN_DELTA
       PHOTON_SETTLE_TOLERANCE LEDMATRIX_INTENSITY AUTO_EXPOSURE_RESET_MS"

for knob in $KNOBS; do
    [ -n "${!knob:-}" ] && ENVS="$ENVS -e $knob=${!knob}"
done

# optional folder filter: ./run_all.sh camera laser
TARGETS=""
for arg in "$@"; do TARGETS="$TARGETS /tmp/e2e/$arg"; done
[ -z "$TARGETS" ] && TARGETS="/tmp/e2e"

tar --no-xattrs -czf - -C "$DIR" . | ssh "$PI" "
    cat > /tmp/e2e.tgz &&
    docker cp /tmp/e2e.tgz $CONTAINER:/tmp/e2e.tgz >/dev/null &&
    docker exec $CONTAINER sh -c 'rm -rf /tmp/e2e && mkdir -p /tmp/e2e && tar xzf /tmp/e2e.tgz -C /tmp/e2e' &&
    docker exec $ENVS $CONTAINER python3 -m pytest $TARGETS \
        -v -ra --tb=line $COLOR -p no:arkitekt_next -p no:cacheprovider -o markers=hardware
"
