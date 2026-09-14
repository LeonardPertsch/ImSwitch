#!/usr/bin/env bash

# Run the camera-witnessed motion test on the remote Pi.
# THIS MOVES THE STAGE: first to the transport position, then every axis out
# and back.
#
#   ./run_motor_motion_camera.sh
#
# Everything goes through run_motor_test.sh, so its overrides and the
# MOTION_CAMERA_* tuning knobs work here too:
#
#   MOTION_CAMERA_DISTANCE_UM=500 ./run_motor_motion_camera.sh

set -euo pipefail

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_TEST_DIR="${REMOTE_TEST_DIR:-/tmp/motor_tests}"

REMOTE_TEST_DIR="$REMOTE_TEST_DIR" \
    exec "$LOCAL_DIR/run_motor_test.sh" "$REMOTE_TEST_DIR/test_motor_motion_camera.py"
