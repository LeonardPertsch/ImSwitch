#!/usr/bin/env bash
#
# hil-run.sh -- test one ImSwitch image on the real rig, then put the rig back.
#
#   check  ->  swap in the image under test  ->  ship the suite  ->  pytest
#                                                       ... always swap back
#
# Runs ON the Pi, not from your machine: it talks to the local Docker daemon.
# From your machine use run_all.sh, which tests whatever image already runs.
#
# Why swap instead of installing: forklift owns the deployment, and the image
# it pins is the one the device is supposed to run. The swap is a temporary
# override that the restore removes again, so a finished run -- or a failed
# one, or an interrupted one -- leaves the same container the user had.
#
# Usage:
#   ci/hil-run.sh --image sha-7d3adda --yes
#   ci/hil-run.sh --image ghcr.io/openuc2/imswitch:sha-7d3adda --yes \
#                 --tests "board firmware" --out reports
#
# MOVES THE STAGE AND SWITCHES LIGHT ON: --yes is mandatory, so no scheduler
# and no stray call can actuate the rig by accident.
#
# Exit codes (CI depends on them):
#   0  the suite passed
#   1  a test failed
#   2  the run could not be performed (bad arguments, no disk, no ImSwitch,
#      no board, a detector that never became ready, another run in progress,
#      the swap or the restore failed)
set -uo pipefail

CONTAINER="${IMSWITCH_CONTAINER:-imswitch-server-1}"

# ImSwitch as seen from the Pi itself: :8001 is not published on the host, so
# every host-side request goes through caddy. Inside the container the tests
# use :8001 directly -- see IMSWITCH_URL below.
HOST_URL="${HIL_HOST_URL:-http://localhost:8000/imswitch}"
CONTAINER_URL="${HIL_CONTAINER_URL:-http://localhost:8001}"

# An ImSwitch image is ~6.3 GB, and the card has been at 74% before. Refuse to
# pull rather than fill the root filesystem of a machine someone else uses.
MIN_FREE_GB="${HIL_MIN_FREE_GB:-15}"

# How long ImSwitch may take to answer after a container swap. It loads the
# setup file, opens the camera SDK and talks to the ESP32 before serving.
READY_TIMEOUT="${HIL_READY_TIMEOUT:-180}"

# How long one detector may take to deliver its first frame, counted per
# detector. Deliberately separate from READY_TIMEOUT: /api/version answers as
# soon as the web server is up, while a camera SDK can still be enumerating
# behind it. The Hikrobot camera has needed minutes there, and snapping it in
# that window answers 500 -- which would look like a broken image rather than
# a rig that is not warm yet.
DETECTOR_TIMEOUT="${HIL_DETECTOR_TIMEOUT:-300}"

# One rig, one run. A second run would fight the first over the container.
LOCK_FILE="${HIL_LOCK_FILE:-/tmp/hil-run.lock}"

# The override lives in /tmp, never in the forklift stage directory: that
# directory belongs to forklift, and this file must not survive us. It only
# sets an image, so it needs no path resolution of its own.
OVERRIDE_FILE="${HIL_OVERRIDE_FILE:-/tmp/hil-override.compose.yml}"

DEFAULT_REGISTRY="ghcr.io/openuc2/imswitch"

DIR="$(cd "$(dirname "$0")" && pwd)"
SUITE_DIR="$(cd "$DIR/.." && pwd)"

IMAGE=""
TESTS=""
OUT_DIR="$DIR/reports"
CONFIRMED=0
KEEP_IMAGE=0

EXIT_OK=0
EXIT_FAILED=1
EXIT_UNAVAILABLE=2

log()  { printf '[hil-run] %s\n' "$*"; }
warn() { printf '[hil-run] %s\n' "$*" >&2; }
die()  { warn "$*"; exit "$EXIT_UNAVAILABLE"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --image)      IMAGE="${2:-}"; shift 2 ;;
        --tests)      TESTS="${2:-}"; shift 2 ;;
        --out)        OUT_DIR="${2:-}"; shift 2 ;;
        --yes)        CONFIRMED=1; shift ;;
        --keep-image) KEEP_IMAGE=1; shift ;;
        -h|--help)    sed -n '2,40p' "$0"; exit "$EXIT_OK" ;;
        *)            die "unknown argument: $1" ;;
    esac
