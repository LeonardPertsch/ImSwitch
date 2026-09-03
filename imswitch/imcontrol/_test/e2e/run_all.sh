#!/usr/bin/env bash
# Run the whole e2e suite on the Pi, inside the imswitch container.
# Ships this folder over one SSH connection, so one password prompt.
#
#   ./run_all.sh                 # everything
#   ./run_all.sh camera          # only camera/
#   ./run_all.sh laser photon    # several folders
#
# Override with PI_HOST / IMSWITCH_CONTAINER / PHOTON_MIN_RATIO / UC2_LASER_VALUE.
set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
DIR="$(cd "$(dirname "$0")" && pwd)"

# inside the container ImSwitch is on :8001 without the caddy prefix
ENVS="-e IMSWITCH_URL=http://localhost:8001"
[ -n "${PHOTON_MIN_RATIO:-}" ] && ENVS="$ENVS -e PHOTON_MIN_RATIO=$PHOTON_MIN_RATIO"
[ -n "${UC2_LASER_VALUE:-}" ] && ENVS="$ENVS -e UC2_LASER_VALUE=$UC2_LASER_VALUE"

# optional folder filter: ./run_all.sh camera laser
TARGETS=""
for arg in "$@"; do TARGETS="$TARGETS /tmp/e2e/$arg"; done
[ -z "$TARGETS" ] && TARGETS="/tmp/e2e"

tar --no-xattrs -czf - -C "$DIR" . | ssh "$PI" "
    cat > /tmp/e2e.tgz &&
    docker cp /tmp/e2e.tgz $CONTAINER:/tmp/e2e.tgz >/dev/null &&
    docker exec $CONTAINER sh -c 'rm -rf /tmp/e2e && mkdir -p /tmp/e2e && tar xzf /tmp/e2e.tgz -C /tmp/e2e' &&
    docker exec $ENVS $CONTAINER python3 -m pytest $TARGETS \
        -v -ra -p no:arkitekt_next -p no:cacheprovider -o markers=hardware
"
