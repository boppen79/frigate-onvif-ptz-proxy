#!/usr/bin/env python3
"""
onvif_idle_proxy.py
===================

En transparent ONVIF-proxy som lagar Hikvisions (och Dahuas) trasiga
MoveStatus-rapportering, sa att Frigates autotracking fungerar.

PROBLEMET
---------
Frigate laser ONVIF-parametern MoveStatus for att veta nar PTZ-motorn star
still. Hikvisions firmware aterstaller aldrig varden till IDLE efter en
rorelse. Frigate vantar da i evighet och loggar:

    Camera X is still in ONVIF 'MOVING' status
    X is already performing an action, not moving...

LOSNINGEN
---------
Proxyn sitter mellan Frigate och kameran. Allt vidarebefordras oforandrat
utom svar pa GetStatus, dar MoveStatus skrivs om utifran en egen
tillstandsmaskin ("dead reckoning"):

  * Nar Frigate skickar RelativeMove / AbsoluteMove / ContinuousMove
    markeras kameran som MOVING under minst MIN_MOVE_TIME sekunder.
  * Darefter jamfors PTZ-koordinaterna mellan pollningar. Har de inte
    andrats mer an POSITION_EPSILON under IDLE_AFTER sekunder anses
    kameran sta still och MoveStatus tvingas till IDLE.
  * MAX_MOVE_TIME ar en hard sakerhetsventil: kameran far aldrig
    rapporteras som MOVING langre an sa, oavsett vad firmware sager.
  * Stop-kommandon ger omedelbar IDLE.
  * Saknas MoveStatus helt i svaret injiceras elementet.

Proxyn skriver ocksa om XAddrs-URL:er i svaren, sa att kameran inte
omdirigerar Frigate forbi proxyn till sin egen IP.

ANVANDNING
----------
Konfigureras helt via miljovariabeln CAMERAS, t.ex.:

    CAMERAS=Camera=0.0.0.0:8238->192.0.2.20:80

Flera kameror separeras med kommatecken:

    CAMERAS=Camera=0.0.0.0:8238->192.0.2.20:80,Camera2=0.0.0.0:8230->192.0.2.21:80

Licens: MIT. Ingen garanti - detta ar en workaround for en firmwarebugg.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web
from lxml import etree

VERSION = "1.5.0"
PTZ_DIAGNOSTICS = os.getenv("PTZ_DIAGNOSTICS", "false").lower() == "true"


def diagnostic(camera: str, event: str, **values) -> None:
    """Log only explicitly selected numeric/timing fields, never SOAP or auth."""
    if PTZ_DIAGNOSTICS:
        log.info("PTZ_TIMING %s", json.dumps(
            {"camera": camera, "event": event, "unix": time.time(), **values},
            separators=(",", ":"), allow_nan=False,
        ))


def translation_values(body: bytes) -> dict:
    """Extract finite translation numbers without logging XML credentials."""
    import math
    try:
        root = etree.fromstring(body, etree.XMLParser(
            resolve_entities=False, no_network=True, recover=False))
        for el in root.iter():
            parent = el.getparent()
            if (_local(el.tag) == "PanTilt" and parent is not None
                    and _local(parent.tag) == "Translation"):
                x, y = float(el.get("x", "0")), float(el.get("y", "0"))
                if math.isfinite(x) and math.isfinite(y):
                    return {"pan": x, "tilt": y}
    except (etree.XMLSyntaxError, ValueError, TypeError):
        pass
    return {}

def reported_zoom_target(target: float) -> float:
    """Optional camera-specific floor quantization, established from motor tests."""
    import math
    if ZOOM_TARGET_QUANTUM <= 0 or target in (0.0, 1.0):
        return target
    return max(0.0, min(1.0, math.floor(target / ZOOM_TARGET_QUANTUM + 1e-9) * ZOOM_TARGET_QUANTUM))


def absolute_zoom_target(body: bytes) -> float | None:
    """Recognize only zoom-only AbsoluteMove in normalized position space."""
    import math
    try:
        root = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True))
        ns = root.tag.split("}")[0].lstrip("{")
        if ns not in ("http://www.w3.org/2003/05/soap-envelope", "http://schemas.xmlsoap.org/soap/envelope/"):
            return None
        soap_body = root.find("{" + ns + "}Body")
        if soap_body is None or len(soap_body) != 1 or _local(soap_body[0].tag) != "AbsoluteMove":
            return None
        positions = [x for x in soap_body[0] if _local(x.tag) == "Position"]
        if len(positions) != 1 or len(positions[0]) != 1:
            return None
        zoom = positions[0][0]
        if _local(zoom.tag) != "Zoom":
            return None
        space = zoom.get("space")
        if space and space != "http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace":
            return None
        target = float(zoom.get("x", "nan"))
        return target if math.isfinite(target) and 0 <= target <= 1 else None
    except (ValueError, TypeError, AttributeError, etree.XMLSyntaxError):
        return None


# --------------------------------------------------------------------------
# Installningar (alla overstyrbara via miljovariabler)
# --------------------------------------------------------------------------

#: Minsta tid som kameran rapporteras som MOVING efter ett rorelsekommando.
#: For lagt varde -> Frigate skickar nasta kommando innan motorn hunnit
#: starta, vilket ger hackig rorelse och overskjutning.
MIN_MOVE_TIME = float(os.getenv("MIN_MOVE_TIME", "0.30"))

#: Hur lange koordinaterna maste vara oforandrade innan IDLE rapporteras.
IDLE_AFTER = float(os.getenv("IDLE_AFTER", "0.25"))

# Optional bounded wait for the first observed movement; 0 preserves defaults.
STARTUP_GRACE = float(os.getenv("STARTUP_GRACE", "0.0"))
ABSOLUTE_STARTUP_GRACE = float(os.getenv("ABSOLUTE_STARTUP_GRACE", "0.0"))
ABSOLUTE_IDLE_AFTER = float(os.getenv("ABSOLUTE_IDLE_AFTER", "0.0"))
# Opt-in; half-step tolerance for this camera's ~1/30 normalized zoom feedback.
ZOOM_TARGET_TOLERANCE = float(os.getenv("ZOOM_TARGET_TOLERANCE", "0.0"))
ZOOM_TARGET_QUANTUM = float(os.getenv("ZOOM_TARGET_QUANTUM", "0.0"))
if not 0 <= ZOOM_TARGET_QUANTUM <= 1:
    raise ValueError("ZOOM_TARGET_QUANTUM must be between0 and1")
if STARTUP_GRACE < 0 or ABSOLUTE_STARTUP_GRACE < 0:
    raise ValueError("STARTUP_GRACE must be >= 0")

#: Sakerhetsventil. Rapportera aldrig MOVING langre an detta.
MAX_MOVE_TIME = float(os.getenv("MAX_MOVE_TIME", "8.0"))

#: Hur stor koordinatandring som raknas som verklig rorelse.
#: ONVIF-koordinater ar normaliserade till -1.0 .. 1.0.
POSITION_EPSILON = float(os.getenv("POSITION_EPSILON", "0.0008"))

#: Timeout mot kameran. Hikvision tappar sporadiskt SOAP-anrop.
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "6.0"))

#: Antal omforsok vid tappade lasanrop innan felet skickas vidare.
#: Rorelse- och stoppkommandon skickas alltid bara en gang, eftersom kameran
#: kan ha utfort kommandot aven om sjalva HTTP-svaret tappades.
UPSTREAM_RETRIES = int(os.getenv("UPSTREAM_RETRIES", "2"))

#: Minsta tid mellan sammanfattande INFO-loggar om PTZ-beslut.
#: Tillstandsbyten sparas tills intervallet passerat, sa att den slutliga
#: overgangen till IDLE fortfarande syns utan att varje GetStatus loggas.
DECISION_LOG_INTERVAL = float(os.getenv("DECISION_LOG_INTERVAL", "5.0"))

#: Forstark Frigates relativa pan/tilt-stracka. Frigate skickar redan
#: hastighet 1.0 (max), sa detta gor inte motorn snabbare utan gor varje
#: korrigering storre. 1.0 betyder helt oforandrat beteende.
RELATIVE_PAN_GAIN = float(os.getenv("RELATIVE_PAN_GAIN", "1.0"))
RELATIVE_TILT_GAIN = float(os.getenv("RELATIVE_TILT_GAIN", "1.0"))

# Storsta pan/tilt-steg per RelativeMove. 1.0 behaller tidigare grans.
# Begransa bada axlar med samma faktor sa rorelsens riktning bevaras.
RELATIVE_PAN_MAX = float(os.getenv("RELATIVE_PAN_MAX", "1.0"))
RELATIVE_TILT_MAX = float(os.getenv("RELATIVE_TILT_MAX", "1.0"))
if not (0 < RELATIVE_PAN_MAX <= 1 and 0 < RELATIVE_TILT_MAX <= 1):
    raise ValueError("RELATIVE_PAN_MAX och RELATIVE_TILT_MAX maste vara > 0 och <= 1")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ONVIF-namnrymder
NS_TT = "http://www.onvif.org/ver10/schema"

# SOAP-actions som betyder "motorn ska borja rora sig"
MOVE_ACTIONS = ("RelativeMove", "AbsoluteMove", "ContinuousMove", "GotoPreset",
                "GotoHomePosition")
STOP_ACTIONS = ("Stop",)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("onvif-idle-proxy")


# --------------------------------------------------------------------------
# Tillstandsmaskin per kamera
# --------------------------------------------------------------------------

@dataclass
class PTZState:
    """Haller reda pa om kameran verkligen ror sig, oberoende av firmware."""

    name: str
    last_move_cmd: float = 0.0          # nar senaste rorelsekommando skickades
    last_stop_cmd: float = 0.0          # nar senaste stopp skickades
    last_position: tuple[float, float, float] | None = None
    last_change: float = 0.0            # nar positionen senast andrades
    last_action: str = ""
    move_commands_since_log: int = 0
    last_logged_state: str = "IDLE"
    last_decision_log: float = 0.0
    pending_state: str | None = None
    pending_reason: str = ""
    command_id: int = 0
    first_change_seen: bool = False
    last_idle_at: float = 0.0
    zoom_target: float | None = None
    zoom_target_since: float | None = None
    zoom_target_samples: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def note_move(self, action: str, detail: str = "") -> None:
        now = time.monotonic()
        self.last_move_cmd = now
        self.last_change = now
        self.last_action = f"{action} ({detail})" if detail else action
        self.move_commands_since_log += 1
        self.command_id += 1
        self.first_change_seen = False
        self.last_idle_at = 0.0
        self.zoom_target = None
        self.zoom_target_since = None
        self.zoom_target_samples = 0
        log.debug("[%s] rorelsekommando registrerat: %s", self.name, action)

    def note_stop(self) -> None:
        self.last_stop_cmd = time.monotonic()
        self.last_move_cmd = 0.0
        log.debug("[%s] stoppkommando registrerat", self.name)
        self._report_decision(False, "Stop mottaget")

    def _position_text(self) -> str:
        if self.last_position is None:
            return ""
        pan, tilt, zoom = self.last_position
        return f" (pan={pan:.4f}, tilt={tilt:.4f}, zoom={zoom:.4f})"

    def _report_decision(self, moving: bool, reason: str) -> bool:
        """Logga tillstandsbyten/sammanfattningar utan att spamma varje poll."""
        now = time.monotonic()
        state = "MOVING" if moving else "IDLE"
        interval = max(0.0, DECISION_LOG_INTERVAL)

        # Kom ihag ett tillstandsbyte tills det far loggas. Om kameran hinner
        # byta tillbaka innan intervallet passerat var det bara ett kort
        # mellansteg och behover inte fylla INFO-loggen.
        if state != self.last_logged_state:
            if self.pending_state != state:
                self.pending_state = state
                self.pending_reason = reason
        else:
            self.pending_state = None
            self.pending_reason = ""

        transition_due = (
            self.pending_state == state
            and (self.last_decision_log == 0.0
                 or now - self.last_decision_log >= interval)
        )
        summary_due = (
            moving
            and state == self.last_logged_state
            and self.move_commands_since_log > 0
            and now - self.last_decision_log >= interval
        )

        if transition_due or summary_due:
            log_reason = self.pending_reason if transition_due else reason
            command_summary = ""
            if moving and self.move_commands_since_log:
                command_word = (
                    "rorelsekommando"
                    if self.move_commands_since_log == 1
                    else "rorelsekommandon"
                )
                command_summary = (
                    f"; {self.move_commands_since_log} {command_word}"
                    f" sedan senaste logg"
                )
            log.info(
                "[%s] PTZ-beslut: %s - %s%s%s",
                self.name,
                state,
                log_reason,
                command_summary,
                self._position_text(),
            )
            self.last_logged_state = state
            self.last_decision_log = now
            self.pending_state = None
            self.pending_reason = ""
            self.move_commands_since_log = 0

        return moving

    def update_position(self, pos: tuple[float, float, float] | None, zoom_observed: bool = True) -> None:
        if pos is None:
            self.zoom_target_since = None
            self.zoom_target_samples = 0
            return
        now = time.monotonic()
        if self.last_position is None:
            self.last_position = pos
            self.last_change = now
            return
        moved = any(
            abs(a - b) > POSITION_EPSILON
            for a, b in zip(pos, self.last_position)
        )
        if PTZ_DIAGNOSTICS and (self.last_move_cmd or (
                self.last_idle_at and now - self.last_idle_at < 1.0)):
            diagnostic(self.name, "position_sample", command_id=self.command_id,
                       pan=pos[0], tilt=pos[1], zoom=pos[2], changed=moved,
                       active=bool(self.last_move_cmd))
        if moved:
            if (not self.last_move_cmd and self.last_idle_at
                    and now - self.last_idle_at < 1.0):
                diagnostic(self.name, "late_position_change", command_id=self.command_id,
                           since_idle_ms=round((now - self.last_idle_at) * 1000, 2))
            if self.last_move_cmd and not self.first_change_seen:
                diagnostic(self.name, "first_position_change",
                           command_id=self.command_id,
                           elapsed_ms=round((now - self.last_move_cmd) * 1000, 2))
                self.first_change_seen = True
            self.last_change = now
            self.last_position = pos
        if self.zoom_target is not None and ZOOM_TARGET_TOLERANCE > 0:
            if zoom_observed and abs(pos[2] - self.zoom_target) <= ZOOM_TARGET_TOLERANCE:
                if self.zoom_target_since is None:
                    self.zoom_target_since = now
                self.zoom_target_samples += 1
            else:
                self.zoom_target_since = None
                self.zoom_target_samples = 0

    def is_moving(self) -> bool:
        """Avgor om kameran ror sig - utan att lita pa firmware."""
        now = time.monotonic()

        # Explicit stopp efter senaste rorelsekommando -> alltid stilla.
        if self.last_stop_cmd > self.last_move_cmd:
            return self._report_decision(False, "Stop mottaget")

        # Inget rorelsekommando alls sedan start -> stilla.
        if self.last_move_cmd == 0.0:
            return self._report_decision(False, "inget aktivt rorelsekommando")

        elapsed = now - self.last_move_cmd

        # Sakerhetsventil: fastna aldrig i MOVING.
        if elapsed > MAX_MOVE_TIME:
            diagnostic(self.name, "forced_idle", command_id=self.command_id,
                       elapsed_ms=round(elapsed * 1000, 2))
            log.warning(
                "[%s] MAX_MOVE_TIME (%.1fs) overskriden - tvingar IDLE",
                self.name, MAX_MOVE_TIME,
            )
            self.last_move_cmd = 0.0
            return self._report_decision(
                False, f"MAX_MOVE_TIME {MAX_MOVE_TIME:.1f}s overskriden"
            )

        # Ge motorn tid att komma igang innan vi litar pa koordinaterna.
        if elapsed < MIN_MOVE_TIME:
            return self._report_decision(
                True,
                f"{self.last_action} mottaget; minimitid {MIN_MOVE_TIME:.2f}s",
            )

        # A known zoom-only target can complete before the conservative idle timer.
        # Require observed movement and repeated target samples spanning 150ms.
        if (self.zoom_target is not None and self.first_change_seen
                and self.zoom_target_since is not None and self.zoom_target_samples >= 2
                and now - self.zoom_target_since >= 0.15):
            diagnostic(self.name, "zoom_target_reached", command_id=self.command_id,
                       target=self.zoom_target, elapsed_ms=round(elapsed * 1000, 2))
            diagnostic(self.name, "idle", command_id=self.command_id,
                       elapsed_ms=round(elapsed * 1000, 2), position_change_seen=True,
                       reason="zoom_target_reached")
            self.last_idle_at = now
            self.last_move_cmd = 0.0
            return self._report_decision(False, "zoommal bekraftat i flera statusprov")

        # An unchanged position before the motor starts is not evidence that
        # a requested move has completed. Keep this wait bounded for no-op moves.
        startup_grace = max(STARTUP_GRACE, ABSOLUTE_STARTUP_GRACE
                            if self.last_action.startswith("AbsoluteMove") else 0.0)
        if not self.first_change_seen and elapsed < startup_grace:
            return self._report_decision(
                True, f"vantar pa forsta positionsandring; startfrist {startup_grace:.2f}s"
            )

        # Koordinaterna har statt still tillrackligt lange -> IDLE.
        unchanged_for = now - self.last_change
        idle_after = max(IDLE_AFTER, ABSOLUTE_IDLE_AFTER
                         if self.last_action.startswith("AbsoluteMove") else 0.0)
        if unchanged_for > idle_after:
            diagnostic(self.name, "idle", command_id=self.command_id,
                       elapsed_ms=round(elapsed * 1000, 2),
                       position_change_seen=self.first_change_seen,
                       unchanged_ms=round(unchanged_for * 1000, 2))
            self.last_idle_at = now
            self.last_move_cmd = 0.0
            return self._report_decision(
                False, f"positionen oforandrad i {unchanged_for:.2f}s"
            )

        return self._report_decision(
            True, f"positionen andras; senaste andring {unchanged_for:.2f}s sedan"
        )


# --------------------------------------------------------------------------
# XML-hantering
# --------------------------------------------------------------------------

def _local(tag) -> str:
    """Returnerar taggnamn utan namnrymd."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def extract_position(root) -> tuple[float, float, float] | None:
    """Plockar ut (pan, tilt, zoom) ur ett PTZStatus-element."""
    pan = tilt = zoom = None
    for el in root.iter():
        name = _local(el.tag)
        if name == "PanTilt" and _local(el.getparent().tag) == "Position":
            try:
                pan = float(el.get("x", "0"))
                tilt = float(el.get("y", "0"))
            except (TypeError, ValueError):
                pass
        elif name == "Zoom" and _local(el.getparent().tag) == "Position":
            try:
                zoom = float(el.get("x", "0"))
            except (TypeError, ValueError):
                pass
    if pan is None and tilt is None and zoom is None:
        return None
    return (pan or 0.0, tilt or 0.0, zoom or 0.0)


