# Validation of the initial public snapshot

Validated locally on 2026-10-22, without replacing the running server services.

* Proxy Docker image built successfully from the included Dockerfile and requirements.
* Fake-camera integration harness: 41 assertions passed with that image.
* Diagnostics: 3 unit tests passed with that image.
* Frigate derived image built successfully; upstream source SHA-256 matched.
* Actual Frigate object-update regression harness: 5 tests passed in the derived image.
* Compose parsed successfully with the supplied example environment file.
* Publication contents were checked for local addresses, host paths and known private credentials.

No camera videos, database files, deployment backups or real environment files
are included. Tests establish the covered software behavior; they do not
establish compatibility with untested camera models or firmware.
