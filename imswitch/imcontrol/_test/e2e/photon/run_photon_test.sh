```bash
#!/usr/bin/env bash

# Run the photon tests inside the ImSwitch container.
#
#   ./run_photon_test.sh
#   ./run_photon_test.sh --measure

set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
TEST="$(cd "$(dirname "$0")" && pwd)/test_led_photon.py"

ENVS="-e IMSWITCH_URL=${IMSWITCH_URL:-http://localhost:8001}"

[ -n "${UC2_LASER_VALUE:-}" ] &&
    ENVS="$ENVS -e UC2_LASER_VALUE=$UC2_LASER_VALUE"

[ -n "${PHOTON_MIN_RATIO:-}" ] &&
    ENVS="$ENVS -e PHOTON_MIN_RATIO=$PHOTON_MIN_RATIO"

[ -n "${PHOTON_MIN_DELTA:-}" ] &&
    ENVS="$ENVS -e PHOTON_MIN_DELTA=$PHOTON_MIN_DELTA"

if [ "${1:-}" = "--measure" ]; then
    ENVS="$ENVS -e PHOTON_MIN_RATIO=0 -e PHOTON_MIN_DELTA=0"
    EXTRA="-v -s"
else
    EXTRA="-v"
fi

ssh "$PI" "cat > /tmp/test_led_photon.py \
    && docker cp /tmp/test_led_photon.py $CONTAINER:/tmp/ >/dev/null \
    && docker exec $ENVS $CONTAINER python3 -m pytest \
        /tmp/test_led_photon.py $EXTRA -p no:arkitekt_next" \
    < "$TEST"
```