def rewrite_move_status(body: bytes, state: PTZState) -> bytes:
    """
    Skriver om MoveStatus i ett GetStatusResponse.
    Returnerar originalet oforandrat om nagot gar fel.
    """
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                 recover=True, huge_tree=False)
        root = etree.fromstring(body, parser=parser)
        if root is None:
            return body
    except etree.XMLSyntaxError:
        log.debug("[%s] kunde inte tolka XML - skickar vidare oforandrat",
                  state.name)
        return body

    ptz_status_nodes = [
        el for el in root.iter() if _local(el.tag) == "PTZStatus"
    ]
    if not ptz_status_nodes:
        return body

    changed = False
    for ptz_status in ptz_status_nodes:
        zoom_nodes = ptz_status.xpath("./*[local-name()='Position']/*[local-name()='Zoom']")
        import math
        try:
            zoom_observed = len(zoom_nodes) == 1 and math.isfinite(float(zoom_nodes[0].get("x", "nan")))
        except (ValueError, TypeError):
            zoom_observed = False
        state.update_position(extract_position(ptz_status), zoom_observed=zoom_observed)
        desired = "MOVING" if state.is_moving() else "IDLE"

        move_status = None
        position = None
        for child in ptz_status:
            cname = _local(child.tag)
            if cname == "MoveStatus":
                move_status = child
            elif cname == "Position":
                position = child

        if move_status is None:
            # Firmware utelamnade MoveStatus helt - injicera det.
            move_status = etree.Element(f"{{{NS_TT}}}MoveStatus")
            insert_at = (list(ptz_status).index(position) + 1) if position is not None else 0
            ptz_status.insert(insert_at, move_status)
            changed = True

        existing = {_local(c.tag): c for c in move_status}
        for axis in ("PanTilt", "Zoom"):
            node = existing.get(axis)
            if node is None:
                node = etree.SubElement(move_status, f"{{{NS_TT}}}{axis}")
                changed = True
            if (node.text or "").strip() != desired:
                log.debug("[%s] MoveStatus/%s: %r -> %s",
                          state.name, axis, node.text, desired)
                node.text = desired
                changed = True

    if not changed:
        return body

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def rewrite_xaddrs(body: bytes, cam_host: str, cam_port: int,
                   pub_host: str, pub_port: int) -> bytes:
    """
    Kameran svarar med absoluta URL:er till sina egna tjanster. Utan
    omskrivning skulle Frigate ga direkt till kameran och forbi proxyn.
    """
    # Match the entire authority: :80 must not match :8080 or a hostname
    # beginning with the camera's IP. An omitted port means port 80 only.
    port = rb"(?::80)?" if cam_port == 80 else re.escape(f":{cam_port}".encode())
    pattern = rb"http://" + re.escape(cam_host.encode()) + port + rb"(?=[/\s<>\"'?#]|$)"
    replacement = f"http://{pub_host}:{pub_port}".encode()
    return re.sub(pattern, lambda match: replacement, body)