done

[ -n "$IMAGE" ] || die "usage: $0 --image <tag or ref> --yes [--tests \"motor camera\"]"
[ "$CONFIRMED" = "1" ] || die "refusing to actuate the rig without --yes"

# A bare tag is the common case; a full ref stays untouched, so a fork's own
# registry works without a flag.
case "$IMAGE" in
    */*) IMAGE_REF="$IMAGE" ;;
    *)   IMAGE_REF="$DEFAULT_REGISTRY:$IMAGE" ;;
esac


# ---------------------------------------------------------------------------
# Preconditions. Each one is something that would otherwise fail halfway
# through, with the rig already swapped.
# ---------------------------------------------------------------------------

command -v docker >/dev/null || die "docker not found -- run this on the Pi"

docker inspect "$CONTAINER" >/dev/null 2>&1 ||
    die "container $CONTAINER not found -- is ImSwitch deployed?"

exec 9>"$LOCK_FILE" || die "cannot open lock file $LOCK_FILE"
flock -n 9 || die "another hil-run holds $LOCK_FILE -- one run per rig"

free_gb="$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
[ "${free_gb:-0}" -ge "$MIN_FREE_GB" ] ||
    die "only ${free_gb}G free on / , need ${MIN_FREE_GB}G for the image"

imswitch_version() {
    curl -sf -m 10 "$HOST_URL/api/version" 2>/dev/null
}

[ -n "$(imswitch_version)" ] ||
    die "ImSwitch does not answer at $HOST_URL -- not swapping anything"

board="$(curl -sf -m 15 "$HOST_URL/api/UC2ConfigController/uc2_board_is_connected" 2>/dev/null)"
[ "$board" = "true" ] ||
    die "UC2 board not connected (answer: ${board:-none}) -- the tests would all skip"


# ---------------------------------------------------------------------------
# Where the deployment lives. Read from the container rather than hardcoded:
# forklift moves the package to a new stage directory on every apply, and the
# feature set decides which compose files take part.
# ---------------------------------------------------------------------------

label() { docker inspect "$CONTAINER" --format "{{index .Config.Labels \"$1\"}}"; }

PROJECT="$(label com.docker.compose.project)"
PKG_DIR="$(label com.docker.compose.project.working_dir)"
CONFIG_FILES="$(label com.docker.compose.project.config_files)"
ORIGINAL_IMAGE="$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')"

[ -n "$PROJECT" ] && [ -d "$PKG_DIR" ] && [ -n "$CONFIG_FILES" ] ||
    die "cannot read the compose setup off $CONTAINER"

# Rebuild the exact -f list the deployment uses, in order: the later files
# carry the device rules and the caddy labels, and dropping one would start a
# container that cannot see the hardware or cannot be reached through caddy.
COMPOSE_ARGS=(--project-name "$PROJECT" --project-directory "$PKG_DIR")
IFS=',' read -r -a config_list <<< "$CONFIG_FILES"
for file in "${config_list[@]}"; do
    # forklift writes absolute paths into the label; a relative one is meant
    # against the package directory. Prefixing an absolute path with PKG_DIR
    # builds something that cannot exist, so decide per entry.
    case "$file" in
        /*) config_path="$file" ;;
        *)  config_path="$PKG_DIR/$file" ;;
    esac

    # Our own override can still be listed here: compose records the -f list a
    # container was started with, and a restore that did not have to recreate
    # the container leaves that entry in the label. It is not part of the
    # deployment -- the swap adds it again when it is wanted -- and the file is
    # usually gone, so skip it instead of dying on it.
    [ "$config_path" = "$OVERRIDE_FILE" ] && continue

    [ -f "$config_path" ] || die "compose file missing: $config_path"
    COMPOSE_ARGS+=(-f "$config_path")
done

compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }
compose_with_override() { docker compose "${COMPOSE_ARGS[@]}" -f "$OVERRIDE_FILE" "$@"; }

log "container   $CONTAINER (project $PROJECT)"
log "package     $PKG_DIR"
log "running     $ORIGINAL_IMAGE"
log "testing     $IMAGE_REF"


# ---------------------------------------------------------------------------
# Restore. Installed before the first change and run on every exit path, so an
# interrupted run still gives the microscope back on the image it came with.
# ---------------------------------------------------------------------------

wait_for_imswitch() {
    local waited=0
    while [ "$waited" -lt "$READY_TIMEOUT" ]; do
        [ -n "$(imswitch_version)" ] && return 0
        sleep 5
        waited=$((waited + 5))
    done
    return 1
}

RESTORED=0

restore() {
    [ "$RESTORED" = "1" ] && return
    RESTORED=1

    log "restoring $ORIGINAL_IMAGE"

    rm -f "$OVERRIDE_FILE"

    if ! compose up -d --no-build 2>&1 | sed 's/^/[hil-run]   /'; then
        warn "RESTORE FAILED -- the rig may still run $IMAGE_REF"
        warn "fix by hand: docker compose --project-name $PROJECT --project-directory $PKG_DIR ... up -d"
        return
    fi

    if wait_for_imswitch; then
        log "restored, ImSwitch answers again"
    else
        warn "restored the container, but ImSwitch does not answer yet"
    fi

    # Only ever remove what this run pulled, and never the image the rig runs
    # on: the card holds the rollback image too, and that one must stay.
    if [ "$KEEP_IMAGE" = "0" ] && [ "$IMAGE_REF" != "$ORIGINAL_IMAGE" ]; then
        docker rmi "$IMAGE_REF" >/dev/null 2>&1 &&
            log "removed $IMAGE_REF" ||
            log "kept $IMAGE_REF (still in use)"
    fi
}

trap restore EXIT INT TERM


# ---------------------------------------------------------------------------
# Swap.
# ---------------------------------------------------------------------------

log "pulling $IMAGE_REF"
docker pull "$IMAGE_REF" 2>&1 | sed 's/^/[hil-run]   /' ||
    die "pull failed -- wrong tag, or not logged in to the registry"

cat > "$OVERRIDE_FILE" <<EOF
# Written by hil-run.sh, removed again by its restore step. If you find this
# file on a rig, a run was killed hard -- check which image is running.
services:
  server:
    image: $IMAGE_REF
EOF

log "starting the container on the image under test"
compose_with_override up -d --no-build 2>&1 | sed 's/^/[hil-run]   /' ||
    die "compose up failed"

log "waiting for ImSwitch (up to ${READY_TIMEOUT}s)"
wait_for_imswitch || die "ImSwitch did not answer within ${READY_TIMEOUT}s"

running_version="$(imswitch_version)"
running_image="$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')"
log "now running  $running_image"
log "reports      $running_version"

[ "$running_image" = "$IMAGE_REF" ] ||
    die "the container runs $running_image, not the image under test"


# ---------------------------------------------------------------------------
# Detector readiness. An answering API is not a ready microscope: the camera
# tests snap a frame, and a camera still starting up answers 500. Waiting here
# rather than in the tests keeps the tests honest -- a retry loop inside them
# would hide a camera that genuinely broke.
# ---------------------------------------------------------------------------

# Read the detectors from the setup instead of naming them here: which cameras
# exist depends on the setup file the image under test loads.
detector_names() {
    # A JSON array of plain strings, e.g. ["WidefieldCamera","ObservationCamera"].
    curl -sf -m 15 "$HOST_URL/api/SettingsController/getDetectorNames" 2>/dev/null |
        tr -d '[]"' | tr ',' '\n' | sed 's/^ *//; s/ *$//' | grep -v '^$'
}

