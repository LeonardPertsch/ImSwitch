# End-to-End Tests

End-to-end tests for ImSwitch that run complete workflows through the full
running system (backend + hardware/virtual devices), rather than isolated
units or single API endpoints.

Sits alongside:

- `../unit/` – unit tests, no server required
- `../api/` – integration tests against a running headless server

## Layout

One folder per hardware component:

| Folder | Hardware | Status on this rig |
|---|---|---|
| [`camera/`](camera/) | RPi camera (IMX477) via `RecordingController` | works — real sensor data |
| [`laser/`](laser/) | LED on the UC2 ESP32, LASER3 / GPIO2 | serial works; HTTP needs a setup change |
| [`photon/`](photon/) | LED on, camera measures the light | needs the LED mapped + a restart, like the HTTP laser test |

Add a folder per component as hardware is added (`positioner/`, `ledmatrix/`, …).

`photon/` is the odd one out: it is not a component but the only test that proves
light physically reached the sensor. Everything else reads back state that
software set one call earlier.

## Running everything

`run_all.sh` ships this folder to the Pi and runs the suite inside the imswitch
container — one SSH connection, one password prompt:

```bash
./run_all.sh                 # everything
./run_all.sh camera          # only camera/
./run_all.sh laser photon    # several folders
```

Every component folder also has its own script:

| Script | What it runs |
|---|---|
| `camera/run_camera_snap.sh` | the snap test; `--curl` for a quick check into `/tmp/snap.png` |
| `laser/run_laser_test.sh` | serial + http tests; `--blink` for a visible blink, `--wire` for the JSON on the wire |
| `photon/run_photon_test.sh` | the photon test; `--measure` to print dark/bright/ratio without asserting |

Override with `PI_HOST`, `IMSWITCH_CONTAINER`, `IMSWITCH_URL`.

Straight pytest works too, if you have it and can reach ImSwitch:

```bash
IMSWITCH_URL=http://192.168.178.124:8000/imswitch \
  python3 -m pytest imswitch/imcontrol/_test/e2e -v
```

All tests **skip rather than fail** when their hardware or ImSwitch is absent, so
this is safe to run anywhere. The one exception is `photon/`: once it does run, a
missing brightness difference is a real failure — that is the point of it.

`laser/test_laser3_serial.py` and everything HTTP-driven are mutually exclusive.
While ImSwitch holds `/dev/ttyUSB0` the serial test skips; if you unmap the ESP32
from the setup, the HTTP laser and photon tests skip instead.

## Rig facts that apply everywhere

- ImSwitch is reachable at `http://192.168.178.124:8000/imswitch` from outside;
  port 8001 is bound only *inside* the imswitch container.
- The ESP32 is a UC2_Feather V2.0 on `/dev/ttyUSB0` at 115200 baud.
- Only one process may hold `/dev/ttyUSB0` — ImSwitch or a test, never both.
- Nothing here sends `/motor_act`. No motor commands, by design.