def amplify_relative_move(body: bytes) -> tuple[bytes, str]:
    """Forstark Translation/PanTilt i ett ONVIF RelativeMove-kommando.

    ONVIF FOV-translation ar normalt -1..1. Vardena klipps till detta
    intervall efter forstarkning. XML skickas oforandrad om kommandot inte
    kan tolkas eller inte innehaller pan/tilt-translation.
    """
    if (RELATIVE_PAN_GAIN == 1.0 and RELATIVE_TILT_GAIN == 1.0
            and RELATIVE_PAN_MAX == 1.0 and RELATIVE_TILT_MAX == 1.0):
        return body, ""

    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                 recover=False, huge_tree=False)
        root = etree.fromstring(body, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        return body, ""

    relative_moves = [el for el in root.iter()
                      if _local(el.tag) == "RelativeMove"]
    if not relative_moves:
        return body, ""

    for relative_move in relative_moves:
        for el in relative_move.iter():
            parent = el.getparent()
            if (_local(el.tag) != "PanTilt" or parent is None
                    or _local(parent.tag) != "Translation"):
                continue
            try:
                requested_pan = float(el.get("x", "0"))
                requested_tilt = float(el.get("y", "0"))
            except (TypeError, ValueError):
                return body, ""

            forwarded_pan = requested_pan * RELATIVE_PAN_GAIN
            forwarded_tilt = requested_tilt * RELATIVE_TILT_GAIN
            scale = max(1.0, abs(forwarded_pan) / RELATIVE_PAN_MAX,
                        abs(forwarded_tilt) / RELATIVE_TILT_MAX)
            forwarded_pan /= scale
            forwarded_tilt /= scale
            el.set("x", f"{forwarded_pan:.8g}")
            el.set("y", f"{forwarded_tilt:.8g}")
            detail = (
                f"begart pan={requested_pan:.3f}, tilt={requested_tilt:.3f}; "
                f"skickat pan={forwarded_pan:.3f}, tilt={forwarded_tilt:.3f}"
            )
            return (
                etree.tostring(root, xml_declaration=True, encoding="UTF-8"),
                detail,
            )

    return body, ""


# --------------------------------------------------------------------------
# Proxy
# --------------------------------------------------------------------------

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
    "content-encoding",
}


