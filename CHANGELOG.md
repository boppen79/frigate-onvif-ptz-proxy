# Changelog

## 1.5.0 — 2026-10-23

* Proxy: opt-in delayed-start grace, absolute-move idle timing, and confirmed
  completion for known zoom-only targets. Optional camera-specific feedback
  quantization affects expected status only; commands remain unchanged.
* Diagnostics: numeric position samples, late position changes and zoom-target
  completion events, without SOAP/authentication payloads.
* Optional Frigate tracking bundle: includes the existing scheduling fix plus
  movement regression, zero-target zoom recovery, image-coordinate velocity,
  bounded lead, stable-zoom approach protection and larger distant zoom steps.
* Reproducible tracking build from the pinned Frigate base with input/output
  source checks; rollback, camera-specific settings and live-test limits documented.
* Additional fake-camera, movement, zoom and source-drift regression tests.

Default proxy timing and disabled target-confirmation behavior remain
conservative. The full Frigate bundle is a separate opt-in image build;
rebuilding the proxy does not patch or restart Frigate.
