"""Núcleo: orquesta cámaras, PTZ, capturas, grabación, eventos y nube."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import discovery, ffmpeg, onvif
from .alerts import Alerter
from .cloud import CloudUploader
from .config import Config
from .db import DB
from .ffmpeg import Recorder
from .go2rtc import Go2RTC
from .motion import MotionDetector
from .util import RECORDINGS, SNAPSHOTS, Throttle, human_size

logger = logging.getLogger("vstarcam.core")

MOVE_VECTORS = {
    "up": (0.0, 1.0, 0.0),
    "down": (0.0, -1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "upleft": (-0.7, 0.7, 0.0),
    "upright": (0.7, 0.7, 0.0),
    "downleft": (-0.7, -0.7, 0.0),
    "downright": (0.7, -0.7, 0.0),
    "zoom_in": (0.0, 0.0, 1.0),
    "zoom_out": (0.0, 0.0, -1.0),
}


class CameraManager:
    def __init__(self, config: Config, db: DB, go2rtc: Go2RTC,
                 cloud: CloudUploader, alerter: Alerter) -> None:
        self.config = config
        self.db = db
        self.go2rtc = go2rtc
        self.cloud = cloud
        self.alerter = alerter

        self._onvif: dict[str, onvif.OnvifClient] = {}
        self._onvif_lock = threading.Lock()
        self._recorders: dict[str, Recorder] = {}
        self._detectors: dict[str, MotionDetector] = {}
        self._lock = threading.RLock()
        self._snap_cache: dict[str, tuple[float, bytes]] = {}
        self._snap_locks: dict[str, threading.Lock] = {}
        self._auto_stops: dict[str, threading.Timer] = {}
        self._alert_throttle: dict[str, Throttle] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="event")
        self._stop = threading.Event()
        self._cleanup_thread: threading.Thread | None = None
        self.last_streams: dict[str, str] = {}

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.last_streams = self.go2rtc.streams()
        for cam in self.config.cameras(only_enabled=True):
            self._alert_throttle[cam["id"]] = Throttle(15)
            if cam.get("motion", {}).get("enabled") and cam.get("rtsp_url"):
                self._detector(cam).start()
            if cam.get("record", {}).get("mode") == "continuous" and cam.get("rtsp_url"):
                self._recorder(cam).start_continuous(
                    int(cam["record"].get("segment_seconds", 300))
                )
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, name="cleanup", daemon=True)
        self._cleanup_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for det in list(self._detectors.values()):
            det.stop()
        for rec in list(self._recorders.values()):
            rec.stop()
        for timer in list(self._auto_stops.values()):
            timer.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def apply_changes(self) -> None:
        """Tras editar cámaras: reinicia detectores/grabadores y go2rtc si cambió algo."""
        streaming = self.go2rtc.streams()
        if streaming != self.last_streams:
            previous = self.last_streams
            self.last_streams = streaming
            self.go2rtc.sync_streams(previous, streaming)

        current_ids = {c["id"] for c in self.config.cameras()}
        with self._lock:
            for cam_id in list(self._detectors):
                if cam_id not in current_ids:
                    self._detectors.pop(cam_id).stop()
            for cam_id in list(self._recorders):
                if cam_id not in current_ids:
                    self._recorders.pop(cam_id).stop()
            for cam_id in list(self._onvif):
                if cam_id not in current_ids:
                    self._onvif.pop(cam_id, None)

        for cam in self.config.cameras(only_enabled=True):
            self._alert_throttle.setdefault(cam["id"], Throttle(15))
            want_motion = bool(cam.get("motion", {}).get("enabled") and cam.get("rtsp_url"))
            det = self._detectors.get(cam["id"])
            if det is not None and det.cam.get("rtsp_url") != cam.get("rtsp_url"):
                det.stop()
                self._detectors.pop(cam["id"], None)
                det = None
            if want_motion and det is None:
                self._detector(cam).start()
            elif want_motion and det is not None and not det.running:
                det.cam = cam
                det.start()
            elif not want_motion and det is not None:
                self._detectors.pop(cam["id"]).stop()

            mode = cam.get("record", {}).get("mode", "off")
            rec = self._recorders.get(cam["id"])
            if rec is not None and rec.cam.get("rtsp_url") != cam.get("rtsp_url"):
                rec.stop()
                self._recorders.pop(cam["id"], None)
                rec = None
            if mode == "continuous" and cam.get("rtsp_url"):
                if rec is None or not rec.is_running():
                    rec = self._recorder(cam)
                    rec.cam = cam
                    rec.start_continuous(int(cam["record"].get("segment_seconds", 300)))
            elif mode != "continuous" and rec is not None:
                self._recorders.pop(cam["id"]).stop()
            elif rec is not None:
                rec.cam = cam

    # --------------------------------------------------------------- helpers
    def _recorder(self, cam: dict) -> Recorder:
        with self._lock:
            rec = self._recorders.get(cam["id"])
            if rec is None:
                rec = Recorder(cam)
                self._recorders[cam["id"]] = rec
            rec.cam = cam
            return rec

    def _detector(self, cam: dict) -> MotionDetector:
        with self._lock:
            det = self._detectors.get(cam["id"])
            if det is None:
                det = MotionDetector(cam, self._on_motion)
                self._detectors[cam["id"]] = det
            det.cam = cam
            return det

    def _snap_lock(self, cam_id: str) -> threading.Lock:
        with self._lock:
            return self._snap_locks.setdefault(cam_id, threading.Lock())

    # ----------------------------------------------------------------- ONVIF
    def onvif_client(self, cam: dict, force: bool = False) -> onvif.OnvifClient:
        with self._lock:
            client = None if force else self._onvif.get(cam["id"])
        if client is not None:
            return client
        client = onvif.OnvifClient(
            cam["host"],
            cam.get("username", ""),
            cam.get("password", ""),
            port=int(cam.get("onvif_port", 80)),
            device_url=(
                f"http://{cam['host']}:{int(cam.get('onvif_port', 80))}"
                f"{cam.get('onvif_path') or '/onvif/device_service'}"
            ),
            timeout=6.0,
        )
        try:
            client.connect()
        except Exception:
            with self._lock:
                self._onvif.pop(cam["id"], None)
            raise
        with self._lock:
            self._onvif[cam["id"]] = client
        return client

    def ptz(self, cam_id: str, action: str, speed: float = 0.6,
            duration: float = 0.6, preset: str = "") -> dict:
        cam = self.config.camera(cam_id)
        if not cam:
            raise KeyError(cam_id)

        driver = str(cam.get("ptz_driver") or "").strip().lower()
        if driver == "cgi":
            return self._ptz_cgi(cam, cam_id, action, duration)
        if driver == "onvif":
            return self._ptz_onvif(cam, cam_id, action, speed, duration, preset)

        try:
            return self._ptz_onvif(cam, cam_id, action, speed, duration, preset)
        except Exception as exc:  # noqa: BLE001
            # Muchas VStarcam no exponen PTZ por ONVIF (el servicio de media
            # responde 404 o se cuelga) y solo aceptan su CGI propietario.
            try:
                result = self._ptz_cgi(cam, cam_id, action, duration)
            except Exception as cgi_exc:  # noqa: BLE001
                raise onvif.OnvifError(f"ONVIF: {exc}; CGI: {cgi_exc}") from None
            cam["ptz_driver"] = "cgi"
            self.config.upsert_camera(cam)
            logger.info("PTZ de %s usando driver CGI", cam_id)
            return result

    def _ptz_onvif(self, cam: dict, cam_id: str, action: str, speed: float,
                   duration: float, preset: str = "") -> dict:
        profile = cam.get("ptz_profile_token") or cam.get("profile_token")
        if not profile:
            client = self.onvif_client(cam, force=True)
            profiles = client.get_profiles()
            if not profiles:
                raise onvif.OnvifError("La cámara no reporta perfiles de vídeo")
            profile = profiles[0]["token"]
            cam["ptz_profile_token"] = profile
            self.config.upsert_camera(cam)

        client = self.onvif_client(cam)
        speed = max(0.05, min(1.0, float(speed)))

        if action in ("stop", "halt"):
            self._cancel_auto_stop(cam_id)
            client.ptz_stop(profile)
            return {"ok": True, "action": "stop", "driver": "onvif"}

        if action == "home":
            body = (
                "<tptz:GotoHomePosition>"
                f"<tptz:ProfileToken>{onvif._esc(profile)}</tptz:ProfileToken>"
                "</tptz:GotoHomePosition>"
            )
            client._ptz("GotoHomePosition", body)
            return {"ok": True, "action": "home", "driver": "onvif"}

        if action == "preset":
            client.ptz_goto_preset(profile, preset)
            return {"ok": True, "action": "preset", "preset": preset,
                    "driver": "onvif"}

        vector = MOVE_VECTORS.get(action)
        if not vector:
            raise ValueError(f"Acción PTZ desconocida: {action}")

        pan, tilt, zoom = vector
        client.ptz_move(profile, pan * speed, tilt * speed, zoom * speed)

        if duration > 0:
            self._schedule_stop(cam_id, profile, duration)
        return {"ok": True, "action": action, "driver": "onvif"}

    def _ptz_cgi(self, cam: dict, cam_id: str, action: str,
                 duration: float) -> dict:
        """PTZ por el CGI propietario (``decoder_control.cgi``)."""
        host = cam.get("host") or ""
        try:
            port = int(cam.get("cgi_port") or 0)
        except (TypeError, ValueError):
            port = 0
        if not port:
            try:
                onvif_port = int(cam.get("onvif_port") or 0)
            except (TypeError, ValueError):
                onvif_port = 0
            port = onvif_port if onvif_port in (80, 81, 8080) else 81
        user = cam.get("username") or ""
        password = cam.get("password") or ""

        if action in ("stop", "halt"):
            self._cancel_auto_stop(cam_id)
            if not discovery.http_ptz_stop(
                host, "stop", port=port, username=user, password=password,
            ):
                raise onvif.OnvifError("la cámara no aceptó el stop por CGI")
            return {"ok": True, "action": "stop", "driver": "cgi"}

        if action == "home":
            self._cancel_auto_stop(cam_id)
            if not discovery.http_ptz(
                host, "home", port=port, username=user, password=password,
            ):
                raise onvif.OnvifError("la cámara no aceptó home por CGI")
            return {"ok": True, "action": "home", "driver": "cgi"}

        if action == "preset":
            raise onvif.OnvifError("esta cámara no soporta presets por CGI")

        if action not in discovery.CGI_PTZ_COMMANDS:
            raise onvif.OnvifError(
                f"la cámara no soporta la acción PTZ '{action}' por CGI"
            )

        if not discovery.http_ptz(
            host, action, port=port, username=user, password=password,
            onestep=duration <= 0,
        ):
            raise onvif.OnvifError(f"PTZ CGI rechazado por {host}:{port}")

        if duration > 0:
            self._schedule_cgi_stop(
                cam_id, host, port, user, password, action, duration,
            )
        return {"ok": True, "action": action, "driver": "cgi"}

    def _schedule_cgi_stop(self, cam_id: str, host: str, port: int,
                           user: str, password: str, action: str,
                           duration: float) -> None:
        self._cancel_auto_stop(cam_id)

        def _stop() -> None:
            try:
                discovery.http_ptz_stop(
                    host, action, port=port, username=user, password=password,
                )
            finally:
                self._auto_stops.pop(cam_id, None)

        timer = threading.Timer(duration, _stop)
        timer.daemon = True
        self._auto_stops[cam_id] = timer
        timer.start()

    def _schedule_stop(self, cam_id: str, profile: str, duration: float) -> None:
        self._cancel_auto_stop(cam_id)
        cam = self.config.camera(cam_id)
        if not cam:
            return

        def _stop() -> None:
            try:
                self.onvif_client(cam).ptz_stop(profile)
            except Exception:  # noqa: BLE001
                try:
                    self._ptz_cgi(cam, cam_id, "stop", 0)
                except Exception:  # noqa: BLE001
                    logger.warning("No se pudo detener el PTZ de %s", cam_id)
            self._auto_stops.pop(cam_id, None)

        timer = threading.Timer(duration, _stop)
        timer.daemon = True
        self._auto_stops[cam_id] = timer
        timer.start()

    def _cancel_auto_stop(self, cam_id: str) -> None:
        timer = self._auto_stops.pop(cam_id, None)
        if timer:
            timer.cancel()

    def presets(self, cam_id: str) -> list[dict]:
        cam = self.config.camera(cam_id)
        if not cam:
            raise KeyError(cam_id)
        profile = cam.get("ptz_profile_token") or cam.get("profile_token")
        client = self.onvif_client(cam)
        if not profile:
            profiles = client.get_profiles()
            if not profiles:
                return []
            profile = profiles[0]["token"]
        return client.ptz_presets(profile)

    def set_preset(self, cam_id: str, name: str, token: str = "") -> dict:
        cam = self.config.camera(cam_id)
        if not cam:
            raise KeyError(cam_id)
        profile = cam.get("ptz_profile_token") or cam.get("profile_token")
        client = self.onvif_client(cam)
        if not profile:
            profiles = client.get_profiles()
            if not profiles:
                raise onvif.OnvifError("Sin perfiles ONVIF")
            profile = profiles[0]["token"]
        new_token = client.ptz_set_preset(profile, name, token)
        return {"ok": True, "token": new_token}

    # -------------------------------------------------------------- capturas
    def snapshot(self, cam_id: str, fresh: bool = False) -> bytes:
        cam = self.config.camera(cam_id)
        if not cam:
            raise KeyError(cam_id)
        now = time.monotonic()
        if not fresh:
            cached = self._snap_cache.get(cam_id)
            if cached and now - cached[0] < 1.5:
                return cached[1]
        lock = self._snap_lock(cam_id)
        with lock:
            if not fresh:
                cached = self._snap_cache.get(cam_id)
                if cached and time.monotonic() - cached[0] < 1.5:
                    return cached[1]

            data = None
            if self.go2rtc.enabled() and cam.get("rtsp_url"):
                data = self.go2rtc.frame_jpeg(cam_id, timeout=6)
            if not data and cam.get("snapshot_url"):
                data = self._http_snapshot(cam["snapshot_url"], cam)
            if not data and cam.get("rtsp_url"):
                data = self._ffmpeg_snapshot(cam)
            if not data and cam.get("snapshot_url"):
                data = self._http_snapshot(cam["snapshot_url"], cam)
            if not data:
                raise RuntimeError(
                    "No se pudo obtener imagen. Comprueba la URL RTSP y las credenciales."
                )
            self._snap_cache[cam_id] = (time.monotonic(), data)
            return data

    @staticmethod
    def _http_snapshot(url: str, cam: dict) -> bytes | None:
        import base64

        req = urllib.request.Request(url)
        if cam.get("username"):
            token = base64.b64encode(
                f"{cam['username']}:{cam.get('password', '')}".encode()
            ).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
            return data if len(data) > 300 else None
        except (urllib.error.URLError, OSError):
            return None

    @staticmethod
    def _ffmpeg_snapshot(cam: dict) -> bytes | None:
        import tempfile

        from .util import DATA

        tmp = Path(tempfile.mkstemp(suffix=".jpg", dir=str(DATA))[1])
        try:
            if ffmpeg.snapshot(cam["rtsp_url"], tmp, width=1920):
                return tmp.read_bytes()
            return None
        finally:
            tmp.unlink(missing_ok=True)

    def save_snapshot_file(self, cam_id: str, fresh: bool = True) -> Path:
        data = self.snapshot(cam_id, fresh=fresh)
        day = time.strftime("%Y-%m-%d")
        folder = SNAPSHOTS / cam_id / day
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{cam_id}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        path.write_bytes(data)
        self.db.add_event(cam_id, "snapshot", "Captura manual", str(path))
        self.cloud.enqueue(path, "snapshot")
        return path

    # ------------------------------------------------------------- grabación
    def recording_start(self, cam_id: str) -> dict:
        cam = self.config.camera(cam_id)
        if not cam:
            raise KeyError(cam_id)
        if not cam.get("rtsp_url"):
            raise ValueError("La cámara no tiene URL RTSP configurada")
        rec = self._recorder(cam)
        out_dir = rec.start_continuous(int(cam["record"].get("segment_seconds", 300)))
        self.db.add_event(cam_id, "record_start", "Grabación manual iniciada")
        return {"ok": True, "dir": str(out_dir)}

    def recording_stop(self, cam_id: str) -> dict:
        with self._lock:
            rec = self._recorders.get(cam_id)
        if rec:
            rec.stop()
            self.db.add_event(cam_id, "record_stop", "Grabación detenida")
        return {"ok": True, "recording": False}

    def recordings(self, cam_id: str | None = None, limit: int = 300) -> list[dict]:
        root = RECORDINGS / cam_id if cam_id else RECORDINGS
        items: list[dict] = []
        if not root.exists():
            return items
        for path in root.rglob("*.mp4"):
            try:
                st = path.stat()
            except OSError:
                continue
            rel = path.relative_to(RECORDINGS).as_posix()
            items.append(
                {
                    "path": rel,
                    "camera": rel.split("/", 1)[0],
                    "name": path.name,
                    "size": st.st_size,
                    "size_human": human_size(st.st_size),
                    "mtime": st.st_mtime,
                    "url": "/media/" + rel,
                }
            )
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return items[:limit]

    def recording_path(self, rel: str) -> Path | None:
        candidate = (RECORDINGS / rel).resolve()
        try:
            candidate.relative_to(RECORDINGS.resolve())
        except ValueError:
            return None
        if candidate.is_file() and candidate.suffix.lower() == ".mp4":
            return candidate
        return None

    def delete_recording(self, rel: str) -> bool:
        path = self.recording_path(rel)
        if not path:
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # --------------------------------------------------------------- eventos
    def _on_motion(self, cam: dict, score: float) -> None:
        self._pool.submit(self._handle_motion, cam, score)

    def _handle_motion(self, cam: dict, score: float) -> None:
        cam_id = cam["id"]
        day = time.strftime("%Y-%m-%d")
        folder = SNAPSHOTS / cam_id / day
        folder.mkdir(parents=True, exist_ok=True)
        snap_path = folder / f"{cam_id}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        snap_ok = False
        try:
            snap_path.write_bytes(self.snapshot(cam_id, fresh=True))
            snap_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sin captura para evento de %s: %s", cam_id, exc)

        clip_path = None
        if cam.get("record", {}).get("mode") == "motion":
            clip_path = self._recorder(cam).clip(int(cam["record"].get("clip_seconds", 15)), "motion")

        message = f"Movimiento detectado ({score:.1f}%)"
        self.db.add_event(
            cam_id,
            "motion",
            message,
            str(snap_path) if snap_ok else "",
            str(clip_path) if clip_path else "",
        )
        logger.info("%s: %s", cam_id, message)

        if snap_ok:
            self.cloud.enqueue(snap_path, "snapshot")
        if clip_path:
            self.cloud.enqueue(clip_path, "recording")

        throttle = self._alert_throttle.setdefault(cam_id, Throttle(15))
        if self.alerter.enabled() and throttle.ready():
            image = snap_path if snap_ok else None
            self._pool.submit(
                self.alerter.send,
                f"{cam.get('name', cam_id)}",
                message,
                image,
            )

    # ------------------------------------------------------------- limpieza
    def _cleanup_loop(self) -> None:
        while not self._stop.wait(1800):
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001
                logger.exception("Error en limpieza automática")

    def cleanup(self) -> dict:
        keep_days = int(self.config.get("storage", "keep_days", default=14) or 14)
        max_gb = float(self.config.get("storage", "max_gb", default=100) or 100)
        cutoff = time.time() - keep_days * 86400
        removed = 0
        freed = 0

        for root in (RECORDINGS, SNAPSHOTS):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    freed += st.st_size
                    path.unlink(missing_ok=True)
                    removed += 1

        # Límite de tamaño total: borra lo más antiguo primero.
        files: list[Path] = []
        for root in (RECORDINGS, SNAPSHOTS):
            if root.exists():
                files.extend(p for p in root.rglob("*") if p.is_file())
        total = sum(p.stat().st_size for p in files if p.exists())
        limit = max_gb * 1024**3
        if total > limit:
            files.sort(key=lambda p: p.stat().st_mtime)
            for path in files:
                if total <= limit * 0.9:
                    break
                try:
                    size = path.stat().st_size
                    path.unlink()
                    total -= size
                    removed += 1
                    freed += size
                except OSError:
                    continue

        self.db.prune_events(keep_days)
        if removed:
            logger.info("Limpieza: %s ficheros borrados (%s)", removed, human_size(freed))
        return {"removed": removed, "freed": freed}

    # --------------------------------------------------------------- estado
    def status(self) -> dict:
        cams = []
        for cam in self.config.cameras():
            rec = self._recorders.get(cam["id"])
            det = self._detectors.get(cam["id"])
            cams.append(
                {
                    "id": cam["id"],
                    "name": cam["name"],
                    "enabled": cam.get("enabled", True),
                    "host": cam.get("host"),
                    "recording": bool(rec and rec.is_running()),
                    "motion": det.status() if det else None,
                    "has_stream": bool(cam.get("rtsp_url")),
                    "record_mode": cam.get("record", {}).get("mode", "off"),
                }
            )
        return {
            "cameras": cams,
            "go2rtc": {
                "enabled": self.go2rtc.enabled(),
                "installed": self.go2rtc.available,
                "error": self.go2rtc.last_error,
            },
            "cloud": {"enabled": self.config.get("cloud", "enabled", default=False),
                      "stats": self.db.upload_stats()},
            "disk": ffmpeg.disk_usage(RECORDINGS),
        }
