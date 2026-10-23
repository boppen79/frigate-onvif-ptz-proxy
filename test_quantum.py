import unittest
from unittest.mock import patch
import onvif_idle_proxy as p
class QuantumTests(unittest.TestCase):
 def test_observed_camera_floor_mapping(self):
  with patch.object(p,'ZOOM_TARGET_QUANTUM',1/30):
   for requested,actual in [(.05,1/30),(.08,2/30),(.12,.1),(.15,4/30),(.18,5/30),(.22,.2),(.23,.2),(.28,8/30),(.32,.3)]:
    with self.subTest(target=requested):self.assertAlmostEqual(p.reported_zoom_target(requested),actual)
 def test_endpoints_remain_exact(self):
  with patch.object(p,'ZOOM_TARGET_QUANTUM',1/30):
   self.assertEqual(p.reported_zoom_target(0.),0.);self.assertEqual(p.reported_zoom_target(1.),1.)
 def test_disabled_quantization_preserves_generic_target(self):
  with patch.object(p,'ZOOM_TARGET_QUANTUM',0.):self.assertEqual(p.reported_zoom_target(.12),.12)
if __name__=='__main__':unittest.main()
