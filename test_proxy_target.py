import unittest
from unittest.mock import patch
import onvif_idle_proxy as p

class TargetTests(unittest.TestCase):
 def setUp(self):
  self.c=patch.object(p.time,'monotonic',return_value=100.);self.now=self.c.start();self.addCleanup(self.c.stop)
  for name,value in [('ZOOM_TARGET_TOLERANCE',.017),('ABSOLUTE_IDLE_AFTER',1.),('ABSOLUTE_STARTUP_GRACE',1.2),('MIN_MOVE_TIME',.15)]:
   c=patch.object(p,name,value);c.start();self.addCleanup(c.stop)
  self.s=p.PTZState('test');self.s.update_position((0.,0.,.366667));self.s.note_move('AbsoluteMove');self.s.zoom_target=0.
 def at(self,t,z,observed=True):
  self.now.return_value=100.+t;self.s.update_position((0.,0.,z),zoom_observed=observed);return self.s.is_moving()
 def test_wide_target_finishes_after_two_confirmations(self):
  self.assertTrue(self.at(.5,.2));self.assertTrue(self.at(.8,0.));self.assertFalse(self.at(.96,0.))
 def test_one_sample_does_not_complete(self):
  self.assertTrue(self.at(.5,0.));self.assertTrue(self.at(.55,0.))
 def test_midway_pause_still_waits(self):
  self.assertTrue(self.at(.5,.3));self.assertTrue(self.at(.9,.3))
 def test_zoom_noise_outside_target_resets_confirmation(self):
  self.assertTrue(self.at(.5,0.));self.assertTrue(self.at(.6,.033333));self.assertTrue(self.at(.7,0.));self.assertTrue(self.at(.8,0.))
 def test_missing_zoom_cannot_confirm_target(self):
  self.assertTrue(self.at(.5,0.));self.assertTrue(self.at(.7,0.,False));self.assertEqual(self.s.zoom_target_samples,0)
 def test_quantized_position_can_complete(self):
  self.s.zoom_target=.38
  self.assertTrue(self.at(.5,.333333));self.assertTrue(self.at(.7,.366667));self.assertFalse(self.at(.86,.366667))
 def test_new_command_resets_target(self):
  self.at(.5,0.);self.s.note_move('RelativeMove');self.assertIsNone(self.s.zoom_target);self.assertEqual(self.s.zoom_target_samples,0)
 def test_no_observed_movement_does_not_complete_early(self):
  self.s.zoom_target=.366667
  self.assertTrue(self.at(.2,.366667));self.assertTrue(self.at(.4,.366667))
 def test_absolute_zoom_parser_is_narrow(self):
  def soap(pos,operation='AbsoluteMove'):
   return ('<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><'+operation+'><Position>'+pos+'</Position></'+operation+'></s:Body></s:Envelope>').encode()
  self.assertEqual(p.absolute_zoom_target(soap('<Zoom x="0"/>')),0.)
  self.assertIsNone(p.absolute_zoom_target(soap('<Zoom x="nan"/>')))
  self.assertIsNone(p.absolute_zoom_target(soap('<PanTilt x="0" y="0"/><Zoom x="0"/>')))
  self.assertIsNone(p.absolute_zoom_target(soap('<Zoom x="0.2" space="unknown"/>')))
  self.assertIsNone(p.absolute_zoom_target(soap('<Zoom x="0.2"/>','RelativeMove')))
if __name__=='__main__':unittest.main(verbosity=2)
