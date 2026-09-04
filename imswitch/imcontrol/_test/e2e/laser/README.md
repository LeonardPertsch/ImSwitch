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
comparison is an absolute **difference**, not a ratio.

`all_lights_off` runs before each test and switches off every light the setup
knows, plus the LED matrix via `LEDMatrixController/setAllLEDOff`. The matrix
needs its own call because `getLaserNames` does not report it, and a lit matrix
would raise the dark frame.

Both frames are taken at the same exposure. `auto_exposure` from the shared
[`../conftest.py`](../conftest.py) runs one auto-exposure pass before the dark
frame, once per pytest session, so with several lights every one of them is
measured against the same exposure.

Live view runs through `LiveViewController`. `ViewController/setLiveViewActive`
does not work on these rigs: its `_acqHandle` is set at boot while
`LiveViewController` owns the actual stream, so `setLiveViewActive(True)`
returns 200 without starting anything and `setLiveViewActive(False)` answers
`500 Invalid or already used handle` on every call.

## What the numbers look like

Frame-to-frame noise on this rig sits at ~0.5 with everything off. A light that
reaches the sensor clears that by a wide margin, so the two cases are not close
together:

| Light | `pixel_change` |
|---|---|
| LED matrix at intensity 20, via [`../ledmatrix/`](../ledmatrix/) | ~52 |
| 488 laser at full value | ~0.5 |

0.5 is the noise floor, so the 488 laser contributes nothing measurable. The
camera, the threshold and the measurement path are all working — the light does
not arrive at the sensor.

## Running

```bash
./run_laser_test.sh              # both tests
./run_laser_test.sh --measure    # print the brightness numbers, drop the threshold
```

`--measure` sets `PHOTON_MIN_DELTA=0` and prints each light's numbers, so a dim
light does not fail the run and a sensible threshold can be read off. A light
that produces no signal at all still fails, with `no light reached the sensor`
— that is a measurement result rather than a calibration question.

The runner ships the whole folder, so adding a test file here is enough to have
it run.

| Variable | Default | Meaning |
|---|---|---|
| `UC2_LASER_VALUE` | 1000 | value each light is set to when switched on |
| `PHOTON_MIN_DELTA` | 1.5 | pixel change required to pass |
| `IMSWITCH_DETECTOR` | first reported | detector to snap from |
| `IMSWITCH_URL` | `http://localhost:8001` | API base, see [`../README.md`](../README.md) |

Also `PI_HOST` and `IMSWITCH_CONTAINER` for the runner itself.

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
- `channel_index` **must be an integer**. The string `'LED'` raises a hard
  `ValueError` on load, and several of the setups shipped on the Pi use it.

## Why the photon test can fail while the software works

A missing brightness difference is a **failure, not a skip** — that is the
point of the test. Causes worth checking, in order of likelihood:

- **The light does not illuminate what the sensor sees.** It has to be in the
  camera's field of view, not merely switched on.
- **Exposure is too short for a dim light**, or the threshold is too tight.
  Read the actual numbers with `--measure` and set `PHOTON_MIN_DELTA` from
  there.
- **The stream has just started and is still drifting.** Two frames taken right
  after `startLiveView` differ by ~1.66 with no light at all, against a
  threshold of 1.5. [`../ledmatrix/`](../ledmatrix/) rejects an unsettled
  baseline with `settled_dark_frame`; this file does not, so a cold stream can
  flip a marginal result here.

## Limits

- Opening the serial port resets the ESP32 via DTR/RTS. Unavoidable on the CP2102.
- The firmware reports internal PWM state, not a measurement.
- `success` is not a uniform code: `/laser_act` answers `success:1`,
  `/ledarr_act` answers `success:0`. Nothing here asserts on it.