class CameraProxy:
    def __init__(self, name: str, listen_host: str, listen_port: int,
                 cam_host: str, cam_port: int, public_host: str):
        self.name = name
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.cam_host = cam_host
        self.cam_port = cam_port
        self.public_host = public_host
        self.state = PTZState(name=name)
        self.session: aiohttp.ClientSession | None = None
        self.last_status_diagnostic = 0.0
        self.status_count = 0
        self.status_total_ms = 0.0
        self.status_max_ms = 0.0

    def _timing(self, action, command_id, started, status, attempt) -> None:
        if not PTZ_DIAGNOSTICS:
            return
        now = time.monotonic()
        elapsed_ms = (now - started) * 1000
        if action == "GetStatus":
            self.status_count += 1
            self.status_total_ms += elapsed_ms
            self.status_max_ms = max(self.status_max_ms, elapsed_ms)
            if now - self.last_status_diagnostic < 1.0:
                return
            diagnostic(self.name, "status_http_summary", command_id=command_id,
                       count=self.status_count,
                       mean_ms=round(self.status_total_ms / self.status_count, 2),
                       max_ms=round(self.status_max_ms, 2), http_status=status)
            self.last_status_diagnostic = now
            self.status_count = 0
            self.status_total_ms = self.status_max_ms = 0.0
        elif action in MOVE_ACTIONS or action in STOP_ACTIONS:
            diagnostic(self.name, "command_http", command_id=command_id,
                       action=action, elapsed_ms=round(elapsed_ms, 2),
                       http_status=status, attempts=attempt)

    @property
    def upstream(self) -> str:
        return f"http://{self.cam_host}:{self.cam_port}"

    async def start(self) -> web.AppRunner:
        timeout = aiohttp.ClientTimeout(total=UPSTREAM_TIMEOUT)
        self.session = aiohttp.ClientSession(
            timeout=timeout, cookie_jar=aiohttp.DummyCookieJar(),
        )

        app = web.Application(client_max_size=16 * 1024 * 1024)
        # Lokal liveness-kontroll. Den far inte vidarebefordras till kameran:
        # en tom GET mot ONVIF-endpointen kan hanga pa vissa Hikvision-modeller.
        app.router.add_get("/healthz", self.health)
        app.router.add_route("*", "/{tail:.*}", self.handle)

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.listen_host, self.listen_port)
        await site.start()

        log.info("[%s] lyssnar pa %s:%d  ->  %s",
                 self.name, self.listen_host, self.listen_port, self.upstream)
        return runner

    async def health(self, request: web.Request) -> web.Response:
        """Lokal liveness-kontroll som inte belastar kamerans ONVIF-tjanst."""
        return web.Response(text="ok\n", content_type="text/plain")

    async def handle(self, request: web.Request) -> web.Response:
        started = time.monotonic()
        body = await request.read()
        target = f"{self.upstream}{request.rel_url}"

        # Identifiera kommandot, forstark eventuell RelativeMove och registrera
        # exakt vad som skickas innan anropet vidarebefordras.
        action = self._find_action(body)
        move_detail = ""
        requested = translation_values(body) if PTZ_DIAGNOSTICS and action == "RelativeMove" else {}
        if action == "RelativeMove":
            body, move_detail = amplify_relative_move(body)
        self._note_action(action, move_detail)
        if action == "AbsoluteMove" and ZOOM_TARGET_TOLERANCE > 0:
            zoom_target_value = absolute_zoom_target(body)
            if zoom_target_value is not None:
                self.state.zoom_target = reported_zoom_target(zoom_target_value)
                requested = {"zoom": zoom_target_value, "reported_zoom_target": self.state.zoom_target}
        command_id = self.state.command_id
        if action in MOVE_ACTIONS or action in STOP_ACTIONS:
            diagnostic(self.name, "command", command_id=command_id, action=action,
                       requested=requested,
                       forwarded=translation_values(body) if action == "RelativeMove" else {})

        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() != "host"
        }
        headers["Host"] = f"{self.cam_host}:{self.cam_port}"

        # Ett skrivkommando kan ha verkstallts aven om kamerans HTTP-svar
        # tappas. Skicka darfor bara identifierade Get-anrop igen.
        # Only positively identified read operations are safe to repeat.
        # Unknown requests may modify settings even if they are not PTZ moves.
        attempts = 1 + max(0, UPSTREAM_RETRIES) if action and action.startswith("Get") else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self.session.request(
                    request.method, target, headers=headers, data=body or None,
                    allow_redirects=False,
                ) as upstream_resp:
                    resp_body = await upstream_resp.read()
                    resp_headers = {
                        k: v for k, v in upstream_resp.headers.items()
                        if k.lower() not in HOP_BY_HOP
                    }
                    resp_body = self._process_response(resp_body)
                    self._timing(action, command_id, started,
                                 upstream_resp.status, attempt + 1)
                    return web.Response(
                        status=upstream_resp.status,
                        headers=resp_headers,
                        body=resp_body,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    log.debug("[%s] forsok %d misslyckades (%s) - forsoker igen",
                              self.name, attempt + 1, exc)
                    await asyncio.sleep(0.15)

        log.warning(
            "[%s] %s %s misslyckades efter %d forsok: %r",
            self.name,
            request.method,
            request.rel_url,
            attempts,
            last_error,
        )
        self._timing(action, command_id, started, 504, attempts)
        return web.Response(status=504, text="Upstream camera unavailable")

    def _find_action(self, body: bytes) -> str | None:
        if not body:
            return None
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                     recover=False, huge_tree=False)
            root = etree.fromstring(body, parser=parser)
        except (etree.XMLSyntaxError, ValueError):
            return None
        soap_namespaces = (
            "http://schemas.xmlsoap.org/soap/envelope/",
            "http://www.w3.org/2003/05/soap-envelope",
        )
        for namespace in soap_namespaces:
            if root.tag != f"{{{namespace}}}Envelope":
                continue
            soap_body = root.find(f"{{{namespace}}}Body")
            if soap_body is not None:
                for operation in soap_body:
                    if isinstance(operation.tag, str):
                        return _local(operation.tag)
        return None

    def _note_action(self, action: str | None, detail: str = "") -> None:
        if action in STOP_ACTIONS:
            self.state.note_stop()
        elif action in MOVE_ACTIONS:
            self.state.note_move(action, detail)

    def _process_response(self, body: bytes) -> bytes:
        if not body:
            return body

        if b"GetStatusResponse" in body or b"PTZStatus" in body:
            body = rewrite_move_status(body, self.state)

        if b"XAddr" in body or b"http://" in body:
            body = rewrite_xaddrs(
                body, self.cam_host, self.cam_port,
                self.public_host, self.listen_port,
            )
        return body

    async def close(self) -> None:
        if self.session:
            await self.session.close()


