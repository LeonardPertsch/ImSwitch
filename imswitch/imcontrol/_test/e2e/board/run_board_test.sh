#!/usr/bin/env bash

# Run UC2 hardware tests inside the remote ImSwitch container.

set -euo pipefail

PI="${PI_HOST:-pi@192.168.178.124}"
CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"
IMSWITCH_URL="${IMSWITCH_URL:-http://localhost:8001}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REMOTE_TEST_DIR="${REMOTE_TEST_DIR:-/tmp/uc2_tests}"

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

. "$LOCAL_DIR/../colors.sh"

ENVS="-e IMSWITCH_URL=$IMSWITCH_URL"

tar --no-xattrs -czf - \
    -C "$LOCAL_DIR/.." conftest.py \
    -C "$LOCAL_DIR" . |
ssh "$PI" "
    set -e

    cat > /tmp/uc2_tests.tgz

    docker cp \
        /tmp/uc2_tests.tgz \
        '$CONTAINER:/tmp/uc2_tests.tgz' \
        >/dev/null

    docker exec '$CONTAINER' sh -c \
        'rm -rf \"$REMOTE_TEST_DIR\" &&
         mkdir -p \"$REMOTE_TEST_DIR\" &&
         tar xzf /tmp/uc2_tests.tgz -C \"$REMOTE_TEST_DIR\"'

    docker exec \
        $ENVS \
        '$CONTAINER' \
        '$PYTHON_BIN' -m pytest \
        '$REMOTE_TEST_DIR' \
        -v \
        -ra \
        --tb=line \
        $COLOR \
        -m hardware \
        -p no:arkitekt_next \
        -p no:cacheprovider \
        -o 'markers=hardware: tests requiring real microscope hardware'
"