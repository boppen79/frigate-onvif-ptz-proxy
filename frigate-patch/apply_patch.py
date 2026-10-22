"""Apply the two scheduling changes only to the verified upstream source."""
import hashlib
from pathlib import Path

path = Path('/opt/frigate/frigate/track/tracked_object.py')
source = path.read_bytes()
EXPECTED_SHA256 = 'b384a96e634d666b79972a8d6c8c4f535e4a40f827208911335ff1302882059a'
if hashlib.sha256(source).hexdigest() != EXPECTED_SHA256:
    raise SystemExit('Unexpected upstream source; review this patch for your image version.')
text = source.decode()
text = text.replace(
    '        self.last_published: float = 0\n',
    '        self.last_published: float = 0\n'
    '        # PTZ scheduling must not share the MQTT/event publication clock.\n'
    '        self.last_autotracker_update: float = 0.0\n', 1)
text = text.replace(
    '            # update autotrack at most 3 objects per second\n'
    '            if self.obj_data["frame_time"] - self.previous["frame_time"] >= (1 / 3):\n'
    '                autotracker_update = True\n',
    '            # Event/path publications update self.previous independently.\n'
    '            # Use a dedicated clock and the incoming frame so frequent event\n'
    '            # updates cannot starve PTZ tracking (or disable its rate limit).\n'
    '            if obj_data["frame_time"] - self.last_autotracker_update >= (1 / 3):\n'
    '                autotracker_update = True\n'
    '                self.last_autotracker_update = obj_data["frame_time"]\n', 1)
path.write_text(text)
