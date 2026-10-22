#!/usr/bin/env python3
"""
Testharness: simulerar en Hikvision-kamera som ALLTID rapporterar MOVING
och verifierar att proxyn skriver om svaret korrekt.
"""
import asyncio
import logging
import os
import sys

os.environ.setdefault("MIN_MOVE_TIME", "0.30")
os.environ.setdefault("IDLE_AFTER", "0.25")
os.environ.setdefault("MAX_MOVE_TIME", "3.0")
os.environ.setdefault("UPSTREAM_TIMEOUT", "0.20")
os.environ.setdefault("UPSTREAM_RETRIES", "2")
os.environ.setdefault("RELATIVE_PAN_GAIN", "1.5")
os.environ.setdefault("RELATIVE_TILT_GAIN", "1.0")
os.environ.setdefault("RELATIVE_PAN_MAX", "1.0")
os.environ.setdefault("RELATIVE_TILT_MAX", "1.0")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import aiohttp
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onvif_idle_proxy as proxy_module  # noqa: E402
from onvif_idle_proxy import (  # noqa: E402
    CameraProxy, PTZState, amplify_relative_move, rewrite_xaddrs,
)

# Position som "kameran" rapporterar. Testet andrar den for att simulera rorelse.
CURRENT_POS = {"x": 0.5, "y": 0.2, "z": 0.0}

STATUS_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
              xmlns:tt="http://www.onvif.org/ver10/schema">
  <env:Body>
    <tptz:GetStatusResponse>
      <tptz:PTZStatus>
        <tt:Position>
          <tt:PanTilt x="{x}" y="{y}"/>
          <tt:Zoom x="{z}"/>
        </tt:Position>
        <tt:MoveStatus>
          <tt:PanTilt>MOVING</tt:PanTilt>
          <tt:Zoom>MOVING</tt:Zoom>
        </tt:MoveStatus>
        <tt:UtcTime>2026-09-09T17:00:00Z</tt:UtcTime>
      </tptz:PTZStatus>
    </tptz:GetStatusResponse>
  </env:Body>
</env:Envelope>"""

# Variant helt utan MoveStatus - proxyn ska injicera elementet.
STATUS_NO_MOVESTATUS = """<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
              xmlns:tt="http://www.onvif.org/ver10/schema">
  <env:Body>
    <tptz:GetStatusResponse>
      <tptz:PTZStatus>
        <tt:Position>
          <tt:PanTilt x="0.5" y="0.2"/>
          <tt:Zoom x="0.0"/>
        </tt:Position>
      </tptz:PTZStatus>
    </tptz:GetStatusResponse>
  </env:Body>
</env:Envelope>"""

CAPS_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
              xmlns:tt="http://www.onvif.org/ver10/schema">
  <env:Body>
    <tds:GetCapabilitiesResponse>
      <tds:Capabilities>
        <tt:Media><tt:XAddr>http://127.0.0.1:19238/onvif/Media</tt:XAddr></tt:Media>
        <tt:PTZ><tt:XAddr>http://127.0.0.1:19238/onvif/PTZ</tt:XAddr></tt:PTZ>
      </tds:Capabilities>
    </tds:GetCapabilitiesResponse>
  </env:Body>
</env:Envelope>"""

MODE = {"no_movestatus": False, "hang_relative_move": False,
        "hang_action": None}
REQUEST_COUNTS = {"total": 0, "relative_move": 0}


async def fake_camera(request):
    body = (await request.read()).decode("utf-8", errors="ignore")
    REQUEST_COUNTS["total"] += 1
    if MODE["hang_action"] and MODE["hang_action"] in body:
        await asyncio.sleep(0.5)
    if "RelativeMove" in body:
        REQUEST_COUNTS["relative_move"] += 1
        if MODE["hang_relative_move"]:
            # Langre an proxyns UPSTREAM_TIMEOUT: simulerar att kameran kan ha
            # tagit emot kommandot men tappar HTTP-svaret.
            await asyncio.sleep(0.5)
    if "GetCapabilities" in body:
        return web.Response(text=CAPS_TMPL, content_type="application/soap+xml")
    if MODE["no_movestatus"]:
        return web.Response(text=STATUS_NO_MOVESTATUS,
                            content_type="application/soap+xml")
    return web.Response(text=STATUS_TMPL.format(**CURRENT_POS),
                        content_type="application/soap+xml")


