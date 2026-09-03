# Laser / LED E2E

An LED wired to the UC2 ESP32's **LASER3 output = GPIO2** (confirmed by blinking
it). Firmware pin map, read from `/laser_get`:

```
LASER1pin: GPIO12    LASER2pin: GPIO4    LASER3pin: GPIO2
```

## `test_laser3_serial.py` — works today

Direct serial, no HTTP, no ImSwitch:

1. Opens `/dev/ttyUSB0` at 115200, resets the board, drops the boot log.
2. Sends `{"task":"/laser_act","LASERid":3,"LASERval":500}`.
3. Sends `{"task":"/laser_get"}`, parses the `++ ... --` frame, asserts `LASER3val > 0`.
4. Sends `LASERval:0` and asserts `LASER3val == 0`.
5. Always closes the port (and sets the laser back to 0) in a `finally`.

```bash
# visible 3s blink + read-back, one SSH hop into the container:
ssh pi@192.168.178.124 'docker exec -i imswitch-server-1 python3 -' < test_laser3_serial.py

# as pytest:
python3 -m pytest test_laser3_serial.py -m hardware
```

Skips if pyserial is missing, `/dev/ttyUSB0` is absent, or the port is held by
another process. Override the device with `UC2_PORT`.

## `test_laser3_http.py` — needs a setup change first

Same on/off check, but driven through ImSwitch:

```
GET /api/LaserController/setLaserActive?laserName=LED&active=true
  -> LaserController.toggleLaser
  -> ESP32LEDLaserManager.setEnabled       (channel_index == LASERid)
  -> uc2rest -> /dev/ttyUSB0
  -> ESP32   {"task":"/laser_act","LASERid":3,...}
```

Asserts on `getLaserActive` / `getLaserValue` read-back rather than on log
content — `setLaserActive` does not log anything itself. A fixture turns the
laser back off afterwards.

Env: `IMSWITCH_URL` (default `http://localhost:8000/imswitch`), `IMSWITCH_LASER`
(default: first laser in the setup).

It currently **skips**: the active setup `example_raspberry_pi_camera.json` has
`lasers: []`. To make it run, the setup needs an ESP32 and a laser bound to it —
`channel_index` is the LASERid, so the LED on GPIO2 is `channel_index: 3`:

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

None of the 11 ESP32 setups already on the Pi fit — they all point at `COM3` or
a macOS device and use `channel_index` 1, 2, 4 or the string `'LED'`.

## `show_wire_traffic.py` — diagnostic, not a test

Calls the same `uc2rest` function ImSwitch uses, one layer below the HTTP API,
with `DEBUG=True` so both directions land on stdout. Verified output:

```
[SendingCommands]:{"task": "/laser_act", "LASERid": 3, "LASERval": 500,
                   "LASERdespeckle": 0, "LASERdespecklePeriod": 10, "qid": 1}
[ProcessLines]:++
[ProcessLines]:{"qid":1,"success":1}
[ProcessLines]:--
[ProcessCommands]: {'qid': 1, 'success': 1}
```

`[ProcessLines]` is the proof the command arrived: those are bytes the ESP32 sent
back, with the matching `qid`.

```bash
ssh pi@192.168.178.124 'docker exec -i imswitch-server-1 python3 -' < show_wire_traffic.py
```

## Limits

- The serial and HTTP tests are **mutually exclusive** — only one process can
  hold `/dev/ttyUSB0`. Once ImSwitch owns it, the serial test skips, and vice versa.
- Opening the port resets the ESP32 via DTR/RTS. Unavoidable on the CP2102.
- `LASER_ID = 3` is hard-coded from this rig's wiring.
- The firmware reports internal PWM state, not a measurement — with the LED
  unplugged the assertions would still pass. Only the camera can prove light.
- `success` is not a uniform code: `/laser_act` answers `success:1` on success,
  `/ledarr_act` answers `success:0`. Neither test asserts on it.
