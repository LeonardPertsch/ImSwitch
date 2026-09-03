#!/usr/bin/env bash
# Run the camera snap test on the Pi, inside the imswitch container.
# One SSH connection, so one password prompt.
#
#   ./run_camera_snap.sh              # pytest run
#   ./run_camera_snap.sh --curl       # quick check, saves the PNG locally
#
# Override with PI_HOST / IMSWITCH_CONTAINER / IMSWITCH_URL.
set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"

CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"

BASE="${IMSWITCH_URL:-http://192.168.178.124:8000/imswitch}"

TEST="$(cd "$(dirname "$0")" && pwd)/test_camera_snap_http.py"

DETECTOR="${IMSWITCH_DETECTOR:-RPiCam}"


if [ "${1:-}" = "--curl" ]; then
    curl -sS -o /tmp/snap.png -w 'HTTP %{http_code}  %{content_type}  %{size_download} bytes\n' \
        "$BASE/api/RecordingController/snapNumpyToFastAPI?detectorName=$DETECTOR&resizeFactor=0.1"
    echo "saved to /tmp/snap.png"
    exit 0
fi

ssh "$PI" "cat > /tmp/test_camera_snap_http.py \
    && docker cp /tmp/test_camera_snap_http.py $CONTAINER:/tmp/ >/dev/null \
    && docker exec $CONTAINER python3 -m pytest /tmp/test_camera_snap_http.py -v -p no:arkitekt_next" \
    < "$TEST"