# Snap one frame and report only the HTTP status.
#
# The suite draws the same distinction in conftest.py (is_observation_camera):
# snapNumpyToFastAPI serves detectors with forAcquisition=true and answers 500
# for the observation camera, which has to go through the overview endpoint.
# Asking the wrong endpoint would wait out the full timeout on a camera that
# was ready all along.
detector_snap_code() {
    local detector="$1"

    case "$(printf '%s' "$detector" | tr '[:upper:]' '[:lower:]')" in
        *observ*)
            curl -s -o /dev/null -w '%{http_code}' -m 30 -X POST \
                "$HOST_URL/api/ExperimentController/snapOverviewImage?slot_id=1&camera_name=hil_ready_check"
            ;;
        *)
            curl -s -o /dev/null -w '%{http_code}' -m 30 -G \
                --data-urlencode "detectorName=$detector" \
                --data-urlencode "resizeFactor=0.1" \
                "$HOST_URL/api/RecordingController/snapNumpyToFastAPI"
            ;;
    esac
}

wait_for_detector() {
    local detector="$1"
    local started="$SECONDS"
    local code=""

    while [ $((SECONDS - started)) -lt "$DETECTOR_TIMEOUT" ]; do
        code="$(detector_snap_code "$detector")"

        if [ "$code" = "200" ]; then
            log "detector    $detector ready after $((SECONDS - started))s"
            return 0
        fi

        # 400 is the overview endpoint saying no overview camera is bound.
        # No amount of waiting changes that, and the suite skips those tests
        # rather than failing them, so it must not hold up the run either.
        if [ "$code" = "400" ]; then
            log "detector    $detector: no overview camera bound (400), not waiting"
            return 0
        fi

        sleep 5
    done

    warn "detector    $detector still answers ${code:-nothing} after ${DETECTOR_TIMEOUT}s"
    return 1
}