# --------------------------------------------------------------------------
# Konfiguration och start
# --------------------------------------------------------------------------

SPEC_RE = re.compile(
    r"^(?P<name>[^=]+)=(?P<lhost>[^:]+):(?P<lport>\d+)->"
    r"(?P<chost>[^:]+):(?P<cport>\d+)$"
)


def parse_cameras(spec: str, public_host: str) -> list[CameraProxy]:
    proxies: list[CameraProxy] = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        m = SPEC_RE.match(part)
        if not m:
            sys.exit(
                f"FEL: ogiltig CAMERAS-post: {part!r}\n"
                "Format: Namn=LYSSNA_IP:PORT->KAMERA_IP:PORT"
            )
        proxies.append(CameraProxy(
            name=m.group("name"),
            listen_host=m.group("lhost"),
            listen_port=int(m.group("lport")),
            cam_host=m.group("chost"),
            cam_port=int(m.group("cport")),
            public_host=public_host,
        ))
    return proxies


async def main() -> None:
    spec = os.getenv("CAMERAS", "").strip()
    if not spec:
        sys.exit(
            "FEL: miljovariabeln CAMERAS saknas.\n"
            "Exempel: CAMERAS=Camera=0.0.0.0:8238->192.0.2.20:80"
        )

    public_host = os.getenv("PUBLIC_HOST", "127.0.0.1")
    proxies = parse_cameras(spec, public_host)

    log.info("ONVIF idle-proxy v%s startar", VERSION)
    log.info("  MIN_MOVE_TIME=%.2fs  IDLE_AFTER=%.2fs  MAX_MOVE_TIME=%.1fs",
             MIN_MOVE_TIME, IDLE_AFTER, MAX_MOVE_TIME)
    log.info("  POSITION_EPSILON=%.5f  PUBLIC_HOST=%s",
             POSITION_EPSILON, public_host)
    log.info("  DECISION_LOG_INTERVAL=%.1fs", DECISION_LOG_INTERVAL)
    log.info("  RELATIVE_PAN_GAIN=%.2f  RELATIVE_TILT_GAIN=%.2f",
             RELATIVE_PAN_GAIN, RELATIVE_TILT_GAIN)
    log.info("  RELATIVE_PAN_MAX=%.2f  RELATIVE_TILT_MAX=%.2f",
             RELATIVE_PAN_MAX, RELATIVE_TILT_MAX)

    runners = [await p.start() for p in proxies]

    stop = asyncio.Event()
    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        for r in runners:
            await r.cleanup()
        for p in proxies:
            await p.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Avslutar.")
