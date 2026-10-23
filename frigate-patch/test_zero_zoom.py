import asyncio,threading,unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,AsyncMock
from frigate.ptz.autotrack import PtzAutoTracker
from frigate.config import ZoomingModeEnum

class ZoomTests(unittest.IsolatedAsyncioTestCase):
    def make(self,mode=ZoomingModeEnum.absolute,current=.066667):
        self.t=PtzAutoTracker.__new__(PtzAutoTracker)
        t=self.t;t.stop_event=threading.Event()
        cfg=NS(frame_shape=(720,1280),onvif=NS(autotracking=NS(zooming=mode,movement_weights=[])))
        t.config=NS(cameras={'test':cfg});t.intercept={'test':None}
        t.ptz_metrics={'test':NS(zoom_level=NS(value=current),max_zoom=NS(value=1.),min_zoom=NS(value=0.),start_time=NS(value=0.),stop_time=NS(value=0.),motor_stopped=threading.Event())}
        t.ptz_metrics['test'].motor_stopped.set()
        t.tracked_object_metrics={'test':{'target_box':.1,'max_target_box':.2}}
        t._should_zoom_in=Mock(return_value=False)
        t._calculate_move_coefficients=Mock()
        t.move_queues={'test':asyncio.Queue()};t.move_queue_locks={'test':asyncio.Lock()}
        t.onvif=NS(loop=NS(call_soon_threadsafe=lambda fn,data:fn(data)),_move_relative=AsyncMock(),_zoom_absolute=AsyncMock())
        self.obj=NS(obj_data={'box':(600,300,660,500),'frame_time':100.})
        return t

    async def consume(self):
        t=self.t;q=t.move_queues['test'];item=q.get_nowait()
        class Once:
            async def get(self):t.stop_event.set();return item
            def empty(self):return True
        t.move_queues['test']=Once()
        await t._process_move_queue('test')

    async def test_zoom_only_zero_reaches_camera(self):
        t=self.make();t._autotrack_move_zoom_only('test',self.obj)
        self.assertEqual(t.move_queues['test'].qsize(),1)
        await self.consume();t.onvif._zoom_absolute.assert_awaited_once_with('test',0.,1)
        t.onvif._move_relative.assert_not_awaited()

    async def test_combined_pan_zero_reaches_camera(self):
        t=self.make();t._enqueue_move('test',100.,.2,0.,0.)
        await self.consume();t.onvif._move_relative.assert_awaited_once_with('test',.2,0.,0,1)
        t.onvif._zoom_absolute.assert_awaited_once_with('test',0.,1)

    async def test_no_zoom_decision_produces_no_zoom_only_command(self):
        t=self.make();t._should_zoom_in.return_value=None
        t._autotrack_move_zoom_only('test',self.obj)
        self.assertTrue(t.move_queues['test'].empty())

    async def test_pan_without_zoom_does_not_zoom_out(self):
        t=self.make();t._enqueue_move('test',100.,.2,0.,None)
        await self.consume();t.onvif._move_relative.assert_awaited_once();t.onvif._zoom_absolute.assert_not_awaited()

    async def test_already_wide_does_not_enqueue_zero_noop(self):
        t=self.make(current=0.);t._enqueue_move('test',100.,0.,0.,0.)
        self.assertTrue(t.move_queues['test'].empty())

    async def test_positive_absolute_target_is_preserved(self):
        t=self.make();t._enqueue_move('test',100.,0.,0.,.2)
        await self.consume();t.onvif._zoom_absolute.assert_awaited_once_with('test',.2,1)

    async def test_absolute_zoom_in_decision_is_preserved(self):
        t=self.make();t._should_zoom_in.return_value=True
        for frame in (99.,99.4,99.8):
            self.obj.obj_data['frame_time']=frame;t._absolute_zoom_guard('test',self.obj)
        self.obj.obj_data['frame_time']=100.
        t._autotrack_move_zoom_only('test',self.obj);await self.consume()
        self.assertAlmostEqual(t.onvif._zoom_absolute.await_args.args[1],.116667)

    async def test_relative_zoom_out_is_not_absolute_zero(self):
        t=self.make(ZoomingModeEnum.relative);t._enqueue_move('test',100.,.2,0.,-.3)
        await self.consume();t.onvif._move_relative.assert_awaited_once_with('test',.2,0.,-.3,1)
        t.onvif._zoom_absolute.assert_not_awaited()

    async def test_relative_zero_is_noop(self):
        t=self.make(ZoomingModeEnum.relative);t._enqueue_move('test',100.,0.,0.,0.)
        self.assertTrue(t.move_queues['test'].empty())

    async def test_disabled_pan_does_not_request_zoom(self):
        t=self.make(ZoomingModeEnum.disabled);t._enqueue_move('test',100.,.2,0.,0.)
        await self.consume();t.onvif._zoom_absolute.assert_not_awaited()
        t.onvif._move_relative.assert_awaited_once()

if __name__=='__main__':unittest.main(verbosity=2)
