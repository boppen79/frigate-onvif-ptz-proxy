"""Exercise Frigate's real TrackedObject.update with frequent event publishing."""
import copy
import unittest
from types import SimpleNamespace as NS

from frigate.track.tracked_object import TrackedObject


def make_object():
    model = NS(colormap={}, all_attribute_logos=[], attributes_map={},
               non_logo_attributes=[])
    camera = NS(name='Veranda', zones={},
                detect=NS(width=1280, height=720, fps=7,
                          stationary=NS(threshold=50)),
                objects=NS(filters={'person': NS(threshold=0.75)}),
                review=NS(alerts=NS(enabled=False), detections=NS(enabled=False)))
    data = dict(id='test-person', label='person', frame_time=1000.0,
                start_time=1000.0, score=0.95, score_history=[0.95] * 10,
                box=(100, 100, 150, 300), centroid=(125, 200), area=10000,
                ratio=0.25, region=(0, 0, 1280, 720), attributes=[],
                motionless_count=0, position_changes=0)
    obj = TrackedObject(model, camera, NS(unit_system='metric'), {}, data)
    obj.false_positive = False
    obj.previous = obj.to_dict()
    return obj


def walk(publish_every_frame):
    obj = make_object()
    calls = []
    for frame in range(1, 36):
        data = copy.deepcopy(obj.obj_data)
        data['frame_time'] = 1000.0 + frame / 7
        # Fast lateral movement produces a path/MQTT update on each frame.
        x = 50 + (frame % 12) * 90
        data['box'] = (x, 100, x + 50, 300)
        data['centroid'] = (x + 25, 200)
        thumb, significant, path, autotrack = obj.update(data['frame_time'], data, False)
        if autotrack or significant:
            calls.append(data['frame_time'])
        if publish_every_frame or significant:
            # Same side effect as ObjectProcessor's update callback.
            obj.previous = obj.to_dict()
    return calls


class AutotrackScheduleTests(unittest.TestCase):
    def test_frequent_event_publications_do_not_starve_tracking(self):
        calls = walk(True)
        self.assertGreaterEqual(len(calls), 10,
                                'Autotracking stopped when event snapshots were published')

    def test_schedule_is_independent_of_event_publications(self):
        self.assertEqual(walk(True), walk(False))

    def test_normal_updates_remain_rate_limited(self):
        calls = walk(False)
        self.assertGreaterEqual(len(calls), 10)
        self.assertLessEqual(len(calls), 16)
        self.assertTrue(all(b - a >= 1 / 3 for a, b in zip(calls, calls[1:])))

    def test_repeated_detection_frame_is_not_scheduled_repeatedly(self):
        obj = make_object()
        data = copy.deepcopy(obj.obj_data)
        data['frame_time'] = 1001.0
        obj.update(1001.0, copy.deepcopy(data), False)
        repeated = [obj.update(1001.0, copy.deepcopy(data), False)[3]
                    for _ in range(5)]
        self.assertFalse(any(repeated))

    def test_false_positive_does_not_request_tracking(self):
        obj = make_object()
        obj.false_positive = True
        obj.score_history = [0.1] * 10
        data = copy.deepcopy(obj.obj_data)
        data.update(frame_time=1001.0, score=0.1)
        self.assertFalse(obj.update(1001.0, data, False)[3])


if __name__ == '__main__':
    unittest.main(verbosity=2)
