# Camera E2E

Raspberry Pi camera (IMX477, 4056x3040) via `RecordingController`.

## `test_camera_snap_http.py`

The only test in `e2e/` that reaches actual hardware data. It snaps a frame over
HTTP:

```
GET {base}/api/RecordingController/snapNumpyToFastAPI?detectorName=RPiCam&resizeFactor=0.1
-> 200, image/png, ~11 KB
```

and asserts three things: status 200, that the body really is a PNG
(Content-Type plus the 8-byte signature), and that the dimensions match
`resizeFactor x` full sensor resolution (4056x3040 -> 405x304). The dimensions
come straight out of the PNG IHDR header, so no Pillow or numpy.

This matters because the laser tests' read-back only returns an ImSwitch-internal
attribute — `getLaserActive` hands back `self.enabled`, set a line earlier. A PNG
in sensor resolution can only have come off the physical sensor.

Env: `IMSWITCH_URL`, `IMSWITCH_DETECTOR` (default: first from
`getDetectorNames`). Skips if ImSwitch is unreachable or has no detectors.

## Running

`run_camera_snap.sh` pipes the test through one SSH connection into the imswitch
container on the Pi, so you get a single password prompt:

```bash
./run_camera_snap.sh            # pytest run on the Pi
./run_camera_snap.sh --curl     # quick check, saves the PNG to /tmp/snap.png
```

Override with `PI_HOST`, `IMSWITCH_CONTAINER`, `IMSWITCH_URL`.

Locally, if you have pytest and requests installed:

```bash
IMSWITCH_URL=http://192.168.178.124:8000/imswitch \
  python3 -m pytest test_camera_snap_http.py -v
```

## Measured resize factors

| `resizeFactor` | output |
|---|---|
| 0.1 | 405x304 |
| 0.25 | 1014x760 |
| 0.5 | 2028x1520 |
| 1.0 | 4056x3040 |
