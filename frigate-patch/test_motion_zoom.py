import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np
from frigate.track.norfair_tracker import image_velocity
from frigate.ptz.autotrack import PtzAutoTracker
import test_zero_zoom as zero

class VelocityTests(unittest.TestCase):
 def tracker(self, position, velocity, transform=None):
  return NS(estimate_velocity=np.array(velocity,dtype=float),abs_to_rel=transform,get_estimate=lambda absolute:np.array(position,dtype=float))
 def test_static_camera_preserves_velocity(self):
  np.testing.assert_allclose(image_velocity(self.tracker([[0,0],[10,10]],[[2,1],[2,1]])),[[2,1],[2,1]])
 def test_translation_is_not_added_to_velocity(self):
  t=self.tracker([[100,200],[300,400]],[[2,1],[2,1]],lambda p: p+[500,800])
  np.testing.assert_allclose(image_velocity(t),[[2,1],[2,1]])
 def test_zoom_reference_units_are_converted_to_image_units(self):
  t=self.tracker([[100,200],[300,400]],[[20,10],[20,10]],lambda p:p*.1+[500,800])
  np.testing.assert_allclose(image_velocity(t),[[2,1],[2,1]])
 def test_projective_velocity_matches_mapped_endpoints(self):
  def transform(p):return p/(1+p[:,1:2]*.001)
  p=np.array([[100.,200.],[300.,400.]]);v=np.array([[2.,1.],[2.,1.]])
  np.testing.assert_allclose(image_velocity(self.tracker(p,v,transform)),transform(p+v)-transform(p))
 def test_bad_transform_rejected_by_ptz_filter(self):
  t=zero.ZoomTests().make();t.config.cameras['test'].detect=NS(fps=7)
  o=NS(obj_data={'estimate_velocity':np.full((2,2),np.nan)})
  valid,v=t._get_valid_velocity('test',o);self.assertFalse(valid);np.testing.assert_array_equal(v,np.zeros(4))
 def test_corrected_scaled_velocity_is_accepted(self):
  t=zero.ZoomTests().make();t.config.cameras['test'].detect=NS(fps=7)
  v=image_velocity(self.tracker([[100,200],[300,400]],[[200,100],[200,100]],lambda p:p*.05))
  valid,out=t._get_valid_velocity('test',NS(obj_data={'estimate_velocity':v}))
  self.assertTrue(valid);np.testing.assert_allclose(out,[10,5,10,5])
 def test_pan_lead_accounts_for_age_but_is_bounded(self):
  h=zero.ZoomTests();t=h.make();cfg=t.config.cameras['test'];cfg.detect=NS(fps=7);cfg.onvif.autotracking.movement_weights=[1]
  t.tracked_object_metrics['test']['velocity']=np.array([-100.,0,-100.,0]);t.tracked_object_metrics['test']['valid_velocity']=True
  t._predict_movement_time=Mock(return_value=2.);t._get_zoom_amount=Mock(return_value=None);t._enqueue_move=Mock()
  h.obj.obj_data.update(centroid=(640,360),box=(600,300,680,420))
  with patch('frigate.ptz.autotrack.time.time',return_value=100.5):t._autotrack_move_ptz('test',h.obj)
  self.assertAlmostEqual(t._enqueue_move.call_args.args[2],-0.359375)

class GuardTests(unittest.TestCase):
 def setUp(self):
  h=zero.ZoomTests();self.t=h.make(current=.2);self.obj=h.obj;self.obj.obj_data['id']='a'
 def sample(self,time,height,zoom=None):
  if zoom is not None:self.t.ptz_metrics['test'].zoom_level.value=zoom
  self.obj.obj_data.update(frame_time=time,box=(600,200,650,200+height))
  return self.t._absolute_zoom_guard('test',self.obj)
 def approach(self):
  self.sample(100,60);self.sample(100.5,70);self.assertTrue(self.sample(101.1,85)[1])
 def test_short_bbox_growth_does_not_latch(self):
  self.sample(100,85);self.sample(100.5,92);self.assertFalse(self.sample(100.8,95)[1])
 def test_true_sustained_approach_latches(self):self.approach()
 def test_zoom_settling_samples_do_not_latch(self):
  self.sample(100,60);self.sample(101,85,zoom=.4);self.sample(101.5,100);self.assertFalse(self.sample(102,110)[1])
  self.assertFalse(self.sample(102.6,100)[0]);self.sample(103.1,100);self.assertFalse(self.sample(103.7,100)[1])
 def test_approach_survives_zoom_out_and_plateau(self):
  self.approach();self.assertTrue(self.sample(102,40,zoom=0)[1])
  for ti in [104,105,106,107]:self.assertTrue(self.sample(ti,40)[1])
 def test_receding_clears_latch(self):
  self.approach();self.sample(104,85);self.sample(104.6,70);self.assertFalse(self.sample(105.2,55)[1])
 def test_near_person_still_gets_immediate_wide_during_settling(self):
  self.sample(100,60);self.sample(101,100,zoom=.4);self.sample(101.1,300)
  self.assertEqual(self.t._get_zoom_amount('test',self.obj,self.obj.obj_data['box'],0),0.)
 def test_edge_person_still_gets_immediate_wide(self):
  self.obj.obj_data.update(frame_time=100,box=(0,200,100,400))
  self.assertEqual(self.t._get_zoom_amount('test',self.obj,self.obj.obj_data['box'],0),0.)
 def test_new_identity_does_not_inherit_latch(self):
  self.approach();self.obj.obj_data['id']='b';self.assertFalse(self.sample(102,60)[1])
 def test_stable_far_person_can_request_more_zoom(self):
  for ti in [100,100.3,100.6]:self.sample(ti,60)
  self.t._should_zoom_in.return_value=True
  self.assertGreater(self.t._get_zoom_amount('test',self.obj,self.obj.obj_data['box'],0),.2)

if __name__=='__main__':unittest.main(verbosity=2)
