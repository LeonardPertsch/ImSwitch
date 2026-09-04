# Laser / LED E2E

An LED wired to the UC2 ESP32's **LASER3 output = GPIO2** (confirmed by blinking
it and watching). Firmware pin map, read from `/laser_get`:

```
LASER1pin: GPIO12    LASER2pin: GPIO4    LASER3pin: GPIO2
```

## `test_laser3_http.py`

Switches the LED through ImSwitch:

```
GET /api/LaserController/setLaserActive?laserName=LED&active=true
  -> LaserController.toggleLaser
  -> ESP32LEDLaserManager.setEnabled       (channel_index == LASERid)
  -> uc2rest -> /dev/ttyUSB0
  -> ESP32   {"task":"/laser_act","LASERid":3,...}
```

Asserts on `getLaserActive` / `getLaserValue` read-back. A fixture switches the
laser back off afterwards.

**What this proves, and what it does not.** `getLaserActive` returns
`self.enabled`, which `setLaserActive` assigned a moment earlier — so a pass
means the request travelled through ImSwitch without error, not that any light
came out. For that, see [`../photon/`](../photon/).

```bash
./run_laser_test.sh
```

Env: `IMSWITCH_URL` (default `http://localhost:8001`), `IMSWITCH_LASER`
(default: first laser in the setup).

## What the setup file needs

The test skips while the active setup has no lasers. It needs an ESP32 and a
laser bound to it — `channel_index` is the LASERid, so the LED on GPIO2 is
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

Two things bite here:

- **ImSwitch reads the setup only at startup.** Editing the file changes nothing
  until `docker restart imswitch-server-1`.
- `channel_index` **must be an integer**. The string `'LED'`, used by several of
  the older setups on the Pi, raises a hard `ValueError` on load.

None of the 11 ESP32 setups already on the Pi fit as they are — they all point at
`COM3` or a macOS device and use `channel_index` 1, 2, 4 or `'LED'`.

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
back, carrying the matching `qid`.

```bash
./run_laser_test.sh --wire
```

It opens `/dev/ttyUSB0` itself, so it only works **while ImSwitch is not
connected to the ESP32** — only one process can hold the port. Once the setup
above is live, this script stops working and the HTTP test starts.

## Limits

- Opening the serial port resets the ESP32 via DTR/RTS. Unavoidable on the CP2102.
- The firmware reports internal PWM state, not a measurement — with the LED
  unplugged everything here would still pass. Only the camera can prove light.
- `success` is not a uniform code: `/laser_act` answers `success:1` on success,
  `/ledarr_act` answers `success:0`. Nothing here asserts on it.
