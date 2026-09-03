# Photon E2E

The one test here that **cannot pass without real photons**.

Every other test reads back state that software set:

| Test | What the read-back proves |
|---|---|
| `laser/test_laser3_http.py` | `getLaserActive` returns `self.enabled`, set a line earlier |
| `laser/test_laser3_serial.py` | the ESP32's own PWM register |
| `camera/test_camera_snap_http.py` | a PNG of the right size arrived — not what is in it |
| **`photon/test_led_photon.py`** | **light physically hit the sensor** |

## `test_led_photon.py`

Everything goes through the ImSwitch HTTP API:

```
setLaserValue(0)    + setLaserActive(false) -> snap -> mean brightness  (dark)
setLaserValue(1000) + setLaserActive(true)  -> snap -> mean brightness  (bright)
assert bright / dark >= PHOTON_MIN_RATIO
```

Brightness is the mean of the greyscale-converted PNG (`PIL.ImageStat`). A
fixture switches the LED back off afterwards.

Measured on this rig: **dark 0.02, bright 115.47, ratio ~7500**. The default
threshold is 5.0, far below that, so the test also catches a dimmed or
half-covered LED rather than only a completely dark one.

Because it drives the LED through ImSwitch, the setup must map the LED onto the
ESP32 (see `../laser/README.md`) and ImSwitch must have been restarted since.
It then runs happily alongside the camera and HTTP laser tests - only
`../laser/test_laser3_serial.py` steps aside, since ImSwitch holds the port.

## Running

```bash
./run_photon_test.sh              # pytest
./run_photon_test.sh --measure    # print dark/bright/ratio without asserting
```

Use `--measure` to see the raw numbers without asserting, e.g. after moving the
LED or changing the optics. Tighten or loosen the threshold from there:

```bash
PHOTON_MIN_RATIO=50 ./run_photon_test.sh
```

Env: `IMSWITCH_URL`, `IMSWITCH_LASER`, `IMSWITCH_DETECTOR`, `UC2_LASER_VALUE`
(default 1000), `PHOTON_MIN_RATIO` (default 5.0).

## Why it might fail even though everything works

- **The LED is not in the camera's field of view.** Most likely cause. It has to
  actually illuminate what the sensor sees.
- **Auto-exposure compensates.** The camera may darken the image as the scene
  gets brighter, cancelling the effect. Fix by pinning exposure via
  `SettingsController` before snapping.
- **Threshold too tight** for a dim LED. Calibrate with `--measure`.

Unlike the other tests, a brightness difference that fails to show up is a
**failure, not a skip** — that is the entire point of this test.
