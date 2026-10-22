# Optional Frigate autotracker scheduling fix

Tested only against `ghcr.io/blakeblackshear/frigate:0.18.0-rc2`. This is separate
from the ONVIF proxy and is not automatically applied when the proxy starts.

## Defect

`TrackedObject.update` uses the previous event-publication snapshot as the
clock for periodic autotracker updates. Frequent path/event publications can
reset that snapshot before the 1/3-second interval elapses, starving updates.
Infrequent publications also defeat the intended periodic rate limit. The
comparison uses the previous object frame rather than the incoming frame.

The patch introduces a dedicated per-object timestamp using incoming frame
time. Existing significant-change callbacks are retained; the three-per-second
interval applies to periodic updates, not necessarily every possible callback.
MQTT/event publication behavior is unchanged.

The regression harness invokes the actual upstream `TrackedObject.update`.
Four of five tests failed before the patch and all five passed afterward.
Coverage includes frequent publication starvation, cadence independence,
rate limiting, duplicate frames and false positives. Live testing also showed
continued corrections through a close walking pass, with limited edge margin.

## Build and test

From the repository root:

```sh
docker build -t frigate:local-autotrack-clock frigate-patch
docker run --rm --network none --entrypoint python3 \
  -e PYTHONPATH=/opt/frigate \
  -v "$PWD/frigate-patch:/tests:ro" \
  frigate:local-autotrack-clock /tests/test_autotrack_schedule.py
```

The build checks the original source SHA-256 before applying changes and fails
if it differs. No full upstream Frigate source file is bundled here; the image
provides it. `autotrack-clock.patch` is the reviewable diff. The build script
applies the same two changes without installing additional system packages.
The base image and its dependencies retain their upstream licenses.

After testing and backing up your current Compose configuration, change only
your existing Frigate service image to `frigate:local-autotrack-clock` and
recreate that service with your usual Compose/Portainer workflow. Preserve all
existing volumes, devices, environment variables, Coral and decoding settings.
There is no need to copy any settings from the author's server.

## Rollback and upgrades

Restore your previous image reference and recreate the Frigate service. This
patch makes no recording/database schema changes. Local images do not receive
upstream updates automatically. Before upgrading, check the new source, rerun
the regression harness and determine whether the patch is still necessary.
Do not apply it blindly to a different release.
