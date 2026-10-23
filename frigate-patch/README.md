# Optional Frigate patches

These patches are separate from the ONVIF proxy. They are not applied by
starting or rebuilding the proxy. Tested only with Frigate **0.18.0-rc2**.

Choose one image:

| Image build | Scope |
| --- | --- |
| `Dockerfile` | Original scheduling-only fix |
| `Dockerfile.tracking` | Complete tested tracking bundle, including scheduling |

Both builds verify original source hashes and fail on upstream drift. The
tracking build also pins the base image digest and verifies patched output
hashes. No complete upstream source files are bundled. The reviewable diffs
are `autotrack-clock.patch` and `tracking.patch`; the base image and its
upstream code retain their original licenses.

## Scheduling defect

`TrackedObject.update` uses the previous event-publication snapshot as the
clock for periodic autotracker updates. Frequent path/event publications can
reset that snapshot before the 1/3-second interval elapses, starving updates.
Infrequent publications can also defeat the intended periodic rate limit.
The comparison uses the previous object frame rather than the incoming frame.

A dedicated per-object timestamp based on incoming frames fixes the periodic
clock. Significant-change callbacks and MQTT/event publication are retained.
The regression harness invokes upstream `TrackedObject.update`: four of five
tests failed before the fix and all five passed afterward.

## Complete tracking bundle

In addition to scheduling, the tracking build contains:

* **Movement timing:** use a constant 1 in the regression prediction's intercept
  column, matching the fitted design matrix.
* **Absolute zoom target zero:** distinguish no request (`None`) from the valid
  full-wide target (`0.0`) throughout enqueueing and execution. Relative zoom
  behavior remains separate.
* **Velocity coordinates:** map absolute Kalman positions before/after one frame
  of velocity through Norfair's current `abs_to_rel` transform, then subtract.
  Translation cancels; zoom and perspective affect the resulting image velocity.
  Static, untransformed cameras retain their original velocity values.
* **Bounded prediction:** add internal frame age, capped at 0.5 seconds, to the
  movement prediction horizon. Cap the total horizon at 0.75 seconds and lead
  displacement at 18% of each frame dimension. Existing velocity rejection
  remains, with explicit rejection of malformed/nonfinite values.
* **Approach protection:** observe size only at a stable zoom level, discard
  trend samples for 1.5 seconds after each observed zoom change, and require
  at least three observations spanning one second with at least 12% fitted
  growth and 5%/second growth rate before latching approach.
* **Wide-angle recovery:** request full wide angle when height reaches 30% of
  the frame, or 15% while approaching or touching an edge. The near/edge
  override remains active while the trend estimator is settling. An ordinary
  zoom-out decision with height at least 20% also requests full wide angle.
* **Distant detail:** larger inward steps for small subjects, tapering as the
  person becomes larger, while retaining Frigate's centering/velocity gates.

These thresholds are experimental constants, not new YAML options. Approach
is inferred from apparent size, not measured distance. The latch survives
zoom changes and plateaus and releases only after confirmed recession or a
new object ID. Standing still after approach does **not** automatically enable
further detail zoom. Image-size changes, occlusion and motion-estimation errors
can still limit behavior. Motor speed and scene geometry remain physical limits.

## Build and test

Run from the repository root. The complete build needs the Frigate base image,
not any image previously built on the author's host:

```sh
docker build -f frigate-patch/Dockerfile.tracking \
  -t frigate:local-tracking-v4 frigate-patch

docker run --rm --network none --entrypoint python3 \
  -e PYTHONPATH=/tests:/opt/frigate \
  -v "$PWD/frigate-patch:/tests:ro" \
  frigate:local-tracking-v4 -m unittest -v \
  test_autotrack_schedule test_installed_prediction \
  test_zero_zoom test_motion_zoom test_patch_application
```

All 37 tests passed for the published build. The suite covers scheduling, regression timing, absolute zero targets, relative
zoom, affine/projective velocity conversion, bounded lead, settling, sustained
approach, immediate wide recovery, and build rejection on source drift.
The patch-application tests verify that a later-file mismatch leaves earlier
sources untouched. These tests do not simulate the camera's complete motion.

To build just the original scheduling fix instead:

```sh
docker build -t frigate:local-autotrack-clock frigate-patch
docker run --rm --network none --entrypoint python3 \
  -e PYTHONPATH=/opt/frigate \
  -v "$PWD/frigate-patch:/tests:ro" \
  frigate:local-autotrack-clock /tests/test_autotrack_schedule.py
```

## Deploy and configure

Back up your current Compose configuration. Change only the image reference
of your **existing Frigate service** to the image you tested, then recreate
that service with your normal Compose/Portainer workflow. Keep volumes,
devices, environment, decoding settings and detector configuration. If both
local Compose and a Portainer stack manage it, keep their image references
consistent so a later redeploy does not restore an older image.

For the complete bundle's absolute-zoom path, this is a fragment to merge into
your existing camera's autotracking settings:

```yaml
onvif:
  autotracking:
    enabled: true
    zooming: absolute
    zoom_factor: 0.35
    calibrate_on_startup: false
```

Retain your existing ONVIF host/port/credentials, zones, tracked labels,
preset and timeout. The value 0.35 is the tested installation's setting,
not a universal recommendation or the camera's zoom multiplier. Follow your
Frigate release's calibration procedure; `calibrate_on_startup: false` assumes
valid calibration already exists. Never copy another camera's movement weights.

The tested installation combined this bundle with the proxy's optional
[startup grace and zoom-target confirmation](../docs/configuration.md#delayed-movement-and-absolute-zoom-proxy-15).
The proxy's quantization values are camera-specific and disabled by default.
The Frigate fixes do not require quantization, but those options affect
command completion timing. Changing pan/tilt gain or limits can invalidate
movement calibration; review and recalibrate where appropriate.

## Live validation and limits

The original clock fix restored continued corrections during close walking
passes. Later log analysis found that rejected velocities gave no useful lead
and that brief bbox growth after zoom could latch approach too early.

The complete bundle passed software tests and a replay of observed sizes/zoom
from the 23 October 11:41 walk: the early approach latch around 11:41:44 was
removed while wide-angle recovery still occurred near the return. Replay used
recorded observations, not a closed-loop motor model. Private recordings,
full configurations and raw local logs are not included in this repository.

A subsequent natural movement sequence showed progressive distant zoom while
the person walked, reaching reported full zoom. Near-camera framing still
included large corrections and lower-body clipping. Some velocities remained
invalid. Different routes prevent a controlled improvement claim. An apparent
premature-IDLE case remains an investigation item, not a proven fix in this
bundle. Daylight tests do not validate nighttime IR/exposure behavior.

After deployment, repeat comparable walks in both directions. Record entry,
turn and near-pass times, edge clipping, occlusion, accepted/rejected velocity
and command timings. Keep the previous image for rollback. Do not infer actual
motor speed from gain, normalized translation limits or HTTP response time.

## Rollback and upgrades

Restore the previous image reference in the configuration that manages your
service and recreate Frigate. These patches make no recording/database schema
changes. Local images do not automatically receive upstream fixes.

Before upgrading Frigate, review the new source, check whether each fix is
still needed and rerun the tests. The build deliberately rejects unreviewed
source changes; do not bypass its hash checks to force another release.
