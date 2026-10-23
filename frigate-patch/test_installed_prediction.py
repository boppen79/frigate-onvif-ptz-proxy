import unittest
from types import SimpleNamespace as NS
from frigate.ptz.autotrack import PtzAutoTracker
import numpy as np
class InstalledPredictionTests(unittest.TestCase):
    def test_known_timing_model(self):
        obj=NS(intercept={'test':.26},move_coefficients={'test':np.array([.2,.6])})
        for p,t,expected in [(.1,0.,.26),(.3,0.,.38),(-.3,.2,.5),(0.,0.,.2)]:
            with self.subTest(p=p,t=t):
                self.assertAlmostEqual(PtzAutoTracker._predict_movement_time(obj,'test',p,t),expected)
    def test_first_sample_metadata_does_not_rescale_constant(self):
        obj=NS(intercept={'test':.9},move_coefficients={'test':np.array([.2,.6])})
        self.assertAlmostEqual(PtzAutoTracker._predict_movement_time(obj,'test',.5,0.),.5)
if __name__=='__main__': unittest.main(verbosity=2)
