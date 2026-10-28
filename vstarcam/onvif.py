"""Cliente ONVIF mínimo (sin dependencias) para PTZ, media y descubrimiento.

Implementa WS-Security UsernameToken (PasswordDigest) sobre SOAP 1.2 con
fallback automático a SOAP 1.1, que es lo que usan las cámaras tipo
VStarcam / Eye4 / O-KAM cuando se les activa ONVIF.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .util import port_open

SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
WSSE_PW = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
WSSE_B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"

NS = {
    "s": SOAP12,
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsse": WSSE,
    "wsu": WSU,
}

DEVICE_URLS = [
    "http://{host}:{port}/onvif/device_service",
    "http://{host}/onvif/device_service",
    "http://{host}:{port}/onvif/device",
    "http://{host}/onvif/device",
    "http://{host}:8080/onvif/device_service",
    "http://{host}:8000/onvif/device_service",
    "http://{host}:5000/onvif/device_service",
    "http://{host}:8899/onvif/device_service",
    "http://{host}:2020/onvif/device_service",
]


class OnvifError(Exception):
    pass


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _split_url(url: str) -> tuple[str, int]:
    m = re.match(r"(https?)://([^/:]+)(?::(\d+))?", url)
    if not m:
        return "", 80
    host = m.group(2)
    port = int(m.group(3)) if m.group(3) else (443 if m.group(1) == "https" else 80)
    return host, port


def find_all(node, name: str) -> list:
    return [el for el in node.iter() if _localname(el.tag) == name]


def find(node, name: str):
    for el in node.iter():
        if _localname(el.tag) == name:
            return el
    return None


def text_of(node, name: str, default: str = "") -> str:
    el = find(node, name)
    if el is None or el.text is None:
        return default
    return el.text.strip()


def _esc(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class OnvifClient:
    def __init__(
        self,
        host: str,
        username: str = "admin",
        password: str = "",
        port: int = 80,
        device_url: str | None = None,
        timeout: float = 6.0,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.soap_version = 12
        self._explicit_device_url = bool(device_url)
        self.device_url = device_url or f"http://{host}:{port}/onvif/device_service"
        self.media_url: str | None = None
        self.ptz_url: str | None = None
        self.profiles: list[dict] = []
        self.info: dict = {}
        self._time_offset = 0.0
        self._soap_ns = NS

    # ---------------------------------------------------------------- SOAP
    def _envelope(self, body: str, header_extra: str = "") -> str:
        ns = self._soap_ns
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="{ns["s"]}" xmlns:tds="{ns["tds"]}" '
            f'xmlns:trt="{ns["trt"]}" xmlns:tptz="{ns["tptz"]}" '
            f'xmlns:tt="{ns["tt"]}">'
            f"<s:Header>{self._security()}{header_extra}</s:Header>"
            f"<s:Body>{body}</s:Body></s:Envelope>"
        )

    def _security(self) -> str:
        nonce = os.urandom(16)
        now = datetime.now(timezone.utc).timestamp() + self._time_offset
        created = (
            datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()
        ).decode()
        return (
            f'<wsse:Security s:mustUnderstand="1" xmlns:wsse="{WSSE}" xmlns:wsu="{WSU}">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{_esc(self.username)}</wsse:Username>"
            f'<wsse:Password Type="{WSSE_PW}">{digest}</wsse:Password>'
            f'<wsse:Nonce EncodingType="{WSSE_B64}">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
            f"<wsu:Created>{created}</wsu:Created>"
            "</wsse:UsernameToken></wsse:Security>"
        )

    def _post(self, url: str, action: str, body: str) -> bytes:
        payload = self._envelope(body).encode("utf-8")
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        if self.soap_version == 12:
            content_type = f'application/soap+xml; charset=utf-8; action="{action}"'
            headers = {"Content-Type": content_type}
        else:
            headers = {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{action}"',
            }
        headers["Authorization"] = f"Basic {auth}"
        headers["User-Agent"] = "vstarcam-local/1.0"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read() or b""
            fault = self._fault_reason(body_bytes)
            if exc.code == 401:
                raise OnvifError("Credenciales INCORRECTAS (HTTP 401)") from None
            raise OnvifError(fault or f"HTTP {exc.code} en {url}") from None
        except urllib.error.URLError as exc:
            raise OnvifError(f"No se pudo conectar: {exc.reason}") from None
        except (socket.timeout, TimeoutError):
            raise OnvifError("Tiempo de espera agotado") from None

    @staticmethod
    def _fault_reason(data: bytes) -> str:
        if not data:
            return ""
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return ""
        reason = find(root, "Text")
        code = find(root, "Value")
        parts = []
        if code is not None and code.text:
            parts.append(code.text.strip())
        if reason is not None and reason.text:
            parts.append(reason.text.strip())
        return ": ".join(parts)

    def call(self, url: str, action: str, body: str) -> ET.Element:
        """Llama a un servicio ONVIF probando SOAP 1.2 y luego 1.1."""
        last_exc: Exception | None = None
        for version in (12, 11):
            self.soap_version = version
            self._soap_ns = {**NS, "s": SOAP12 if version == 12 else SOAP11}
            try:
                data = self._post(url, action, body)
                if not data:
                    return ET.Element("empty")
                root = ET.fromstring(data)
                if find(root, "Fault") is not None:
                    raise OnvifError(self._fault_reason(data) or "Fault ONVIF")
                return root
            except OnvifError as exc:
                msg = str(exc)
                if "HTTP 401" in msg or "Credenciales" in msg:
                    raise
                last_exc = exc
                # Errores de transporte no mejoran cambiando de versión.
                if "No se pudo conectar" in msg or "Tiempo de espera" in msg:
                    break
        raise OnvifError(str(last_exc) if last_exc else "Error ONVIF desconocido")

    # ------------------------------------------------------------- Device
    def connect(self, candidates: list[str] | None = None) -> "OnvifClient":
        urls: list[str] = []
        if candidates:
            urls.extend(candidates)
        else:
            urls.append(self.device_url)
            urls.extend(u.format(host=self.host, port=self.port) for u in DEVICE_URLS)
        seen: set[str] = set()
        ordered = [u for u in urls if not (u in seen or seen.add(u))]

        last: Exception | None = None
        for url in ordered:
            host, port = _split_url(url)
            if not port_open(host, port, timeout=0.45):
                last = OnvifError(f"{host}:{port} cerrado")
                continue
            try:
                self._sync_clock(url)
                self.device_url = url
                self._init_services()
                return self
            except OnvifError as exc:
                last = exc
                if "Credenciales" in str(exc):
                    # El host habla ONVIF pero las credenciales fallan.
                    self.device_url = url
                    raise
                continue
        raise OnvifError(
            f"Ningún servicio ONVIF en {self.host}. "
            f"¿Está activado ONVIF en la app de la cámara? ({last})"
        )

    def _sync_clock(self, url: str) -> None:
        """Ajusta el reloj al de la cámara (algunas rechazan el digest si hay desfase)."""
        try:
            root = self.call(
                url,
                "http://www.onvif.org/ver10/device/wsdl/GetSystemDateAndTime",
                "<tds:GetSystemDateAndTime/>",
            )
        except OnvifError:
            return
        utc = find(root, "UTCDateTime")
        if utc is None:
            return
        try:
            dt = datetime(
                int(text_of(utc, "Year")),
                int(text_of(utc, "Month")),
                int(text_of(utc, "Day")),
                int(text_of(utc, "Hour")),
                int(text_of(utc, "Minute")),
                int(text_of(utc, "Second")),
                tzinfo=timezone.utc,
            )
        except (ValueError, TypeError):
            return
        offset = dt.timestamp() - time.time()
        if abs(offset) < 86400:
            self._time_offset = offset

    def _init_services(self) -> None:
        try:
            root = self.call(
                self.device_url, "http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation", ""
            )
            self.info = {
                "manufacturer": text_of(root, "Manufacturer"),
                "model": text_of(root, "Model"),
                "firmware": text_of(root, "FirmwareVersion"),
                "serial": text_of(root, "SerialNumber"),
                "hardware": text_of(root, "HardwareId"),
            }
        except OnvifError:
            self.info = {}

        media_url = ptz_url = None
        try:
            root = self.call(
                self.device_url,
                "http://www.onvif.org/ver10/device/wsdl/GetCapabilities",
                "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>",
            )
            media_el = find(root, "Media")
            if media_el is not None:
                xaddr = find(media_el, "XAddr")
                if xaddr is not None and xaddr.text:
                    media_url = xaddr.text.strip()
            ptz_el = find(root, "PTZ")
            if ptz_el is not None:
                xaddr = find(ptz_el, "XAddr")
                if xaddr is not None and xaddr.text:
                    ptz_url = xaddr.text.strip()
        except OnvifError:
            pass

        if not media_url:
            try:
                root = self.call(
                    self.device_url,
                    "http://www.onvif.org/ver10/device/wsdl/GetServices",
                    "<tds:GetServices><tds:IncludeCapability>false</tds:IncludeCapability></tds:GetServices>",
                )
                for svc in find_all(root, "Service"):
                    ns_el = find(svc, "Namespace")
                    addr_el = find(svc, "XAddr")
                    if ns_el is None or addr_el is None or not addr_el.text:
                        continue
                    if "media" in (ns_el.text or "") and not media_url:
                        media_url = addr_el.text.strip()
                    if "ptz" in (ns_el.text or "") and not ptz_url:
                        ptz_url = addr_el.text.strip()
            except OnvifError:
                pass

        # Normaliza el host del XAddr por si la cámara devuelve otra IP,
        # pero conserva el puerto/ruta que ella misma indica.
        def fix(url: str | None, fallback: str) -> str:
            if not url:
                return fallback
            m = re.match(r"(https?)://[^/]+(/.*)$", url)
            if not m:
                return url
            scheme, path = m.group(1), m.group(2)
            port = self.port
            pm = re.match(r"https?://[^/]*?:(\d+)", url)
            if pm:
                port = pm.group(1)
            suffix = "" if str(port) in ("80", "443") and scheme == "http" else f":{port}"
            return f"{scheme}://{self.host}{suffix}{path}"

        self.media_url = fix(media_url, f"http://{self.host}:{self.port}/onvif/media_service")
        self.ptz_url = fix(ptz_url, f"http://{self.host}:{self.port}/onvif/ptz_service")

    # -------------------------------------------------------------- Media
    def get_profiles(self) -> list[dict]:
        root = self.call(
            self.media_url or self.device_url,
            "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
            "<trt:GetProfiles/>",
        )
        profiles = []
        for prof in find_all(root, "Profiles"):
            token = prof.attrib.get("token", "")
            name = text_of(prof, "Name", token)
            enc = find(prof, "VideoEncoderConfiguration")
            width = height = fps = encoding = ""
            if enc is not None:
                res = find(enc, "Resolution")
                if res is not None:
                    width = text_of(res, "Width")
                    height = text_of(res, "Height")
                fps = text_of(enc, "FrameRateLimit")
                encoding = text_of(enc, "Encoding")
            profiles.append(
                {
                    "token": token,
                    "name": name,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "encoding": encoding,
                }
            )
        self.profiles = profiles
        return profiles

    def get_stream_uri(self, profile_token: str) -> str:
        body = (
            "<trt:GetStreamUri><trt:StreamSetup>"
            "<tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            f"</trt:StreamSetup><trt:ProfileToken>{_esc(profile_token)}</trt:ProfileToken>"
            "</trt:GetStreamUri>"
        )
        root = self.call(
            self.media_url or self.device_url,
            "http://www.onvif.org/ver10/media/wsdl/GetStreamUri",
            body,
        )
        uri = text_of(root, "Uri")
        if not uri:
            raise OnvifError("La cámara no devolvió URL RTSP")
        # Algunas cámaras anuncian su IP de fábrica o la de su interfaz P2P:
        # conserva puerto/ruta pero usa el host con el que las hemos encontrado.
        m = re.match(r"(rtsp[s]?)://(?:[^@/]*@)?[^/:]+(:\d+)?(/.*)?$", uri)
        if m:
            scheme = m.group(1)
            port = m.group(2) or ""
            path = m.group(3) or ""
            cred = ""
            if self.username:
                cred = (
                    f"{urllib.parse.quote(self.username, safe='')}:"
                    f"{urllib.parse.quote(self.password, safe='')}@"
                )
            uri = f"{scheme}://{cred}{self.host}{port}{path}"
        return uri

    def get_snapshot_uri(self, profile_token: str) -> str:
        body = (
            f"<trt:GetSnapshotUri><trt:ProfileToken>{_esc(profile_token)}</trt:ProfileToken></trt:GetSnapshotUri>"
        )
        root = self.call(
            self.media_url or self.device_url,
            "http://www.onvif.org/ver10/media/wsdl/GetSnapshotUri",
            body,
        )
        return text_of(root, "Uri")

    # ---------------------------------------------------------------- PTZ
    def _ptz(self, action: str, body: str) -> ET.Element:
        if not self.ptz_url:
            raise OnvifError("La cámara no expone servicio PTZ por ONVIF")
        return self.call(
            self.ptz_url, f"http://www.onvif.org/ver20/ptz/wsdl/{action}", body
        )

    def ptz_move(self, profile: str, pan: float, tilt: float, zoom: float = 0.0) -> None:
        body = (
            "<tptz:ContinuousMove>"
            f"<tptz:ProfileToken>{_esc(profile)}</tptz:ProfileToken>"
            "<tptz:Velocity>"
            f'<tt:PanTilt x="{pan:.3f}" y="{tilt:.3f}"/>'
            f'<tt:Zoom x="{zoom:.3f}"/>'
            "</tptz:Velocity></tptz:ContinuousMove>"
        )
        self._ptz("ContinuousMove", body)

    def ptz_stop(self, profile: str) -> None:
        body = (
            "<tptz:Stop>"
            f"<tptz:ProfileToken>{_esc(profile)}</tptz:ProfileToken>"
            "<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom>"
            "</tptz:Stop>"
        )
        self._ptz("Stop", body)

    def ptz_absolute(self, profile: str, pan: float, tilt: float, zoom: float = 0.0) -> None:
        body = (
            "<tptz:AbsoluteMove>"
            f"<tptz:ProfileToken>{_esc(profile)}</tptz:ProfileToken>"
            "<tptz:Position>"
            f'<tt:PanTilt x="{pan:.4f}" y="{tilt:.4f}"/>'
            f'<tt:Zoom x="{zoom:.4f}"/>'
            "</tptz:Position></tptz:AbsoluteMove>"
        )
        self._ptz("AbsoluteMove", body)

    def ptz_presets(self, profile: str) -> list[dict]:
        body = f"<tptz:GetPresets><tptz:ProfileToken>{_esc(profile)}</tptz:ProfileToken></tptz:GetPresets>"
        root = self._ptz("GetPresets", body)
        out = []
        for preset in find_all(root, "Preset"):
            token = preset.attrib.get("token", "")
            out.append({"token": token, "name": text_of(preset, "Name", token)})
        return out

    def ptz_goto_preset(self, profile: str, preset_token: str) -> None:
        body = (
            "<tptz:GotoPreset>"
            f"<tptz:ProfileToken>{_esc(profile)}</tptz:ProfileToken>"
            f"<tptz:PresetToken>{_esc(preset_token)}</tptz:PresetToken>"
            "</tptz:GotoPreset>"
        )
        self._ptz("GotoPreset", body)

    def ptz_set_preset(self, profile: str, name: str, preset_token: str = "") -> str:
        tok = f"<tptz:PresetToken>{_esc(preset_token)}</tptz:PresetToken>" if preset_token else ""
        body = (
            "<tptz:SetPreset>"
            f"<tptz:ProfileToken>{_esc(profile)}</tptz:ProfileToken>"
            f"{tok}<tptz:PresetName>{_esc(name)}</tptz:PresetName>"
            "</tptz:SetPreset>"
        )
        root = self._ptz("SetPreset", body)
        return text_of(root, "PresetToken", preset_token)

    def ptz_status(self, profile: str) -> dict:
        body = f"<tptz:GetStatus><tptz:ProfileToken>{_esc(profile)}</tptz:ProfileToken></tptz:GetStatus>"
        root = self._ptz("GetStatus", body)
        pos = find(root, "Position")
        out = {"pan": None, "tilt": None, "zoom": None}
        if pos is not None:
            pan_tilt = find(pos, "PanTilt")
            if pan_tilt is not None:
                out["pan"] = pan_tilt.attrib.get("x")
                out["tilt"] = pan_tilt.attrib.get("y")
            zoom = find(pos, "Zoom")
            if zoom is not None:
                out["zoom"] = zoom.attrib.get("x")
        return out


def discover(timeout: float = 3.0) -> list[dict]:
    """Descubrimiento WS-Discovery de cámaras ONVIF en la red local."""
    message_id = f"uuid:{uuid.uuid4()}"
    msg = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<e:Envelope xmlns:e="{SOAP12}" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        f"<e:Header><w:MessageID>{message_id}</w:MessageID>"
        '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
        '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
        "</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>"
        "</d:Probe></e:Body></e:Envelope>"
    ).encode("utf-8")

    found: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.4)
        sock.sendto(msg, ("239.255.255.250", 3702))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, (addr, _port) = sock.recvfrom(65535)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                continue
            addrs = find(root, "XAddrs")
            types = find(root, "Types")
            scopes = find(root, "Scopes")
            urls = (addrs.text or "").split() if addrs is not None and addrs.text else []
            if not urls:
                continue
            ip = addr[0]
            if not ip or ip.startswith("127."):
                # A veces el origen es 0.0.0.0; usa el host del XAddr.
                m = re.match(r"https?://([^:/]+)", urls[0])
                ip = m.group(1) if m else ip
            found[ip] = {
                "ip": ip,
                "urls": urls,
                "types": (types.text or "").strip() if types is not None else "",
                "scopes": (scopes.text or "").strip() if scopes is not None else "",
            }
    finally:
        sock.close()
    return list(found.values())
