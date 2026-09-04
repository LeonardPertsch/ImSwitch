# LED Matrix E2E

The ESP32 LED matrix, measured the way `photon/` measures a laser: switch it on
and check the camera actually sees it.

## Why it is not part of `photon/`

`photon/test_led_photon.py` iterates `LaserController/getLaserNames`. On this
rig that returns `['488 Laser']` only — the matrix is declared in the setup
under both `lasers` and `LEDMatrixs`, but is served by `LEDMatrixController`
and never appears in the laser list. It therefore falls through every loop in
that file.

## `test_ledmatrix_photon.py`

```
GET {base}/api/LEDMatrixController/setAllLED?intensity_r=20&intensity_g=20&intensity_b=20
GET {base}/api/RecordingController/snapNumpyToFastAPI?detectorName=...&resizeFactor=0.25
GET {base}/api/LEDMatrixController/setAllLEDOff
```

Measured on this rig at intensity 20:

```
LED matrix @ intensity 20: dark_mean=0.00 bright_mean=3.95 pixel_change=3.95
```

Three things differ from the laser photon test, each for a reason found on
hardware:

**The dark frame really is all zeros.** With the matrix off and the enclosure
dark, every pixel is 0 (`min/max=(0, 0)`). `photon/`'s `take_image` rejects
such a frame as "acquisition may not be running", which would be wrong here, so
this file does not make that check on the baseline. It does assert the *bright*
frame is non-black.

**The baseline is settled before it is trusted.** A freshly started stream is
not quiet: two frames taken right after `startLiveView` were measured differing
by 1.66 with no light at all, against a threshold of 1.5. `settled_dark_frame`
re-reads until two consecutive dark frames agree, so that drift cannot be
credited to the matrix. Its tolerance is `PHOTON_SETTLE_TOLERANCE`, kept
separate from `PHOTON_MIN_DELTA` because `--measure` zeroes the latter.

**The controller has no getters.** `LEDMatrixController` exposes `setAllLED`,
`setAllLEDOff`, `setIntensity` and friends — all setters. Nothing can ask
whether the matrix is lit, so the test writes unconditionally rather than
checking first. A setup without the controller answers 404 and the module
skips.

Live view goes through `LiveViewController`, not
`ViewController/setLiveViewActive`; the latter returns 200 without starting
anything and answers 500 on the way out. See [`../photon/`](../photon/).

## Running

```bash
./run_ledmatrix_test.sh            # pytest run on the Pi
./run_ledmatrix_test.sh --measure  # print dark/bright/change without asserting
```

Override with `PI_HOST`, `IMSWITCH_CONTAINER`, `IMSWITCH_URL`,
`LEDMATRIX_INTENSITY` (default 20), `PHOTON_MIN_DELTA` (default 1.5),
`PHOTON_SETTLE_TOLERANCE` (default 1.5), `IMSWITCH_DETECTOR`.

Skips if ImSwitch is unreachable, the setup has no detectors, there is no
LEDMatrixController, or the camera never settles.
