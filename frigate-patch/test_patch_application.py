"""The build must reject drift before touching any upstream source."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from apply_tracking import apply_bundle


class PatchApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.before = 'one\ntwo\nthree\n'
        self.after = 'one\nchanged\nthree\n'
        self.manifest = self.root / 'manifest.json'
        self.patch = self.root / 'changes.patch'
        hashes = {
            'before': hashlib.sha256(self.before.encode()).hexdigest(),
            'after': hashlib.sha256(self.after.encode()).hexdigest(),
        }
        self.manifest.write_text(json.dumps({'a.py': hashes, 'b.py': hashes}))
        for name in ['a.py', 'b.py']:
            (self.root / name).write_text(self.before)
        self.patch.write_text(''.join(
            f'--- a/{name}\n+++ b/{name}\n@@ -1,3 +1,3 @@\n one\n-two\n+changed\n three\n'
            for name in ['a.py', 'b.py']
        ))

    def test_exact_inputs_produce_verified_outputs(self):
        apply_bundle(self.root, self.manifest, self.patch)
        for name in ['a.py', 'b.py']:
            self.assertEqual((self.root / name).read_text(), self.after)

    def test_second_input_drift_leaves_first_file_untouched(self):
        (self.root / 'b.py').write_text('different upstream\n')
        with self.assertRaisesRegex(ValueError, 'Unexpected upstream source'):
            apply_bundle(self.root, self.manifest, self.patch)
        self.assertEqual((self.root / 'a.py').read_text(), self.before)

    def test_altered_output_is_rejected_without_writes(self):
        self.patch.write_text(self.patch.read_text().replace('+changed', '+incorrect'))
        with self.assertRaisesRegex(ValueError, 'Patched source hash mismatch'):
            apply_bundle(self.root, self.manifest, self.patch)
        self.assertEqual((self.root / 'a.py').read_text(), self.before)

    def test_second_application_is_rejected(self):
        apply_bundle(self.root, self.manifest, self.patch)
        with self.assertRaisesRegex(ValueError, 'Unexpected upstream source'):
            apply_bundle(self.root, self.manifest, self.patch)


if __name__ == '__main__':
    unittest.main()
