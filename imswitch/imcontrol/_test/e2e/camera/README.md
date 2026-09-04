# Camera E2E

Whatever detector the active setup provides, via `RecordingController`.

## `test_camera_snap_http.py`

The only test in `e2e/` that reaches actual hardware data. It snaps a frame over
HTTP:

```
GET {base}/api/RecordingController/snapNumpyToFastAPI?detectorName=...&resizeFactor=0.1
-> 200, image/png
```

`test_camera_returns_image` asserts status 200 and that the body really is a PNG
(Content-Type plus the 8-byte signature) with non-zero dimensions.
`test_camera_matches_sensor_resolution` additionally asserts that those
dimensions are `resizeFactor x` the frame size the camera reports. The
dimensions come straight out of the PNG IHDR header, so no Pillow or numpy.

Nothing about the sensor is hardcoded. The expected size comes from
`getCameraStatus`, preferring `currentWidth`/`currentHeight` (they already
account for ROI and binning) over `sensorWidth`/`sensorHeight`. The setup file
is *not* the source: its `managerProperties.hikcam.image_width/image_height`
say what ImSwitch asks the driver for, not what it gets — on this rig it
declares 1000x1000 while the camera delivers 3072x2048.

This matters because the laser tests' read-back only returns an ImSwitch-internal
attribute — `getLaserActive` hands back `self.enabled`, set a line earlier. A PNG
in sensor resolution can only have come off the physical sensor.

## The mock gate

A detector name does not prove hardware. When the real driver fails to start,
`HikCamManager` catches that and substitutes `MockCameraTIS`, which serves a
black frame with *"The camera is not connected"* drawn on it. That frame is a
valid PNG of plausible size, so the format checks above pass on it — the test
would go green with no camera attached.

The `camera_status` fixture closes that hole: it reads `getCameraStatus` and
skips when the detector is a mock.

The signal is `model == "mock"`, not the `isMock` flag. `MockCameraTIS` reports
model `"mock"`, and the OpenCV/Tucsen/ToupCam managers derive `isMock` from
exactly that — but `HikCamManager` derives it from the *configured* `mocktype`,
which stays `"normal"` when a mock is substituted at runtime. Forcing the mock
path shows it:

```
WARNING [HikCamManager] Failed to initialize CameraHik 99, loading TIS mocker
model       = 'mock'
isMock      = False      <- the built-in flag misses it
isConnected = False
```

`isConnected` is unusable in both directions: `HikCamManager` builds it from an
attribute the real `CameraHIK` object does not have, so it reads `False` on
working hardware too. Skipping on it would disable these tests on a rig whose
camera is delivering frames, which is why the gate ignores it.

Both flags are ImSwitch as it ships; nothing here patches them. The gate works
around them from the test side, so this folder stays independent of the version
of ImSwitch deployed on the rig.

Env: `IMSWITCH_URL`, `IMSWITCH_DETECTOR` (default: first from
`getDetectorNames`). Skips if ImSwitch is unreachable, has no detectors, or
serves a mock camera.

## Running

`run_camera_snap.sh` pipes the test through one SSH connection into the imswitch
container on the Pi, so you get a single password prompt:

```bash
./run_camera_snap.sh            # pytest run on the Pi
./run_camera_snap.sh --curl     # quick check, saves the PNG to /tmp/snap.png
```

Override with `PI_HOST`, `IMSWITCH_CONTAINER`, `IMSWITCH_URL`.
`--curl` runs from your machine, not the container, so it uses
`IMSWITCH_EXTERNAL_URL` (default `http://<PI_HOST>:8000/imswitch`) instead.

Locally, if you have pytest and requests installed:

```bash
IMSWITCH_URL=http://192.168.178.124:8000/imswitch \
  python3 -m pytest test_camera_snap_http.py -v
```

## Measured resize factors

On the current rig, a Hik camera reporting 3072x2048:

| `resizeFactor` | output |
|---|---|
| 0.1 | 307x204 |
| 0.25 | 768x512 |
| 0.5 | 1536x1024 |
| 1.0 | 3072x2048 |

These follow the attached sensor, so they change with the camera. The test
derives them rather than assuming them.
