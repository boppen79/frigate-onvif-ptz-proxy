import unittest
from unittest.mock import patch
import onvif_idle_proxy as p

class StartupGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(p.time, 'monotonic', return_value=100.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        for key, value in [('MIN_MOVE_TIME', .15), ('IDLE_AFTER', .10),
                           ('MAX_MOVE_TIME', 8.0), ('STARTUP_GRACE', .35)]:
            patcher = patch.object(p, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.s = p.PTZState('Test')
        self.s.update_position((0., 0., 0.))
        self.s.note_move('RelativeMove')

    def at(self, t, pos):
        self.now.return_value = 100.0 + t
        self.s.update_position(pos)
        return self.s.is_moving()

    def test_delayed_start_remains_active_then_completes(self):
        self.assertTrue(self.at(.151, (0., 0., 0.)))
        self.assertTrue(self.at(.220, (.02, 0., 0.)))
        self.assertTrue(self.s.first_change_seen)
        self.assertFalse(self.at(.330, (.02, 0., 0.)))

    def test_no_op_wait_is_bounded(self):
        self.assertTrue(self.at(.300, (0., 0., 0.)))
        self.assertFalse(self.at(.351, (0., 0., 0.)))

    def test_fast_observed_move_does_not_wait_for_grace(self):
        self.assertTrue(self.at(.030, (.02, 0., 0.)))
        self.assertFalse(self.at(.151, (.02, 0., 0.)))

    def test_stop_overrides_startup_grace(self):
        self.now.return_value = 100.05
        self.s.note_stop()
        self.assertFalse(self.s.is_moving())

    def test_max_timeout_overrides_long_grace(self):
        with patch.object(p, 'STARTUP_GRACE', 20.):
            self.assertFalse(self.at(8.01, (0., 0., 0.)))

    def test_late_movement_is_diagnosed(self):
        self.assertFalse(self.at(.351, (0., 0., 0.)))
        with patch.object(p, 'PTZ_DIAGNOSTICS', True), patch.object(p, 'diagnostic') as out:
            self.at(.400, (.02, 0., 0.))
        self.assertIn('late_position_change', [c.args[1] for c in out.call_args_list])

class AbsoluteStartupTests(StartupGuardTests):
    def test_absolute_move_waits_for_delayed_zoom(self):
        with patch.object(p, 'ABSOLUTE_STARTUP_GRACE', 1.2):
            self.s.note_move('AbsoluteMove')
            self.assertTrue(self.at(.5, (0., 0., 0.)))
            self.assertTrue(self.at(.6, (0., 0., .03)))
            self.assertFalse(self.at(.72, (0., 0., .03)))

    def test_absolute_zoom_survives_sparse_position_updates(self):
        with patch.object(p, 'ABSOLUTE_IDLE_AFTER', .35):
            self.s.note_move('AbsoluteMove')
            self.assertTrue(self.at(.1, (0., 0., .03)))
            self.assertTrue(self.at(.25, (0., 0., .03)))
            self.assertTrue(self.at(.3, (0., 0., .06)))
            self.assertFalse(self.at(.66, (0., 0., .06)))

    def test_relative_no_op_keeps_shorter_deadline(self):
        with patch.object(p, 'ABSOLUTE_STARTUP_GRACE', 1.2):
            self.assertFalse(self.at(.351, (0., 0., 0.)))

if __name__ == '__main__': unittest.main()
