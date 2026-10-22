import io
import json
import logging
import unittest
from unittest.mock import patch

import onvif_idle_proxy as proxy


class DiagnosticsTests(unittest.TestCase):
    def test_only_finite_translation_values_are_extracted(self):
        xml = b'''<Envelope><Password>private-password</Password>
        <RelativeMove><Translation><PanTilt x="0.85" y="-0.25"/>
        </Translation></RelativeMove></Envelope>'''
        values = proxy.translation_values(xml)
        self.assertEqual(values, {"pan": 0.85, "tilt": -0.25})
        self.assertNotIn("private-password", json.dumps(values))
        self.assertEqual(proxy.translation_values(xml.replace(b'0.85', b'nan')), {})
        self.assertEqual(proxy.translation_values(b'<broken'), {})

    def test_status_timing_is_aggregated_and_rate_limited(self):
        camera = proxy.CameraProxy("Test", "127.0.0.1", 1, "127.0.0.1", 2, "localhost")
        camera.last_status_diagnostic = 100.0
        with patch.object(proxy, 'PTZ_DIAGNOSTICS', True), \
             patch.object(proxy.time, 'monotonic', side_effect=[100.2, 101.2]), \
             patch.object(proxy, 'diagnostic') as output:
            camera._timing('GetStatus', 4, 100.1, 200, 1)
            output.assert_not_called()
            camera._timing('GetStatus', 4, 101.0, 200, 1)
            data = output.call_args.kwargs
            self.assertEqual(data['count'], 2)
            self.assertEqual(data['mean_ms'], 150.0)
            self.assertEqual(data['max_ms'], 200.0)
            self.assertEqual(camera.status_count, 0)

    def test_command_timing_can_be_disabled(self):
        camera = proxy.CameraProxy("Test", "127.0.0.1", 1, "127.0.0.1", 2, "localhost")
        with patch.object(proxy, 'PTZ_DIAGNOSTICS', False), \
             patch.object(proxy, 'diagnostic') as output:
            camera._timing('RelativeMove', 3, 0, 200, 1)
            output.assert_not_called()


if __name__ == '__main__':
    unittest.main()
