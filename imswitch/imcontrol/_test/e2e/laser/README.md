# Laser / LED E2E

Two tests on the same lights, at two different levels of proof. Both discover
the lights from `LaserController/getLaserNames` and make one test case per
reported laser.

| Test | What a pass proves |
|---|---|
| `test_laser_http.py` | the request travelled through ImSwitch without error |
| `test_laser_photon.py` | light physically hit the camera sensor |

The LED matrix is not covered here — it is served by `LEDMatrixController` and
never appears in `getLaserNames`. See [`../ledmatrix/`](../ledmatrix/).

## `test_laser_http.py`

Switches each light through ImSwitch and asserts on the read-back:

```
GET /api/LaserController/setLaserActive?laserName=...&active=true
  -> LaserController.toggleLaser
  -> ESP32LEDLaserManager.setEnabled       (channel_index == LASERid)
  -> uc2rest -> /dev/ttyUSB0
  -> ESP32   {"task":"/laser_act","LASERid":3,...}
```

**What it does not prove.** `getLaserActive` returns `self.enabled`, which
`setLaserActive` assigned a moment earlier. A pass means no error on the way
through, not that any light came out — with the LED unplugged this still
passes. A fixture forces the laser back to 0 and off afterwards, in a
`try/finally` so a failed test cannot leave it lit.

## `test_laser_photon.py`

The one test here that **cannot pass without real photons**:

```
all lights off        -> snap -> mean brightness   (dark)
setLaserValue(1000)
setLaserActive(true)  -> snap -> mean brightness   (bright)
assert mean|bright - dark| >= PHOTON_MIN_DELTA
```

Brightness is the mean of the greyscale PNG (`PIL.ImageStat`), and the
comparison is an absolute **difference**, not a ratio. Before each test every
known light goes off, the LED matrix included — `all_lights_off` calls
`LEDMatrixController/setAllLEDOff` because the matrix is invisible to
`getLaserNames` and would otherwise brighten the "dark" frame.

Live view runs through `LiveViewController`, not
`ViewController/setLiveViewActive`. The latter is the older path and is broken
on these rigs: its `_acqHandle` is already set at boot while
`LiveViewController` owns the actual stream, so `setLiveViewActive(True)`
returns 200 without starting anything and `setLiveViewActive(False)` always
answers `500 Invalid or already used handle`. That 500 was the teardown error
this folder used to end every run with.

Measured on the current rig: the 488 laser produces `pixel_change=0.00`, while
the LED matrix at intensity 20 produces 3.95 through the same camera and code
path. The camera and the threshold are fine; that laser does not reach the
sensor.

## Running

```bash
./run_laser_test.sh              # both tests
./run_laser_test.sh --measure    # print the brightness numbers, drop the threshold
```

`--measure` sets `PHOTON_MIN_DELTA=0`, so a dim light no longer fails the run
and you can read off a sensible threshold. A light that produces *no* signal at
all still fails, with `no light reached the sensor` — that is a result, not a
calibration question:

```
488 Laser: dark_mean=0.00 bright_mean=0.00 pixel_change=0.00
```

The runner ships the whole folder, so adding a test file here is enough to have
it run. Env: `PI_HOST`, `IMSWITCH_CONTAINER`, `IMSWITCH_URL`,
`IMSWITCH_DETECTOR`, `UC2_LASER_VALUE` (default 1000), `PHOTON_MIN_DELTA`
(default 1.5).

## What the setup file needs

The tests skip while the active setup has no lasers. It needs an ESP32 and a
laser bound to it — `channel_index` is the LASERid, so an LED on GPIO2 is
`channel_index: 3`:

```json
"rs232devices": {
  "ESP32": {
    "managerName": "ESP32Manager",
    "managerProperties": {"serialport": "/dev/ttyUSB0", "baudrate": 115200, "debug": 1}
  }
},
"lasers": {
  "LED": {
    "managerName": "ESP32LEDLaserManager",
    "managerProperties": {"rs232device": "ESP32", "channel_index": 3},
    "wavelength": 488, "valueRangeMin": 0, "valueRangeMax": 1023
  }
}
```

Firmware pin map, read from `/laser_get`:

```
LASER1pin: GPIO12    LASER2pin: GPIO4    LASER3pin: GPIO2
```

Two things bite here:

- **ImSwitch reads the setup only at startup.** Editing the file changes nothing
  until `docker restart imswitch-server-1`.
- `channel_index` **must be an integer**. The string `'LED'`, used by several of
  the older setups on the Pi, raises a hard `ValueError` on load.

## Why the photon test might fail even though everything works

- **The light is not in the camera's field of view.** Most likely cause. It has
  to actually illuminate what the sensor sees.
- **Auto-exposure compensates**, darkening the image as the scene brightens and
  cancelling the effect. Pin the exposure via `SettingsController` first.
- **Threshold too tight** for a dim light. Calibrate with `--measure`.
- **A freshly started stream is not settled.** Two frames taken right after
  `startLiveView` were measured differing by 1.66 with no light at all, against
  a threshold of 1.5 — close enough to flip a run either way.
  [`../ledmatrix/`](../ledmatrix/) guards against this with a settled-baseline
  check; this file does not yet.

Unlike the other tests, a brightness difference that fails to show up is a
**failure, not a skip** — that is the entire point.

## Limits

- Opening the serial port resets the ESP32 via DTR/RTS. Unavoidable on the CP2102.
- The firmware reports internal PWM state, not a measurement.
- `success` is not a uniform code: `/laser_act` answers `success:1`,
  `/ledarr_act` answers `success:0`. Nothing here asserts on it.
