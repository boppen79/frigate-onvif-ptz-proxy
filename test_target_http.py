import asyncio
import aiohttp
from aiohttp import web
import onvif_idle_proxy as p
SOAP='http://www.w3.org/2003/05/soap-envelope'
async def main():
 zoom=.2
 async def camera(request):
  body=await request.read()
  if b'AbsoluteMove' in body:return web.Response(text='<ok/>')
  return web.Response(text=f'<s:Envelope xmlns:s="{SOAP}"><s:Body><GetStatusResponse><PTZStatus><Position><PanTilt x="0" y="0"/><Zoom x="{zoom}"/></Position><MoveStatus><PanTilt>MOVING</PanTilt><Zoom>MOVING</Zoom></MoveStatus></PTZStatus></GetStatusResponse></s:Body></s:Envelope>')
 app=web.Application();app.router.add_post('/onvif',camera);up=web.AppRunner(app,access_log=None);await up.setup();await web.TCPSite(up,'127.0.0.1',0).start()
 proxy=p.CameraProxy('Test','127.0.0.1',0,'127.0.0.1',up.addresses[0][1],'127.0.0.1');runner=await proxy.start()
 url=f'http://127.0.0.1:{runner.addresses[0][1]}/onvif'
 def body(op,contents=''):return f'<s:Envelope xmlns:s="{SOAP}"><s:Body><{op}>{contents}</{op}></s:Body></s:Envelope>'
 try:
  async with aiohttp.ClientSession() as client:
   async def status():
    async with client.post(url,data=body('GetStatus')) as resp:return await resp.text()
   await status()
   async with client.post(url,data=body('AbsoluteMove','<Position><Zoom x="0"/></Position>')) as resp:assert resp.status==200
   assert proxy.state.zoom_target==0.
   await asyncio.sleep(.17);zoom=0.
   assert 'IDLE' not in await status()
   await asyncio.sleep(.17)
   assert 'IDLE' in await status()
   assert proxy.state.last_move_cmd==0.
   async with client.post(url,data=body('AbsoluteMove','<Position><Zoom x="0.12"/></Position>')) as resp:assert resp.status==200
   assert abs(proxy.state.zoom_target-.1)<1e-9
   await asyncio.sleep(.17);zoom=.1
   assert 'IDLE' not in await status()
   await asyncio.sleep(.17)
   assert 'IDLE' in await status()
   print('HTTP end-to-end: zoom-only target0 extracted, forwarded and confirmed before1s idle timeout.')
 finally:
  await runner.cleanup();await proxy.close();await up.cleanup()
if __name__ == '__main__':
 from unittest.mock import patch
 with patch.multiple(p, ZOOM_TARGET_TOLERANCE=.001, ZOOM_TARGET_QUANTUM=1/30,
                     ABSOLUTE_IDLE_AFTER=1., MIN_MOVE_TIME=.15):
  asyncio.run(main())
