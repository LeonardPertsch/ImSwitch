#!/usr/bin/env bash
# Run the camera snap test on the Pi, inside the imswitch container.
# One SSH connection, so one password prompt.
#
#   ./run_camera_snap.sh              # pytest run
#   ./run_camera_snap.sh --curl       # quick check, saves the PNG locally
#
# Two different URLs are in play, because the two modes run in two places:
#
#   IMSWITCH_URL            ImSwitch as pytest sees it, from inside the
#                           container: its own port, no caddy prefix.
#   IMSWITCH_EXTERNAL_URL   ImSwitch as --curl sees it, from this machine:
#                           through caddy on :8000 under /imswitch.
#                           Derived from PI_HOST, so it follows the rig.
#
# Override with PI_HOST / IMSWITCH_CONTAINER / IMSWITCH_URL /
# IMSWITCH_EXTERNAL_URL / IMSWITCH_DETECTOR.
set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"

CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"

ENVS="-e IMSWITCH_URL=${IMSWITCH_URL:-http://localhost:8001}"

# ${PI#*@} drops the ssh user, leaving the host the rig is actually on.
EXTERNAL="${IMSWITCH_EXTERNAL_URL:-http://${PI#*@}:8000/imswitch}"

TEST="$(cd "$(dirname "$0")" && pwd)/test_camera_snap_http.py"

DETECTOR="${IMSWITCH_DETECTOR:-RPiCam}"

# pytest turns colour off when stdout is not a tty, and it never is here: both
# ssh and docker exec are run without one. Force it back on, but only while we
# are actually on a terminal, so redirecting to a file stays clean text.
COLOR=""
if [ -t 1 ]; then COLOR="--color=yes"; fi


if [ "${1:-}" = "--curl" ]; then
    curl -sS -o /tmp/snap.png -w 'HTTP %{http_code}  %{content_type}  %{size_download} bytes\n' \
        "$EXTERNAL/api/RecordingController/snapNumpyToFastAPI?detectorName=$DETECTOR&resizeFactor=0.1"
    echo "saved to /tmp/snap.png"
    exit 0
fi

# Ship the test together with the shared conftest.py from one level up, which
# colours the progress percentage for skips. pytest reads it from the same
# directory as the test, so both land in one temporary folder.
DIR="$(cd "$(dirname "$0")" && pwd)"

tar --no-xattrs -czf - -C "$DIR/.." conftest.py -C "$DIR" test_camera_snap_http.py |
ssh "$PI" "cat > /tmp/camera_tests.tgz \
    && docker cp /tmp/camera_tests.tgz $CONTAINER:/tmp/ >/dev/null \
    && docker exec $CONTAINER sh -c 'rm -rf /tmp/camera_tests \
        && mkdir -p /tmp/camera_tests \
        && tar xzf /tmp/camera_tests.tgz -C /tmp/camera_tests' \
    && docker exec $ENVS $CONTAINER python3 -m pytest /tmp/camera_tests \
        -v --tb=line $COLOR -p no:arkitekt_next -o markers=hardware"
