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
