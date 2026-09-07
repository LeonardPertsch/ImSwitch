# LED Matrix E2E

Switches the ESP32 LED matrix on and checks with the camera that the light
actually arrives. A pass here cannot be faked by software state: it compares
two real frames.

The matrix has its own folder because `LaserController/getLaserNames` does not
report it. It is declared in the setup under both `lasers` and `LEDMatrixs`,
but served by `LEDMatrixController`, so the tests in [`../laser/`](../laser/)
never reach it.

## `test_ledmatrix_photon.py`

```
GET {base}/api/LEDMatrixController/setAllLEDOff
GET {base}/api/RecordingController/snapNumpyToFastAPI?detectorName=...&resizeFactor=0.25   -> dark
GET {base}/api/LEDMatrixController/setAllLED?intensity_r=20&intensity_g=20&intensity_b=20
GET {base}/api/RecordingController/snapNumpyToFastAPI?detectorName=...&resizeFactor=0.25   -> bright
```

The test passes when the mean absolute pixel difference between the two frames
reaches `PHOTON_MIN_DELTA`. Measured at intensity 20:

```
LED matrix @ intensity 20: dark_mean=2.33 bright_mean=54.75 pixel_change=52.42
```

Both frames are taken at the same exposure. `auto_exposure` from the shared
[`../conftest.py`](../conftest.py) runs one auto-exposure pass before the dark
frame, once per pytest session, so several photon modules in one run measure
against the same exposure.

## Three properties of this rig that shape the test

**The controller has only setters.** `setAllLED`, `setAllLEDOff`,
`setIntensity` and the rest all write; nothing reads back whether the matrix is
lit. Every call therefore writes unconditionally. A setup without the
controller answers 404, which skips the module.

**The dark frame is all zeros.** With the matrix off in a closed enclosure
every pixel is 0 (`min/max=(0, 0)`). The brightness assertion is on the bright
frame only — requiring a non-black baseline would fail the test on exactly the
frame it needs.

**Frame-to-frame noise never falls below ~0.52.** `settled_dark_frame` re-reads
the baseline until two consecutive dark frames differ by less than
`PHOTON_SETTLE_TOLERANCE`, which catches a stream that has just started and is
still drifting (measured at 1.66 right after `startLiveView`). The tolerance
has to sit between those two numbers:

```
0.52   sensor read noise, unreachable below this
1.66   drift of a freshly started stream, what the check is for
```

Waiting longer does not lower the noise — it is read noise, not settling — so a
tolerance under 0.52 makes the check impossible to satisfy and the module skips
every run.

## Running

```bash
./run_ledmatrix_test.sh            # pytest run on the Pi
./run_ledmatrix_test.sh --measure  # print the numbers, drop PHOTON_MIN_DELTA
```

| Variable | Default | Meaning |
|---|---|---|
| `LEDMATRIX_INTENSITY` | 20 | per-channel intensity when the matrix is on |
| `PHOTON_MIN_DELTA` | 1.5 | pixel change required to pass |
| `PHOTON_SETTLE_TOLERANCE` | 0.4 | baseline counts as quiet below this |
| `IMSWITCH_DETECTOR` | first reported | detector to snap from |
| `AUTO_EXPOSURE_RESET_MS` | 1500 | how long the one-shot auto exposure stays in auto |
| `IMSWITCH_URL` | `http://localhost:8001` | API base, see [`../README.md`](../README.md) |

Also `PI_HOST` and `IMSWITCH_CONTAINER` for the runner itself.

Skips when ImSwitch is unreachable, the setup has no detectors, there is no
`LEDMatrixController`, or the baseline never goes quiet.