def soap(action: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">'
        f'<env:Body><tptz:{action}><tptz:ProfileToken>Profile_1'
        f'</tptz:ProfileToken></tptz:{action}></env:Body></env:Envelope>'
    )


def relative_move_soap(pan: float, tilt: float) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
        ' xmlns:tt="http://www.onvif.org/ver10/schema">'
        '<env:Body><tptz:RelativeMove><tptz:ProfileToken>Profile_1'
        '</tptz:ProfileToken><tptz:Translation>'
        f'<tt:PanTilt x="{pan}" y="{tilt}"/>'
        '</tptz:Translation></tptz:RelativeMove></env:Body></env:Envelope>'
    ).encode()


def move_status_of(xml: str) -> str:
    """Plockar ut PanTilt-vardet ur MoveStatus."""
    import re
    m = re.search(r"<[\w\-]*:?MoveStatus>.*?<[\w\-]*:?PanTilt>(\w+)</",
                  xml, re.S)
    return m.group(1) if m else "SAKNAS"


PASS, FAIL = 0, 0


def check(label: str, actual, expected):
    global PASS, FAIL
    ok = actual == expected
    if ok:
        PASS += 1
        print(f"  PASS  {label}: {actual}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}: fick {actual!r}, forvantade {expected!r}")


async def run():
    # Starta falsk kamera pa 19238
    cam_app = web.Application()
    cam_app.router.add_route("*", "/{tail:.*}", fake_camera)
    cam_runner = web.AppRunner(cam_app, access_log=None)
    await cam_runner.setup()
    await web.TCPSite(cam_runner, "127.0.0.1", 19238).start()

    # Starta proxy pa 18238 -> 19238
    proxy = CameraProxy("TestCam", "127.0.0.1", 18238,
                        "127.0.0.1", 19238, "127.0.0.1")
    proxy_runner = await proxy.start()

    url = "http://127.0.0.1:18238/onvif/PTZ"
    async with aiohttp.ClientSession() as s:

        async def get_status() -> str:
            async with s.post(url, data=soap("GetStatus")) as r:
                return await r.text()

        print("\n== Test 1: vila utan rorelsekommando ==")
        print("   Kameran ljuger och sager MOVING. Proxyn ska saga IDLE.")
        check("MoveStatus", move_status_of(await get_status()), "IDLE")

        print("\n== Test 2: direkt efter RelativeMove ==")
        print("   Ska rapportera MOVING (MIN_MOVE_TIME ej passerad).")
        async with s.post(url, data=soap("RelativeMove")):
            pass
        check("MoveStatus", move_status_of(await get_status()), "MOVING")

        print("\n== Test 3: kameran ror sig verkligen ==")
        print("   Koordinater andras -> ska forbli MOVING.")
        for i in range(4):
            await asyncio.sleep(0.15)
            CURRENT_POS["x"] += 0.05
            st = move_status_of(await get_status())
        check("MoveStatus under rorelse", st, "MOVING")

        print("\n== Test 4: rorelsen upphor ==")
        print("   Koordinater frysta -> ska bli IDLE efter IDLE_AFTER.")
        await asyncio.sleep(0.45)
        check("MoveStatus", move_status_of(await get_status()), "IDLE")

        print("\n== Test 5: MAX_MOVE_TIME som sakerhetsventil ==")
        print("   Koordinater andras hela tiden, men ska tvingas IDLE.")
        async with s.post(url, data=soap("RelativeMove")):
            pass
        deadline = asyncio.get_event_loop().time() + 4.0
        st = "MOVING"
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)
            CURRENT_POS["x"] += 0.01
            st = move_status_of(await get_status())
            if st == "IDLE":
                break
        check("MoveStatus efter MAX_MOVE_TIME", st, "IDLE")

        print("\n== Test 6: Stop ger omedelbar IDLE ==")
        async with s.post(url, data=soap("RelativeMove")):
            pass
        async with s.post(url, data=soap("Stop")):
            pass
        check("MoveStatus", move_status_of(await get_status()), "IDLE")

        print("\n== Test 7: firmware utelamnar MoveStatus helt ==")
        print("   Proxyn ska injicera elementet.")
        MODE["no_movestatus"] = True
        check("MoveStatus", move_status_of(await get_status()), "IDLE")
        MODE["no_movestatus"] = False

        print("\n== Test 8: XAddrs skrivs om till proxyn ==")
        async with s.post(url, data=soap("GetCapabilities")) as r:
            caps = await r.text()
        check("XAddr pekar pa proxyport 18238", "127.0.0.1:18238" in caps, True)
        check("XAddr pekar INTE pa kameraport 19238", "127.0.0.1:19238" in caps, False)

        print("\n== Test 9: lokal healthcheck belastar inte kameran ==")
        before = REQUEST_COUNTS["total"]
        async with s.get("http://127.0.0.1:18238/healthz") as r:
            health_body = await r.text()
            check("HTTP-status", r.status, 200)
            check("Svar", health_body.strip(), "ok")
        check("Inga upstream-anrop", REQUEST_COUNTS["total"], before)

        print("\n== Test 10: rorelsekommando forsoks inte om ==")
        MODE["hang_relative_move"] = True
        before = REQUEST_COUNTS["relative_move"]
        async with s.post(url, data=soap("RelativeMove")) as r:
            check("Timeout ger HTTP-status", r.status, 504)
        MODE["hang_relative_move"] = False
        check("RelativeMove skickades en gang",
              REQUEST_COUNTS["relative_move"] - before, 1)

        print("\n== Test 11: PTZ-beslut loggas bara vid relevanta byten ==")

        class CaptureHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.messages = []

            def emit(self, record):
                message = record.getMessage()
                if "PTZ-beslut:" in message:
                    self.messages.append(message)

        handler = CaptureHandler()
        old_level = proxy_module.log.level
        old_propagate = proxy_module.log.propagate
        old_interval = proxy_module.DECISION_LOG_INTERVAL
        proxy_module.log.setLevel(logging.INFO)
        proxy_module.log.propagate = False
        proxy_module.log.addHandler(handler)
        proxy_module.DECISION_LOG_INTERVAL = 0.0
        try:
            state = PTZState("LogCam")
            state.note_move("RelativeMove")
            state.is_moving()
            await asyncio.sleep(0.35)
            state.is_moving()
            state.is_moving()
            state.is_moving()
        finally:
            proxy_module.DECISION_LOG_INTERVAL = old_interval
            proxy_module.log.removeHandler(handler)
            proxy_module.log.setLevel(old_level)
            proxy_module.log.propagate = old_propagate

        check("MOVING visar action",
              any("MOVING" in m and "RelativeMove" in m
                  for m in handler.messages), True)
        check("IDLE visar orsak",
              any("IDLE" in m and "oforandrad" in m
                  for m in handler.messages), True)
        check("Upprepade IDLE-pollningar spammar inte",
              len(handler.messages), 2)

        print("\n== Test 12: RelativeMove-forstarkning ==")
        amplified, detail = amplify_relative_move(relative_move_soap(0.4, -0.3))
        amplified_text = amplified.decode()
        check("Pan forstarks 1.5x", 'x="0.6"' in amplified_text, True)
        check("Tilt ar oforandrad", 'y="-0.3"' in amplified_text, True)
        check("Logg visar begart och skickat",
              "begart pan=0.400" in detail and "skickat pan=0.600" in detail,
              True)

        clipped, _ = amplify_relative_move(relative_move_soap(0.9, 0.2))
        check("Pan klipps vid 1.0", 'x="1"' in clipped.decode(), True)

        print("\n== Test 12b: sma steg med bibehallen riktning ==")
        from lxml import etree
        old_settings = (proxy_module.RELATIVE_PAN_GAIN,
                        proxy_module.RELATIVE_TILT_GAIN,
                        proxy_module.RELATIVE_PAN_MAX,
                        proxy_module.RELATIVE_TILT_MAX)
        try:
            proxy_module.RELATIVE_PAN_GAIN = 0.65
            proxy_module.RELATIVE_TILT_GAIN = 0.65
            proxy_module.RELATIVE_PAN_MAX = 0.20
            proxy_module.RELATIVE_TILT_MAX = 0.15
            for pan, tilt, expected_pan, expected_tilt in (
                (0.1, -0.1, 0.065, -0.065),
                (1.0, -0.5, 0.20, -0.10),
                (-0.5, 1.0, -0.075, 0.15),
                (0.0, 0.0, 0.0, 0.0),
            ):
                result, _ = amplify_relative_move(relative_move_soap(pan, tilt))
                node = etree.fromstring(result).find(
                    ".//{http://www.onvif.org/ver10/schema}PanTilt")
                check(f"Steg {pan},{tilt} pan",
                      round(float(node.get("x")), 6), expected_pan)
                check(f"Steg {pan},{tilt} tilt",
                      round(float(node.get("y")), 6), expected_tilt)
            # Limits must still apply when gains are both 1.0.
            proxy_module.RELATIVE_PAN_GAIN = 1.0
            proxy_module.RELATIVE_TILT_GAIN = 1.0
            result, _ = amplify_relative_move(relative_move_soap(1.0, 0.0))
            node = etree.fromstring(result).find(
                ".//{http://www.onvif.org/ver10/schema}PanTilt")
            check("Tak utan forstarkning", float(node.get("x")), 0.20)
        finally:
            (proxy_module.RELATIVE_PAN_GAIN,
             proxy_module.RELATIVE_TILT_GAIN,
             proxy_module.RELATIVE_PAN_MAX,
             proxy_module.RELATIVE_TILT_MAX) = old_settings

        print("\n== Test 13: bara lasanrop forsoks om ==")
        for action, expected in (("SetConfiguration", 1), ("GetStatus", 3)):
            MODE["hang_action"] = action
            before = REQUEST_COUNTS["total"]
            async with s.post(url, data=soap(action)) as r:
                check(f"{action} timeout", r.status, 504)
            check(f"{action} antal forsok",
                  REQUEST_COUNTS["total"] - before, expected)
        MODE["hang_action"] = None

        print("\n== Test 14: SOAP-action hamtas bara fran Body ==")
        with_header = soap("GetStatus").replace(
            "<env:Body>",
            "<env:Header><tptz:Stop/></env:Header><env:Body>",
        )
        check("Header paverkar inte action",
              proxy._find_action(with_header.encode()), "GetStatus")
        check("Felaktig XML ar okand action",
              proxy._find_action(b"<RelativeMove"), None)

        print("\n== Test 15: XAddr matchar hela adressen ==")
        for original, expected in (
            ("http://camera:80/onvif", "http://proxy:18238/onvif"),
            ("http://camera/onvif", "http://proxy:18238/onvif"),
            ("http://camera:8080/onvif", "http://camera:8080/onvif"),
            ("http://camera.example/onvif", "http://camera.example/onvif"),
        ):
            check(original, rewrite_xaddrs(original.encode(), "camera", 80,
                                          "proxy", 18238).decode(), expected)
        check("Utelamnad port ar bara port 80",
              rewrite_xaddrs(b"http://camera/onvif", "camera", 8080,
                             "proxy", 18238), b"http://camera/onvif")

    await proxy_runner.cleanup()
    await proxy.close()
    await cam_runner.cleanup()

    print("\n" + "=" * 60)
    print(f"  RESULTAT: {PASS} godkanda, {FAIL} misslyckade")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
