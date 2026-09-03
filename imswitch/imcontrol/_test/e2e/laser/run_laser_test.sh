#!/usr/bin/env bash
# Run the laser test on the Pi, inside the imswitch container.
# One SSH connection, so one password prompt.
#
#   ./run_laser_test.sh          # pytest
#   ./run_laser_test.sh --wire   # show the JSON going over the wire to the ESP32
#
# Override with PI_HOST / IMSWITCH_CONTAINER.
set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
DIR="$(cd "$(dirname "$0")" && pwd)"

# --wire runs the diagnostic script instead; it needs the serial port to itself,
# so it only works while ImSwitch is not connected to the ESP32.
if [ "${1:-}" = "--wire" ]; then
    ssh "$PI" "docker exec -i $CONTAINER python3 -" < "$DIR/show_wire_traffic.py"
    exit 0
fi

tar --no-xattrs -czf - -C "$DIR" . | ssh "$PI" "
    cat > /tmp/laser.tgz &&
    docker cp /tmp/laser.tgz $CONTAINER:/tmp/laser.tgz >/dev/null &&
    docker exec $CONTAINER sh -c 'rm -rf /tmp/laser && mkdir -p /tmp/laser && tar xzf /tmp/laser.tgz -C /tmp/laser' &&
    docker exec -e IMSWITCH_URL=http://localhost:8001 $CONTAINER python3 -m pytest /tmp/laser \
        -v -ra -p no:arkitekt_next -p no:cacheprovider -o markers=hardware
"
