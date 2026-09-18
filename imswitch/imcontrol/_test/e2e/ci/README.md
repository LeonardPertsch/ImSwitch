# Hardware-in-the-loop runs

`hil-run.sh` answers one question: **does this ImSwitch image work on real
hardware?** It swaps the image into the running deployment, runs the e2e suite
against it, and puts the rig back the way it was.

Sits alongside:

- [`../run_all.sh`](../run_all.sh) – runs the suite from your machine against
  whatever image the Pi already runs. For working on the tests.
- `hil-run.sh` – runs **on the Pi** and decides which image gets tested. For CI.

## Running it

The script lives on the Pi, next to the rest of the suite:

```bash
# on the Pi
cd ~/ImSwitch/imswitch/imcontrol/_test/e2e
ci/hil-run.sh --image sha-7d3adda --yes
ci/hil-run.sh --image sha-7d3adda --yes --tests "board firmware"
ci/hil-run.sh --image ghcr.io/openuc2/imswitch:sha-7d3adda --yes --out reports
```

`--yes` is mandatory. The suite moves the stage and switches light on, so the
script refuses to start without someone saying so — a cron entry or a stray
call cannot actuate the rig by accident.

| Flag | Meaning |
|---|---|
| `--image` | bare tag (`sha-7d3adda`) or full ref; a bare tag resolves against `ghcr.io/openuc2/imswitch` |
| `--tests` | folders to run, space separated; default is the whole suite |
| `--out` | where the JUnit report lands; default `ci/reports/` |
| `--keep-image` | do not delete the tested image afterwards |
| `--yes` | required; confirms the rig may be actuated |

| Exit code | Meaning |
|---|---|
| 0 | the suite passed |
| 1 | a test failed |
| 2 | the run could not be performed at all |

The 1 / 2 split is what CI reads: a 1 is a finding about the image, a 2 is a
problem with the rig or the run itself, and only one of those should page
anyone.

## What it does, in order

1. **Checks** disk space, that ImSwitch answers, that the UC2 board is
   connected, and that no other run is in progress (`flock`). Each of these
   would otherwise fail halfway through, with the rig already swapped.
2. **Reads the deployment off the container** — compose project, package
   directory, the list of compose files, the image currently pinned. Nothing
   is hardcoded: forklift moves the package to a new stage directory on every
   apply, and the enabled features decide which compose files take part.
3. **Pulls** the image under test.
4. **Swaps** it in with a temporary override file that sets nothing but the
   image. Everything else — the two caddy networks, the device rules for
   serial and cameras, the group memberships, the mounts — comes from the
   deployment's own compose files, so the container under test is the
   deployment, with one line changed.
5. **Waits** until ImSwitch answers, then verifies the container really runs
   the image under test.
6. **Ships the suite** into the container (the image carries no `e2e` folder,
   and a fresh container starts with an empty `/tmp`) and runs pytest with
   `--junitxml`.
7. **Restores** — always, including on failure, `Ctrl+C` and `SIGTERM`. The
   override is deleted, the deployment is brought back up on its own image,
   and the pulled image is removed unless `--keep-image` is given.

## Things worth knowing

**The restore is the important part.** The rig is somebody's microscope. If the
restore fails, the script says so loudly and prints what to run by hand — it
does not exit quietly on a foreign image.

**It never deletes the image the rig runs on.** The card also holds the
rollback image forklift would fall back to; only the image this run pulled is
removed.

**forklift wins on reboot.** `forklift-apply.service` runs at boot and puts
back whatever the pallet pins. That is a safety net, not a problem: a swap
survives exactly as long as the run.

**Two URLs, as everywhere in this suite.** From the Pi, ImSwitch is only
reachable through caddy (`http://localhost:8000/imswitch`) because `:8001` is
not published on the host. Inside the container the tests use
`http://localhost:8001` directly. Override with `HIL_HOST_URL` and
`HIL_CONTAINER_URL`.

## Knobs

| Variable | Default | What for |
|---|---|---|
| `IMSWITCH_CONTAINER` | `imswitch-server-1` | the deployment's container |
| `HIL_HOST_URL` | `http://localhost:8000/imswitch` | ImSwitch as the script sees it |
| `HIL_CONTAINER_URL` | `http://localhost:8001` | ImSwitch as the tests see it |
| `HIL_MIN_FREE_GB` | `15` | refuse to pull below this |
| `HIL_READY_TIMEOUT` | `180` | seconds to wait for ImSwitch after a swap |
| `HIL_LOCK_FILE` | `/tmp/hil-run.lock` | one run per rig |
| `HIL_OVERRIDE_FILE` | `/tmp/hil-override.compose.yml` | the temporary override |

The test knobs themselves (`PHOTON_MIN_DELTA`, `LEDMATRIX_INTENSITY`, …) are
read by the tests from the environment, so exporting them before the call
still works.
