"""Descubrimiento de cámaras en la red local y detección de su stream."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import socket
import time
import urllib.parse

from . import ffmpeg, onvif
from .util import local_ip, port_open

logger = logging.getLogger("vstarcam.discovery")

COMMON_PORTS = [554, 80, 8080, 8000, 81, 88, 8899, 5000, 34567, 2020, 8001, 8002, 9000]
RTSP_PATHS = [
    "/onvif1",
    "/onvif2",
    "/live/ch00_0",
    "/live/ch01_0",
    "/Streaming/Channels/101",
    "/Streaming/Channels/102",
    "/h264",
    "/mpeg4",
    "/11",
    "/12",
    "/stream1",
    "/stream2",
    "/live/main",
    "/av_stream/ch0",
    "/cam/realmonitor?channel=1&subtype=0",
    "/user=admin&password=&channel=1&stream=0.sdp?real_stream",
]


def _local_subnets() -> list[ipaddress.IPv4Network]:
    ip = ipaddress.ip_address(local_ip())
    nets = [ipaddress.ip_network(f"{ip}/24", strict=False)]
    return nets


def _arp_table() -> dict[str, str]:
    table: dict[str, str] = {}
    try:
        with open("/proc/net/arp", "r", encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split()
                if len(parts) >= 4 and parts[3] != "00:00:00:00:00:00":
                    table[parts[0]] = parts[3]
    except OSError:
        pass
    return table


def _probe_host(ip: str) -> tuple[str, list[int]] | None:
    ports = [p for p in COMMON_PORTS if port_open(ip, p, timeout=0.30)]
    # Solo consideramos "cámara" si tiene RTSP o un puerto web típico.
    if 554 in ports or any(p in ports for p in (80, 8080, 8000, 81, 88, 8899, 5000, 34567, 2020)):
        return ip, ports
    return None


def scan_network(deep: bool = True, timeout: float = 8.0) -> list[dict]:
    """Escanea la /24 local en busca de dispositivos con puertos de cámara."""
    arp = _arp_table()
    hosts: set[str] = set(arp.keys())
    for net in _local_subnets():
        hosts.update(str(h) for h in net.hosts())
    hosts.discard(local_ip())

    started = time.monotonic()
    results: list[tuple[str, list[int]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        futures = {pool.submit(_probe_host, ip): ip for ip in hosts}
        for fut in concurrent.futures.as_completed(futures, timeout=timeout + 30):
            try:
                res = fut.result()
            except Exception:  # noqa: BLE001 - un host caído no debe romper el escaneo
                continue
            if res:
                results.append(res)
            if time.monotonic() - started > timeout + 25:
                break

    devices = []
    for ip, ports in sorted(results, key=lambda r: tuple(int(x) for x in r[0].split("."))):
        devices.append(
            {
                "ip": ip,
                "ports": ports,
                "mac": arp.get(ip, ""),
                "rtsp_port": 554 if 554 in ports else None,
            }
        )
    logger.info("Escaneo: %s dispositivos con puertos de cámara", len(devices))
    return devices


def ws_discover(timeout: float = 3.0) -> list[dict]:
    try:
        return onvif.discover(timeout=timeout)
    except OSError as exc:
        logger.warning("WS-Discovery falló: %s", exc)
        return []


def merged_scan(timeout: float = 8.0, username: str = "admin", password: str = "") -> dict:
    """Escaneo de red + WS-Discovery + intento ONVIF con las credenciales dadas."""
    net = scan_network(timeout=timeout)
    ws = ws_discover()
    by_ip: dict[str, dict] = {d["ip"]: d for d in net}

    for item in ws:
        dev = by_ip.setdefault(
            item["ip"],
            {"ip": item["ip"], "ports": [], "mac": "", "rtsp_port": None},
        )
        dev["onvif_urls"] = item["urls"]
        dev["scopes"] = item.get("scopes", "")
        if not dev.get("ports"):
            for port in (80, 8080, 8000, 8899):
                if port_open(item["ip"], port):
                    dev["ports"].append(port)

    def enrich(dev: dict) -> dict:
        if not username and not password:
            return dev
        try:
            res = detect(dev["ip"], username, password, timeout=5.0)
            dev.update(
                {
                    "onvif": res.get("onvif", False),
                    "info": res.get("info", {}),
                    "rtsp": res.get("rtsp_url", ""),
                    "profiles": res.get("profiles", []),
                }
            )
        except Exception as exc:  # noqa: BLE001
            dev["onvif_error"] = str(exc)
        return dev

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        devices = list(pool.map(enrich, by_ip.values()))

    return {"devices": devices, "subnet": str(_local_subnets()[0])}


def _candidate_onvif_urls(ip: str, port: int) -> list[str]:
    urls = [f"http://{ip}:{port}/onvif/device_service"]
    for extra in (80, 8080, 8000, 8899, 5000):
        if extra != port:
            urls.append(f"http://{ip}:{extra}/onvif/device_service")
    return urls


def detect(ip: str, username: str = "admin", password: str = "", port: int = 80,
           timeout: float = 6.0) -> dict:
    """Intenta hablar ONVIF y, si se puede, obtiene la URL RTSP real."""
    result: dict = {
        "ip": ip,
        "onvif": False,
        "info": {},
        "profiles": [],
        "rtsp_url": "",
        "snapshot_url": "",
        "ptz": False,
        "error": "",
    }
    client = onvif.OnvifClient(ip, username, password, port=port, timeout=timeout)
    try:
        client.connect(candidates=_candidate_onvif_urls(ip, port))
        result["onvif"] = True
        result["info"] = client.info
        result["ptz"] = bool(client.ptz_url and "/ptz" in client.ptz_url)
        profiles = client.get_profiles()
        result["profiles"] = profiles
        if profiles:
            token = profiles[0]["token"]
            try:
                result["rtsp_url"] = client.get_stream_uri(token)
            except onvif.OnvifError as exc:
                result["error"] = f"ONVIF sin stream: {exc}"
            try:
                result["snapshot_url"] = client.get_snapshot_uri(token)
            except onvif.OnvifError:
                pass
            result["profile_token"] = token
            result["ptz_profile_token"] = token
    except onvif.OnvifError as exc:
        result["error"] = str(exc)

    if not result["rtsp_url"]:
        guessed = guess_rtsp(ip, username, password)
        if guessed:
            result["rtsp_url"] = guessed["url"]
            result["rtsp_probe"] = guessed
    return result


def rtsp_candidates(ip: str, username: str, password: str, port: int = 554) -> list[str]:
    cred = ""
    if username:
        cred = f"{urllib.parse.quote(username, safe='')}:{urllib.parse.quote(password, safe='')}@"
    return [f"rtsp://{cred}{ip}:{port}{path}" for path in RTSP_PATHS]


def guess_rtsp(ip: str, username: str = "admin", password: str = "",
               port: int = 554, limit: int = 8) -> dict | None:
    """Prueba rutas RTSP típicas de VStarcam/Eye4/O-KAM hasta dar con una válida."""
    candidates = rtsp_candidates(ip, username, password, port)[:limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(ffmpeg.probe, url, 8.0): url for url in candidates}
        for fut in concurrent.futures.as_completed(futures):
            try:
                info = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if info.get("ok"):
                return info
    return None


def http_ptz(ip: str, action: str, port: int = 81, speed: int = 45) -> bool:
    """Fallback PTZ del protocolo CGI de VStarcam (puerto 81 por defecto)."""
    import urllib.request

    acts = {
        "up": "up", "down": "down", "left": "left", "right": "right",
        "stop": "stop", "home": "home", "zoom_in": "zoomin", "zoom_out": "zoomout",
    }
    act = acts.get(action)
    if not act:
        return False
    url = f"http://{ip}:{port}/cgi-bin/hi3510/ptzctrl.cgi?-step={0 if action == 'stop' else 1}&-act={act}&-speed={speed}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            resp.read(64)
        return True
    except Exception:  # noqa: BLE001
        return False
