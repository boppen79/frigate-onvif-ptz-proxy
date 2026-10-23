# Configuration and tuning

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAMERAS` | required | Comma-separated `Name=bind:port->camera:port` entries |
| `PUBLIC_HOST` | `127.0.0.1` | Address Frigate can reach, used in rewritten XAddrs |
| `MIN_MOVE_TIME` | `0.30` | Minimum seconds reported MOVING after a command |
| `IDLE_AFTER` | `0.25` | Seconds without a position change before IDLE |
| `MAX_MOVE_TIME` | `8.0` | Maximum seconds before forced IDLE |
| `POSITION_EPSILON` | `0.0008` | Position change threshold |
| `UPSTREAM_TIMEOUT` | `6.0` | Camera HTTP request timeout in seconds |
| `UPSTREAM_RETRIES` | `2` | Read request retry count; moves/stops are not retried |
| `RELATIVE_PAN_GAIN` | `1.0` | Relative pan distance multiplier |
| `RELATIVE_TILT_GAIN` | `1.0` | Relative tilt distance multiplier |
| `RELATIVE_PAN_MAX` | `1.0` | Maximum absolute relative pan step, >0 and <=1 |
| `RELATIVE_TILT_MAX` | `1.0` | Maximum absolute relative tilt step, >0 and <=1 |
| `PTZ_DIAGNOSTICS` | `false` | Numeric timing diagnostics |
| `DECISION_LOG_INTERVAL` | `5.0` | Minimum seconds between movement-decision summaries |
| `LOG_LEVEL` | `INFO` | Python log level |

When either relative movement limit is exceeded, both axes are scaled by the
same factor to preserve direction. These are normalized ONVIF translation
values, not degrees and not motor speed settings.

A tested installation used minimum move time 0.15 s, idle time 0.10 s, pan cap
0.85, tilt cap 0.50 and both gains 1.0. These are observations rather than
universal recommendations. Use conservative defaults first, calibrate, then
change one variable at a time and compare similar walks in both directions.
Very short idle thresholds can declare a camera idle before it starts moving.
A forced IDLE timeout does not physically stop the motor.

Timing events include command, command HTTP response, first reported position
change and reported idle. Reported position changes are sampled camera status,
not independent motor measurements. Frame-to-enqueue age measured in Frigate
excludes camera exposure/encoding and RTSP buffering, so it is not end-to-end
latency. A fast HTTP response does not prove the video stream is current.

To stop using the workaround, restore Frigate's ONVIF host/port to the camera,
then stop this Compose stack. The example stack has no recording/database
volumes and does not change camera stream settings.

## Delayed movement and absolute zoom (proxy 1.5)

These features are opt-in. Defaults preserve the earlier movement estimator.
They are independent of the optional Frigate tracking bundle.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STARTUP_GRACE` | `0.0` | Bounded seconds to wait for the first observed position change |
| `ABSOLUTE_STARTUP_GRACE` | `0.0` | Additional minimum startup window for `AbsoluteMove` |
| `ABSOLUTE_IDLE_AFTER` | `0.0` | Additional minimum unchanged-position interval for `AbsoluteMove` |
| `ZOOM_TARGET_TOLERANCE` | `0.0` | Opt in to completion based on reported zoom reaching a known target |
| `ZOOM_TARGET_QUANTUM` | `0.0` | Optional floor quantization of the expected zoom feedback; 0 disables it |

Startup grace stops an initially unchanged position from immediately being
interpreted as a completed move. Once movement is observed, normal idle
handling resumes. The maximum move timeout and Stop override the grace.
It does not solve every pause or late motor movement.

Target completion recognizes only **zoom-only AbsoluteMove** requests in the
normalized generic position space. It requires observed movement, at least two
valid zoom observations within tolerance and 150 ms of target confirmation.
Missing zoom feedback breaks confirmation. Mixed pan/tilt/zoom commands and
unknown zoom spaces retain the conservative idle path. No-op targets use the
bounded fallback rather than pretending that movement was observed.

The tested camera reports zoom in approximately 1/30 normalized increments,
rounding requests down. `ZOOM_TARGET_QUANTUM` changes only the expected feedback;
it does **not** rewrite the requested lens position. Do not enable this mapping
for another camera without measuring its behavior. Too broad a tolerance can
report completion early; a wrong quantum may prevent early completion.

The camera-specific experiment used this additional environment fragment:

```yaml
STARTUP_GRACE: "0.35"
ABSOLUTE_STARTUP_GRACE: "1.2"
ABSOLUTE_IDLE_AFTER: "1.0"
ZOOM_TARGET_TOLERANCE: "0.001"
ZOOM_TARGET_QUANTUM: "0.03333333333333333"
```

Merge these into the proxy service only after verifying the camera's feedback.
Use your existing Frigate service for the separate tracking image; see
[the optional patch instructions](../frigate-patch/README.md).

With diagnostics enabled, `position_sample`, `late_position_change` and
`zoom_target_reached` help distinguish delayed start, pauses and target
completion. Samples remain camera-reported positions. Absence of a
`late_position_change` event does not prove the motor stopped: it requires
subsequent status polling to observe it. Diagnostic logs can grow quickly.
