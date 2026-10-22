# ONVIF Idle Proxy for Frigate PTZ autotracking

An ONVIF HTTP workaround for PTZ cameras that **already support the relative
movement Frigate needs, but have broken or missing movement-status reporting**.
These cameras can execute tracking moves, yet Frigate may keep waiting because
`GetStatus` still reports `MOVING` after the motor has stopped. The proxy
estimates movement state from reported PTZ positions so tracking can continue.

**Status:** experimental workaround, tested with a Hikvision **DS-2DE2A404IW-DE3**
and Frigate **0.18.0-rc2**. Other models, firmware and Frigate versions need testing.

## Is this workaround for my camera?

Frigate requires ONVIF field-of-view-relative pan/tilt movement:
`RelativePanTiltTranslationSpace` must include `TranslationSpaceFov`.
Ordinary PTZ control or generic `RelativeMove` support alone is insufficient.
See [Frigate's camera support requirements](https://docs.frigate.video/configuration/autotracking/#checking-onvif-camera-support).

| Camera behavior | Does this proxy address it? |
| --- | --- |
| Required FOV-relative moves work, but `MoveStatus` stays `MOVING` after stopping | Yes: this is the main target |
| Required moves and useful PTZ positions work, but `MoveStatus` is missing | The proxy can insert movement status; camera testing is still needed |
| Camera lacks `RelativeMove` or the required `TranslationSpaceFov` | No: the proxy does not implement or advertise missing movement capabilities |
| Frigate reports “FOV relative movement not supported” | This proxy does not remove that requirement |
| Moves work, but tracking corrections stop despite an IDLE status | Investigate Frigate scheduling; see the separate optional patch below |

In other words, this helps a camera that is nearly compatible because of its
**status reporting**, not one that cannot perform the required movements.
It does not translate relative moves into continuous/absolute movement or
fabricate ONVIF capability responses.

## Tested camera and host

The following identifies the actual test setup, not a compatibility list:

| Component | Tested setup |
| --- | --- |
| Camera | Hikvision **DS-2DE2A404IW-DE3** |
| Firmware | **V5.6.18 build 210428** (reported by ONVIF) |
| Frigate | **0.18.0-rc2**, with the optional scheduling patch for the latest walk test |
| Host | Ubuntu, Intel Core i5-6500T, 16 GB RAM; eight cameras configured |
| Object detector | USB Coral |
| Video decoding | Intel VAAPI with the `i965` driver on this host |
| PTZ detection frames | **1280 × 720 at 7 fps** |
| Recording stream | **2560 × 1440**, retained when detection dimensions changed |
| Tracking | Person tracking, zoom disabled, camera-specific movement calibration |

The camera does not provide reliable native movement-status behavior for this
setup; the proxy works around that limitation. This is not a general claim
that all Hikvision cameras or firmware versions have the same defect.

## How it works

```text
Frigate -- ONVIF control --> proxy --> camera
Frigate <---------------- RTSP ------- camera
```

The proxy forwards ONVIF requests and camera authentication. It rewrites
`GetStatus` movement status using position changes, a minimum movement time,
and an idle threshold. A maximum movement timeout prevents indefinite waiting.
It also rewrites matching service addresses (`XAddrs`) so later requests
continue through the proxy. Optional gain and proportional limits adjust
relative pan/tilt corrections; gains change distance, not motor speed.
Movement commands are sent once: retries could execute a move twice.
Read requests can be retried.

There are **two independent fixes** in this repository:

* The proxy handles incorrect or missing camera movement status.
* An optional [Frigate scheduling patch](frigate-patch/README.md) fixes periodic
  autotracker updates being starved by frequent object event publications in
  the tested release. It is not required to run the proxy.

## Requirements

* Linux Docker host with Docker Compose, network access to the camera's ONVIF
  HTTP port, and a free proxy port. The example uses Linux host networking.
* A camera with useful ONVIF position reporting and the movement capabilities
  Frigate requires. Verify native autotracking requirements first.
* A proxy address reachable from the Frigate container.

## Quick start

```sh
cp .env.example .env
# Edit .env: set your actual camera and proxy-host LAN addresses.
docker compose up -d --build
docker compose logs --tail 50
```

`192.0.2.x` addresses in examples are placeholders and must be replaced.
Point only the camera's ONVIF settings in your existing Frigate configuration
to the proxy. Keep the RTSP stream pointed at the camera:

```yaml
cameras:
  ptz_camera:
    # Keep your existing ffmpeg inputs, detect settings, objects and zones.
    onvif:
      host: 192.0.2.10  # proxy host, replace this
      port: 8238
      user: "{FRIGATE_CAMERA_USER}"
      password: "{FRIGATE_CAMERA_PASSWORD}"
      autotracking:
        enabled: true
        calibrate_on_startup: false
        zooming: disabled
        required_zones:
          - tracking_zone  # must exist in this camera's existing zones
        return_preset: home  # must match your camera's preset name
        timeout: 10
```

This is a configuration fragment, not a complete Frigate configuration.
Supply the `FRIGATE_` environment variables to Frigate, and use your own zone
and preset. Follow your Frigate release's calibration instructions with the
proxy enabled; do not copy another installation's movement weights.

The proxy has no additional access-control layer. Use it on a trusted camera
LAN and restrict its listening port with your host firewall. Camera credentials
and SOAP messages pass through the proxy; do not expose it to the Internet.

## Tuning and troubleshooting

See [configuration and tuning](docs/configuration.md). Start with gain 1.0.
Increasing gain cannot increase motor speed and can cause overshoot. Close
passes require faster angular movement and can still reach the image edge.
Occlusion, stale video, decoder backlog and missing object detections can each
limit tracking independently of ONVIF status.

`/healthz` confirms the proxy HTTP service is responding; it does not prove the
camera is reachable or that tracking works. For short diagnostic sessions set
`PTZ_DIAGNOSTICS=true` and inspect `PTZ_TIMING` JSON records. Diagnostics select
numeric timing/movement fields rather than recording SOAP/authentication.
Turn diagnostics off after testing. Compose bounds container log size.

## Tests

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python test_proxy.py
python -m unittest -v test_diagnostics.py
```

The integration harness starts a fake camera and proxy on local test ports;
it never contacts a real camera. It covers stuck/missing movement status,
forwarding, movement scaling, timeouts, retries, SOAP parsing and address
rewriting. Optional Frigate regression tests run inside the pinned Frigate
image; see the patch README.

The initial snapshot built successfully as both Docker images. Its fake-camera
harness passed 41 assertions, diagnostics passed 3 tests, and the optional
Frigate regression harness passed 5 tests. The example Compose configuration
also parsed successfully. These checks cover software behavior, not untested
camera compatibility.

## Settings and findings from real walking tests

Our best tested baseline so far uses the following proxy overrides. These
values differ from the conservative defaults in `compose.yaml`; they are a
starting point for experiments on this camera, not universal settings:

```yaml
# Merge into the existing onvif-proxy service's environment:
MIN_MOVE_TIME: "0.15"
IDLE_AFTER: "0.10"
MAX_MOVE_TIME: "8.0"
POSITION_EPSILON: "0.0008"
RELATIVE_PAN_GAIN: "1.0"
RELATIVE_TILT_GAIN: "1.0"
RELATIVE_PAN_MAX: "0.85"
RELATIVE_TILT_MAX: "0.50"
```

Detection settings used for that baseline (merge into the existing camera):

```yaml
detect:
  width: 1280
  height: 720
  fps: 7
```

### Detection resolution and latency matter

More pixels do not automatically produce better tracking. Processing large
frames can increase work and delay corrections on a busy host. This setup
originally used 2560 × 1440 detection frames; we reduced them to 1280 × 720
while retaining 7 fps and the full-resolution recording stream. That reduces
the detection frame pixel count by 75%. The USB Coral remained in use.

Before the change, one walking-test window had frame-to-command age up to
**1.94 s**. A later window after resizing reached **0.69 s**, but its median
was not lower and the walks differed. This supports investigating processing
latency; it does **not** establish a controlled speedup from resolution alone.
The separate scheduling fix was still needed afterward.

Changing Frigate's detection dimensions can resize frames from the existing
input; it does not by itself reduce the camera's encoded stream resolution
or guarantee less decoder work. If decoding is the bottleneck, consider a
lower-resolution camera substream for detection while retaining the main
stream for recording. Check that it has adequate image detail, field of view
and timely frames. We have not validated that alternative on this setup.

Similarly, increasing detection fps can add load and backlog. Confirm that
frames are current and processing keeps up before raising fps. A fast Coral
inference time alone does not measure decoding or the complete tracking loop.

### More gain is not more motor speed

A pan gain of 1.5 produced overshoot in earlier tests; 1.0 with camera-specific
calibration was a better baseline. Frigate was already requesting maximum
movement speed, so amplifying translation made corrections larger rather
than making the motor faster. Raising the pan cap from 0.75 to 0.85 alone did
not clearly resolve losing the person; callback scheduling also mattered.
Do not keep increasing gain to compensate for stale frames or missing updates.

Calibrate movement with the proxy in the control path. If gain or movement
limits affect the calibration moves, review those settings and recalibrate as
needed. Never copy the test installation's movement weights to another camera.

### Fixing movement status is only one part of tracking

We observed a case where the proxy correctly returned IDLE, yet Frigate
stopped issuing tracking updates while the person continued moving. The
[separate scheduling patch](frigate-patch/README.md) addresses a reproducible
publication-clock defect in the tested release. An IDLE proxy cannot fix
missing callbacks inside Frigate by itself.

After calibration, resizing and the scheduling fix, a close walking pass kept
the same tracking ID and continued receiving corrections. In a one-minute
window there were 21 relative moves, median frame-to-enqueue age of **282 ms**,
and median command HTTP response time of **17 ms**. Those metrics describe
that window, not a guaranteed tracking rate or end-to-end latency. One camera
HTTP response still took **670 ms**.

At the closest point the person briefly reached the right image edge, and
three pan corrections reached the 0.85 cap. There is still limited tracking
margin. Close lateral movement requires a higher angular tracking speed;
physical motor speed remains a limit. Occlusion behind a pillar or vegetation
can also interrupt visibility even when the control loop works correctly.

Test similar walks at the same distance, in both directions, changing one
setting at a time. Record lost tracks, edge clipping, overshoot and timing
rather than judging smoothness alone. The observations above do not isolate
every change or guarantee results on another camera.

When reporting an issue, include camera model/firmware, Frigate version,
redacted settings, timestamps and sanitized timing logs. Do not upload camera
passwords, full private configurations or recordings of people.

## License and attribution

Proxy code: MIT, as identified in the original source. Frigate is a separate
upstream project; the small optional patch and its build process are described
separately. This is a community workaround, not an official Frigate or camera
vendor component. Existing source comments and the integration harness are
currently mostly Swedish; user-facing instructions are in English.