log "waiting for every detector to deliver a frame (up to ${DETECTOR_TIMEOUT}s each)"

DETECTORS="$(detector_names)"
[ -n "$DETECTORS" ] ||
    die "ImSwitch reports no detectors -- the camera tests would all skip"

# Every detector is waited out even after one fails, so the log shows how long
# each of them really took and whether the timeout is set anywhere near right.
NOT_READY=""
while IFS= read -r detector; do
    wait_for_detector "$detector" || NOT_READY="$NOT_READY $detector"
done <<< "$DETECTORS"

# Exit 2, not 1: a camera that never woke up says nothing about the image, and
# CI must not page anyone about a red test that never ran.
[ -z "$NOT_READY" ] ||
    die "detector(s) not ready within ${DETECTOR_TIMEOUT}s:$NOT_READY -- not running the suite"


# ---------------------------------------------------------------------------
# Ship the suite and run it. The image carries no e2e folder, and a swapped
# container starts with an empty /tmp, so this happens after the swap.
# ---------------------------------------------------------------------------

log "shipping the suite into the container"
tar --format=ustar -czf - -C "$SUITE_DIR" --exclude=__pycache__ --exclude=ci . |
    docker exec -i "$CONTAINER" sh -c \
        'rm -rf /tmp/e2e && mkdir -p /tmp/e2e && tar xzf - -C /tmp/e2e' ||
    die "could not ship the suite"

TARGETS=""
for folder in $TESTS; do TARGETS="$TARGETS /tmp/e2e/$folder"; done
[ -z "$TARGETS" ] && TARGETS="/tmp/e2e"

mkdir -p "$OUT_DIR"
REPORT="$OUT_DIR/junit-${IMAGE##*:}-$(date +%Y-%m-%d_%H-%M-%S).xml"

log "running pytest on:${TARGETS}"

# No --tb=line here: CI reads the report, and a human reading a failed nightly
# wants the assertion, not one line of it.
docker exec -e "IMSWITCH_URL=$CONTAINER_URL" "$CONTAINER" \
    python3 -m pytest $TARGETS \
        -v -ra --tb=short \
        --junitxml=/tmp/e2e-report.xml \
        -p no:arkitekt_next -p no:cacheprovider -o markers=hardware
STATUS=$?

docker cp "$CONTAINER:/tmp/e2e-report.xml" "$REPORT" >/dev/null 2>&1 &&
    log "report $REPORT" ||
    warn "no report written -- pytest died before it could write one"

if [ "$STATUS" -eq 0 ]; then
    log "PASS  $IMAGE_REF"
    exit "$EXIT_OK"
fi

warn "FAIL  $IMAGE_REF (pytest exit $STATUS)"
exit "$EXIT_FAILED"
