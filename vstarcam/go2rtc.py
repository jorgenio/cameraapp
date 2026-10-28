"""Gestión del proceso go2rtc (RTSP → WebRTC/MJPEG para el navegador)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import DATA, ROOT, all_ips, which

logger = logging.getLogger("vstarcam.go2rtc")

CONFIG_PATH = DATA / "go2rtc.yaml"


def _yaml_str(value: str) -> str:
    # comillas dobles + escapado mínimo suficiente para URLs RTSP
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Go2RTC:
    def __init__(self, config) -> None:
        self.config = config
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.binary = which("go2rtc")
        self.api_port = int(config.get("go2rtc", "api_port", default=1984))
        self.webrtc_port = int(config.get("go2rtc", "webrtc_port", default=8555))
        self.last_error = ""

    # ------------------------------------------------------------- helpers
    @property
    def available(self) -> bool:
        return bool(self.binary)

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    def enabled(self) -> bool:
        return bool(self.config.get("go2rtc", "enabled", default=True)) and self.available

    def _stream_name(self, cam: dict) -> str:
        return cam["id"]

    def streams(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for cam in self.config.cameras(only_enabled=True):
            url = cam.get("rtsp_url")
            if url:
                out[self._stream_name(cam)] = url
        return out

    # ------------------------------------------------------------ lifecycle
    def write_config(self) -> None:
        streams = self.streams()
        api_port = self.api_port
        webrtc_port = self.webrtc_port
        candidate = self.config.get("go2rtc", "candidate_ip", default="") or ""
        candidates = []
        if candidate:
            candidates.append(f"{candidate}:{webrtc_port}")
        else:
            for ip in all_ips():
                candidates.append(f"{ip}:{webrtc_port}")

        lines = [
            "# Generado automáticamente por vstarcam. No editar a mano.",
            "log:",
            "  level: info",
        ]
        ffmpeg_bin = which("ffmpeg")
        if ffmpeg_bin:
            lines += ["ffmpeg:", f"  bin: {_yaml_str(ffmpeg_bin)}"]
        lines += [
            "api:",
            f'  listen: "127.0.0.1:{api_port}"',
            "rtsp:",
            f'  listen: "127.0.0.1:{api_port + 1}"',
            "webrtc:",
            f'  listen: ":{webrtc_port}"',
        ]
        if candidates:
            lines.append("  candidates:")
            lines.extend(f"    - {_yaml_str(c)}" for c in candidates)
        lines.append("streams:")
        if not streams:
            lines.append("  {}")
        for name, url in streams.items():
            lines.append(f"  {name}: {_yaml_str(url)}")
        lines.append("")
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_PATH)

    def start(self) -> bool:
        if not self.enabled():
            if not self.available:
                logger.warning(
                    "go2rtc no está instalado; el vídeo usará modo de instantáneas. "
                    "Ejecuta ./setup.sh para habilitar WebRTC."
                )
            return False
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return True
            self.write_config()
            cmd = [self.binary, "-config", str(CONFIG_PATH)]
            logger.info("Arrancando go2rtc: %s", " ".join(cmd))
            log_path = DATA / "logs" / "go2rtc.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "ab", buffering=0)
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            ready = self._wait_ready(12)
            if not ready:
                logger.error("go2rtc no respondió en el puerto %s", self.api_port)
                self.last_error = f"go2rtc no respondió en el puerto {self.api_port}"
            else:
                self.last_error = ""
                logger.info("go2rtc listo en %s", self.api_base)
            return ready

    def _wait_ready(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(self.api_base + "/api", timeout=1.5) as resp:
                    if resp.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.35)
        return False

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def stop(self) -> None:
        with self._lock:
            proc = self.proc
            self.proc = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def reload_streams(self) -> None:
        """Aplica cambios de cámaras: reescribe config y reinicia si hace falta."""
        if not self.enabled():
            return
        with self._lock:
            running = bool(self.proc and self.proc.poll() is None)
        if running:
            self.restart()
        else:
            self.start()

    def watchdog(self) -> None:
        while not self._stop.wait(10):
            if not self.enabled():
                continue
            with self._lock:
                proc = self.proc
            if proc is None or proc.poll() is not None:
                logger.warning("go2rtc se ha parado, reiniciando…")
                self.start()

    def start_watchdog(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.watchdog, name="go2rtc-watchdog", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self.stop()

    # ------------------------------------------------------------------ API
    def _api(self, path: str, method: str = "GET", data: bytes | None = None,
             content_type: str = "application/json", timeout: float = 8.0) -> bytes:
        req = urllib.request.Request(
            self.api_base + path,
            data=data,
            method=method,
            headers={"Content-Type": content_type} if data is not None else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def add_stream(self, name: str, url: str) -> bool:
        if not self.enabled():
            return False
        query = urllib.parse.urlencode({"name": name, "src": url})
        try:
            self._api(f"/api/streams?{query}", method="PUT")
            return True
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("No se pudo añadir stream %s: %s", name, exc)
            return False

    def sync_streams(self, old: dict[str, str], new: dict[str, str]) -> bool:
        """Añade/actualiza/borra streams sin reiniciar go2rtc (no corta el resto)."""
        if not self.enabled():
            return False
        try:
            for name in old:
                if name not in new:
                    self._api(
                        "/api/streams?" + urllib.parse.urlencode({"src": name}), method="DELETE"
                    )
            for name, url in new.items():
                if old.get(name) == url:
                    continue
                query = urllib.parse.urlencode({"name": name, "src": url})
                self._api(
                    f"/api/streams?{query}", method="PATCH" if name in old else "PUT"
                )
            self.write_config()
            return True
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("Actualización en caliente falló (%s); reiniciando go2rtc", exc)
            self.restart()
            return False

    def webrtc_offer(self, name: str, offer: str, video: bool = True, audio: bool = True) -> str:
        """Proxy del SDP hacia go2rtc (así go2rtc queda solo en localhost)."""
        query = urllib.parse.urlencode({"src": name})
        return self._api(
            f"/api/webrtc?{query}",
            method="POST",
            data=offer.encode("utf-8"),
            content_type="application/sdp",
            timeout=20,
        ).decode("utf-8", "replace")

    def frame_jpeg(self, name: str, timeout: float = 6.0) -> bytes | None:
        if not self.enabled():
            return None
        try:
            return self._api(
                f"/api/frame.jpeg?{urllib.parse.urlencode({'src': name})}",
                timeout=timeout,
            )
        except (urllib.error.URLError, OSError):
            return None

    def info(self) -> dict:
        try:
            data = json.loads(self._api("/api/streams", timeout=3))
            return {"ok": True, "streams": list(data.keys()) if isinstance(data, dict) else []}
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc)}
