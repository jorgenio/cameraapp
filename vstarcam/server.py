"""Servidor HTTP y API JSON (sin frameworks, solo stdlib)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import discovery, ffmpeg
from .config import Config
from .core import CameraManager
from .util import RECORDINGS, SNAPSHOTS, WEB, local_ip, slug

logger = logging.getLogger("vstarcam.server")

MAX_BODY = 4 * 1024 * 1024
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


def _safe_join(base: Path, rel: str) -> Path | None:
    try:
        candidate = (base / rel).resolve()
        candidate.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return candidate


class App:
    def __init__(self, config: Config, manager: CameraManager) -> None:
        self.config = config
        self.manager = manager

    # --------------------------------------------------------------- auth
    @property
    def password(self) -> str:
        return self.config.get("server", "password", default="") or ""

    def token(self) -> str:
        return hmac.new(
            self.password.encode(), b"vstarcam-session-v1", hashlib.sha256
        ).hexdigest()

    def check_password(self, password: str) -> bool:
        if not self.password:
            return True
        return hmac.compare_digest(password, self.password)


class Handler(BaseHTTPRequestHandler):
    server_version = "vstarcam/1.0"
    protocol_version = "HTTP/1.1"
    app: App  # inyectado al arrancar

    # ------------------------------------------------------------- utilidades
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8",
              extra: dict | None = None, head_only: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def error(self, status: int, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("Cuerpo de petición demasiado grande")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON no válido: {exc}") from None
        if not isinstance(data, dict):
            raise ValueError("Se esperaba un objeto JSON")
        return data

    def cookies(self) -> dict[str, str]:
        raw = self.headers.get("Cookie") or ""
        out = {}
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key:
                out[key] = value
        return out

    def authorized(self) -> bool:
        app = self.app
        if not app.password:
            return True
        token = self.cookies().get("vs_session", "")
        return bool(token) and hmac.compare_digest(token, app.token())

    def client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    # ---------------------------------------------------------------- verbos
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            self._route(method, path, query)
        except KeyError as exc:
            self.error(404, f"No encontrado: {exc}")
        except ValueError as exc:
            self.error(400, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error atendiendo %s %s", method, path)
            self.error(500, f"{type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------- rutas
    def _route(self, method: str, path: str, query: dict) -> None:
        app = self.app
        mgr = app.manager

        # Público
        if path == "/login" and method == "GET":
            return self._login_page()
        if path == "/api/login" and method == "POST":
            data = self.read_json()
            if not app.check_password(str(data.get("password", ""))):
                time.sleep(0.6)
                return self.error(401, "Contraseña incorrecta")
            cookie = f"vs_session={app.token()}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
            return self._send_json_cookie({"ok": True}, cookie)
        if path == "/api/health":
            return self.send_json({"ok": True, "name": "vstarcam", "version": 1})

        if not self.authorized():
            if path.startswith("/api/"):
                return self.error(401, "No autorizado")
            return self._redirect("/login")

        # Estáticos
        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/favicon.ico":
            return self._serve_static("favicon.svg")

        # API
        if path == "/api/status" and method == "GET":
            return self.send_json(mgr.status())

        if path == "/api/cameras":
            if method == "GET":
                cams = mgr.config.cameras()
                public = []
                for cam in cams:
                    item = {k: v for k, v in cam.items() if k not in ("password",)}
                    item["has_password"] = bool(cam.get("password"))
                    public.append(item)
                return self.send_json({"cameras": public})
            if method == "POST":
                data = self.read_json()
                if not data.get("name") and not data.get("rtsp_url") and not data.get("host"):
                    return self.error(400, "Faltan datos de la cámara")
                if not data.get("id"):
                    data["id"] = slug(str(data.get("name") or data.get("host") or "cam"))
                existing = mgr.config.camera(str(data.get("id")))
                if existing and not data.get("password"):
                    data.pop("password", None)  # conserva la contraseña guardada
                saved = mgr.config.upsert_camera(data)
                mgr.apply_changes()
                safe = {k: v for k, v in saved.items() if k != "password"}
                return self.send_json({"ok": True, "camera": safe})

        m = re.fullmatch(r"/api/cameras/([^/]+)", path)
        if m:
            cam_id = m.group(1)
            if method == "DELETE":
                ok = mgr.config.delete_camera(cam_id)
                if ok:
                    mgr.apply_changes()
                return self.send_json({"ok": ok})
            if method == "GET":
                cam = mgr.config.camera(cam_id)
                if not cam:
                    return self.error(404, "Cámara no existe")
                safe = {k: v for k, v in cam.items() if k != "password"}
                safe["has_password"] = bool(cam.get("password"))
                return self.send_json({"ok": True, "camera": safe})

        m = re.fullmatch(r"/api/cameras/([^/]+)/test", path)
        if m and method == "POST":
            return self._test_camera(m.group(1))

        m = re.fullmatch(r"/api/cameras/([^/]+)/snapshot\.jpg", path)
        if m and method == "GET":
            cam_id = m.group(1)
            fresh = query.get("fresh", ["0"])[0] == "1"
            try:
                data = mgr.snapshot(cam_id, fresh=fresh)
            except RuntimeError as exc:
                return self.error(503, str(exc))
            return self._send(200, data, "image/jpeg", {"Cache-Control": "no-store"})

        m = re.fullmatch(r"/api/cameras/([^/]+)/snapshot", path)
        if m and method == "POST":
            try:
                path_saved = mgr.save_snapshot_file(m.group(1))
            except RuntimeError as exc:
                return self.error(503, str(exc))
            rel = path_saved.relative_to(SNAPSHOTS).as_posix()
            return self.send_json({"ok": True, "path": str(path_saved), "url": f"/snapshots/{rel}"})

        m = re.fullmatch(r"/api/cameras/([^/]+)/presets", path)
        if m:
            cam_id = m.group(1)
            if method == "GET":
                try:
                    return self.send_json({"ok": True, "presets": mgr.presets(cam_id)})
                except Exception as exc:  # noqa: BLE001
                    return self.error(502, str(exc))
            if method == "POST":
                data = self.read_json()
                try:
                    result = mgr.set_preset(cam_id, str(data.get("name") or "Preset"),
                                            str(data.get("token") or ""))
                    return self.send_json({"ok": True, **result})
                except Exception as exc:  # noqa: BLE001
                    return self.error(502, str(exc))

        m = re.fullmatch(r"/api/ptz/([^/]+)", path)
        if m and method == "POST":
            data = self.read_json()
            action = str(data.get("action") or "stop")
            try:
                result = mgr.ptz(
                    m.group(1),
                    action,
                    speed=float(data.get("speed", 0.6)),
                    duration=float(data.get("duration", 0.6)),
                    preset=str(data.get("preset") or ""),
                )
                return self.send_json(result)
            except Exception as exc:  # noqa: BLE001
                return self.error(502, f"PTZ: {exc}")

        if path == "/api/webrtc" and method == "POST":
            cam_id = query.get("src", [""])[0]
            if not cam_id:
                return self.error(400, "Falta src")
            length = int(self.headers.get("Content-Length") or 0)
            offer = self.rfile.read(min(length, MAX_BODY)).decode("utf-8", "replace")
            if not self.app.manager.go2rtc.enabled():
                return self.error(503, "go2rtc no está disponible")
            try:
                answer = self.app.manager.go2rtc.webrtc_offer(cam_id, offer)
            except Exception as exc:  # noqa: BLE001
                return self.error(502, f"WebRTC: {exc}")
            return self._send(201, answer.encode(), "application/sdp")

        if path == "/api/frame" and method == "GET":
            cam_id = query.get("src", [""])[0]
            try:
                data = mgr.snapshot(cam_id)
            except RuntimeError as exc:
                return self.error(503, str(exc))
            return self._send(200, data, "image/jpeg", {"Cache-Control": "no-store"})

        m = re.fullmatch(r"/api/record/([^/]+)/(start|stop)", path)
        if m and method == "POST":
            try:
                if m.group(2) == "start":
                    return self.send_json(mgr.recording_start(m.group(1)))
                return self.send_json(mgr.recording_stop(m.group(1)))
            except Exception as exc:  # noqa: BLE001
                return self.error(502, str(exc))

        if path == "/api/recordings" and method == "GET":
            cam = query.get("camera", [None])[0]
            return self.send_json({"recordings": mgr.recordings(cam)})
        if path == "/api/recordings" and method == "DELETE":
            rel = query.get("path", [""])[0]
            return self.send_json({"ok": mgr.delete_recording(rel)})

        if path == "/api/events":
            if method == "GET":
                limit = int(query.get("limit", ["60"])[0])
                cam = query.get("camera", [None])[0]
                events = mgr.db.events(limit=limit, camera_id=cam)
                for ev in events:
                    ev["snapshot_url"] = _file_url(ev.get("snapshot"), SNAPSHOTS, "/snapshots/")
                    ev["clip_url"] = _file_url(ev.get("clip"), RECORDINGS, "/media/")
                return self.send_json({"events": events})
            if method == "DELETE":
                event_id = int(query.get("id", ["0"])[0])
                mgr.db.delete_event(event_id)
                return self.send_json({"ok": True})

        if path == "/api/settings":
            if method == "GET":
                cfg = mgr.config.get()
                cfg.pop("cameras", None)
                cloud = cfg.get("cloud", {})
                if cloud.get("password"):
                    cloud["password"] = "********"
                return self.send_json({"settings": cfg})
            if method == "POST":
                data = self.read_json()
                cloud = data.get("cloud")
                if isinstance(cloud, dict) and cloud.get("password") == "********":
                    cloud.pop("password")
                mgr.config.update(data)
                mgr.apply_changes()
                return self.send_json({"ok": True})

        if path == "/api/scan" and method == "POST":
            data = self.read_json()
            result = discovery.merged_scan(
                timeout=float(data.get("timeout", 8)),
                username=str(data.get("username", "admin")),
                password=str(data.get("password", "")),
            )
            return self.send_json(result)

        if path == "/api/detect" and method == "POST":
            data = self.read_json()
            if not data.get("host"):
                return self.error(400, "Falta host/IP")
            password = str(data.get("password", ""))
            if not password and data.get("camera_id"):
                stored = mgr.config.camera(str(data["camera_id"]))
                if stored:
                    password = stored.get("password", "")
            result = discovery.detect(
                str(data["host"]),
                str(data.get("username", "admin")),
                password,
                port=int(data.get("onvif_port", 80)),
            )
            return self.send_json(result)

        if path == "/api/probe" and method == "POST":
            data = self.read_json()
            url = str(data.get("url") or "")
            if not url:
                return self.error(400, "Falta la URL")
            return self.send_json(ffmpeg.probe(url, timeout=float(data.get("timeout", 15))))

        if path == "/api/cloud/test" and method == "POST":
            return self.send_json(mgr.cloud.test())

        if path == "/api/alerts/test" and method == "POST":
            return self.send_json(mgr.alerter.send("VStarcam", "Prueba de aviso 😀"))

        if path == "/api/cleanup" and method == "POST":
            return self.send_json(mgr.cleanup())

        if path == "/api/system":
            return self.send_json(
                {
                    "local_ip": local_ip(),
                    "ffmpeg": ffmpeg.ffmpeg() if _has("ffmpeg") else None,
                    "go2rtc": mgr.go2rtc.binary,
                    "go2rtc_api": mgr.go2rtc.api_base,
                }
            )

        # Medios y capturas
        if path.startswith("/media/"):
            return self._serve_file(mgr.recording_path(path[len("/media/"):]), range_ok=True)
        if path.startswith("/snapshots/"):
            target = _safe_join(SNAPSHOTS, path[len("/snapshots/"):])
            return self._serve_file(target, range_ok=False)

        return self.error(404, f"Ruta desconocida: {path}")

    # ------------------------------------------------------------ respuestas
    def _send_json_cookie(self, data: dict, cookie: str) -> None:
        body = json.dumps(data).encode()
        self._send(200, body, "application/json", {"Set-Cookie": cookie})

    def _redirect(self, location: str) -> None:
        self._send(302, b"", "text/plain", {"Location": location})

    def _login_page(self) -> None:
        body = LOGIN_HTML.encode("utf-8")
        self._send(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def _serve_static(self, rel: str) -> None:
        target = _safe_join(WEB, rel)
        if not target or not target.is_file():
            return self.error(404, "Recurso no encontrado")
        ctype = STATIC_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        cache = "no-cache" if target.suffix == ".html" else "public, max-age=300"
        self._send(200, data, ctype, {"Cache-Control": cache})

    def _serve_file(self, target: Path | None, range_ok: bool = True) -> None:
        if not target or not target.is_file():
            return self.error(404, "Fichero no encontrado")
        size = target.stat().st_size
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        range_header = self.headers.get("Range") if range_ok else None
        start, end = 0, size - 1
        status = 200
        extra = {"Accept-Ranges": "bytes"} if range_ok else {}
        if range_header:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                status = 206
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(target, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # --------------------------------------------------------------- acciones
    def _test_camera(self, cam_id: str) -> None:
        cam = self.app.manager.config.camera(cam_id)
        if not cam:
            return self.error(404, "Cámara no existe")
        out: dict = {"ok": True, "onvif": None, "stream": None}
        try:
            result = discovery.detect(
                cam["host"], cam.get("username", ""), cam.get("password", ""),
                port=int(cam.get("onvif_port", 80)),
            )
            out["onvif"] = {
                "ok": result.get("onvif", False),
                "info": result.get("info", {}),
                "error": result.get("error", ""),
                "ptz": result.get("ptz"),
            }
        except Exception as exc:  # noqa: BLE001
            out["onvif"] = {"ok": False, "error": str(exc)}
        if cam.get("rtsp_url"):
            out["stream"] = ffmpeg.probe(cam["rtsp_url"], timeout=15)
        out["ok"] = bool(out["stream"] and out["stream"].get("ok"))
        return self.send_json(out)


def _file_url(path_str: str | None, base: Path, prefix: str) -> str:
    """Convierte una ruta absoluta guardada en la BD a una URL servible."""
    if not path_str:
        return ""
    try:
        path = Path(path_str).resolve()
        rel = path.relative_to(base.resolve())
        if not path.is_file():
            return ""
        return prefix + rel.as_posix()
    except (ValueError, OSError):
        return ""


def _has(name: str) -> bool:
    from .util import which

    return bool(which(name))


LOGIN_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VStarcam · Acceso</title>
<style>
 :root{color-scheme:dark}
 *{box-sizing:border-box}
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0f14;
      font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#e6edf3}
 .card{width:min(360px,92vw);background:#131a22;border:1px solid #223042;border-radius:16px;
       padding:32px 28px;box-shadow:0 20px 60px #0008}
 h1{margin:0 0 6px;font-size:20px;letter-spacing:.2px}
 p{margin:0 0 22px;color:#8b9bb0;font-size:13px}
 input{width:100%;padding:12px 14px;border-radius:10px;border:1px solid #2a3a4f;
       background:#0b0f14;color:#e6edf3;font-size:15px;outline:none}
 input:focus{border-color:#3b82f6}
 button{margin-top:14px;width:100%;padding:12px;border:0;border-radius:10px;cursor:pointer;
        background:#2563eb;color:#fff;font-size:15px;font-weight:600}
 button:hover{background:#1d4ed8}
 .err{margin-top:12px;color:#f87171;font-size:13px;min-height:18px}
</style></head>
<body><form class="card" id="f">
  <h1>📷 VStarcam</h1>
  <p>Introduce la contraseña del panel</p>
  <input type="password" name="password" placeholder="Contraseña" autofocus autocomplete="current-password">
  <button type="submit">Entrar</button>
  <div class="err" id="e"></div>
</form>
<script>
document.getElementById('f').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({password: ev.target.password.value})});
  if (r.ok) location.href = '/';
  else document.getElementById('e').textContent = 'Contraseña incorrecta';
});
</script></body></html>"""


def build_server(host: str, port: int, app: App) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.allow_reuse_address = True
    return httpd
