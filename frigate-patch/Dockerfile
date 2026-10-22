FROM ghcr.io/blakeblackshear/frigate:0.18.0-rc2
COPY apply_patch.py /tmp/apply_autotrack_patch.py
RUN python3 /tmp/apply_autotrack_patch.py && rm /tmp/apply_autotrack_patch.py
