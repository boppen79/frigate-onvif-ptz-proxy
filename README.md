# ONVIF Idle Proxy for Frigate PTZ autotracking

A small ONVIF HTTP proxy for PTZ cameras that keep reporting `MOVING` after
movement has stopped. It estimates movement state from reported PTZ positions
so Frigate can send the next tracking correction.

**Status:** experimental workaround, tested with one Hikvision PTZ camera and
Frigate **0.18.0-rc2**. Other models, firmware and Frigate versions need testing.
It does not add missing field-of-view movement support or make every PTZ camera
compatible with Frigate autotracking.

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

See [snapshot validation](docs/validation.md) for the checks run before publication.

## Observed results and limits

On the test installation, tracking improved after calibration, lower detection
resolution and the separate scheduling fix. A subsequent close walking pass
kept the same tracking ID and continued corrections, but briefly reached the
right image edge. These observations do not isolate each change or guarantee
results on another camera. Physical motor speed remains a hard limit.

When reporting an issue, include camera model/firmware, Frigate version,
redacted settings, timestamps and sanitized timing logs. Do not upload camera
passwords, full private configurations or recordings of people.

## License and attribution

Proxy code: MIT, as identified in the original source. Frigate is a separate
upstream project; the small optional patch and its build process are described
separately. This is a community workaround, not an official Frigate or camera
vendor component. Existing source comments and the integration harness are
currently mostly Swedish; user-facing instructions are in English.
