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
`getCameraStatus`, preferring `currentWidth`/`currentHeight` over
`sensorWidth`/`sensorHeight` because they already account for ROI and binning.

The setup file is the wrong source for this. Its
`managerProperties.hikcam.image_width/image_height` say what ImSwitch asks the
driver for, not what the driver returns — on this rig it declares 1000x1000
while the camera delivers 3072x2048.

A frame in sensor resolution can only have come off the physical sensor, which
is more than the laser HTTP test can show: `getLaserActive` there hands back
`self.enabled`, set a line earlier.

## The mock gate

A detector name does not prove hardware. When the real driver fails to start,
`HikCamManager` catches that and substitutes `MockCameraTIS`, which serves a
black frame with *"The camera is not connected"* drawn on it. That frame is a
valid PNG of plausible size, so the format checks above pass on it — the test
would go green with no camera attached.

The `camera_status` fixture closes that hole: it reads `getCameraStatus` and
skips when the detector is a mock.

The signal is `model == "mock"`. `MockCameraTIS` reports that model, and the
OpenCV, Tucsen and ToupCam managers derive their `isMock` flag from it.

Two fields in `getCameraStatus` look like they would do the job and do not,
both because of how `HikCamManager` fills them:

| Field | Real camera | Mock | Usable |
|---|---|---|---|
| `model` | `CameraHIK` | `mock` | yes |
| `isMock` | False | False | no — derived from the configured `mocktype`, which stays `"normal"` when a mock is substituted at runtime |
| `isConnected` | False | False | no — probes an attribute `CameraHIK` does not have, so it reads False on working hardware |

Skipping on `isConnected` would disable the camera tests on a rig whose camera
is delivering frames, so the gate ignores it. Both flags are ImSwitch as it
ships; the gate works around them from the test side and needs no particular
ImSwitch version on the rig.

## Running

`run_camera_snap.sh` pipes the test through one SSH connection into the imswitch
container on the Pi, so you get a single password prompt:

```bash
./run_camera_snap.sh            # pytest run on the Pi
./run_camera_snap.sh --curl     # quick check, saves the PNG to /tmp/snap.png
```

| Variable | Default | Meaning |
|---|---|---|
| `IMSWITCH_DETECTOR` | first reported | detector to snap from |
| `IMSWITCH_URL` | `http://localhost:8001` | API base inside the container |
| `IMSWITCH_EXTERNAL_URL` | `http://<PI_HOST>:8000/imswitch` | API base for `--curl`, which runs on your machine |

Also `PI_HOST` and `IMSWITCH_CONTAINER` for the runner itself. The two URLs are
explained in [`../README.md`](../README.md).

Running pytest directly works too, given pytest and requests and a reachable
ImSwitch:

```bash
IMSWITCH_URL=http://<pi>:8000/imswitch python3 -m pytest test_camera_snap_http.py -v
```

## Measured resize factors

Output size is `resizeFactor x` the frame size the camera reports, so these
follow whichever sensor is attached. With the Hik camera on this rig, reporting
3072x2048:

| `resizeFactor` | output |
|---|---|
| 0.1 | 307x204 |
| 0.25 | 768x512 |
| 0.5 | 1536x1024 |
| 1.0 | 3072x2048 |
