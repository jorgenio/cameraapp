"""Detección de movimiento sin dependencias: diff de fotogramas en gris."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from array import array

from . import ffmpeg

logger = logging.getLogger("vstarcam.motion")

WIDTH = 160
HEIGHT = 90
FRAME_BYTES = WIDTH * HEIGHT
PIXEL_DELTA = 22  # diferencia mínima por píxel para contar como cambio


def _read_exact(stream, n: int) -> bytes | None:
    buf = bytearray(n)
    view = memoryview(buf)
    pos = 0
    while pos < n:
        chunk = stream.read(n - pos)
        if not chunk:
            return None
        view[pos : pos + len(chunk)] = chunk
        pos += len(chunk)
    return bytes(buf)


class MotionDetector:
    """Un hilo por cámara. Llama a `on_event(cam, score)` cuando hay movimiento."""

    def __init__(self, cam: dict, on_event) -> None:
        self.cam = cam
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.last_score = 0.0
        self.last_event = 0.0
        self.running = False
        self.last_error = ""

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"motion-{self.cam['id']}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        if self._thread:
            self._thread.join(timeout=5)
        self.running = False

    def status(self) -> dict:
        return {
            "running": self.running,
            "score": round(self.last_score, 2),
            "last_event": self.last_event,
            "error": self.last_error,
        }

    # --------------------------------------------------------------- interno
    def _cmd(self) -> list[str]:
        url = self.cam.get("rtsp_url") or ""
        fps = 2
        cmd = [ffmpeg.ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin"]
        if url.lower().startswith("rtsp"):
            cmd += ["-rtsp_transport", "tcp", "-timeout", "10000000"]
        cmd += [
            "-i", url,
            "-an",
            "-vf", f"fps={fps},scale={WIDTH}:{HEIGHT},format=gray",
            "-f", "rawvideo",
            "-pix_fmt", "gray",
            "-",
        ]
        return cmd

    def _loop(self) -> None:
        self.running = True
        motion_cfg = self.cam.get("motion") or {}
        threshold = float(motion_cfg.get("threshold", 1.5))
        cooldown = float(motion_cfg.get("cooldown", 30))
        backoff = 6.0
        while not self._stop.is_set():
            try:
                self.proc = subprocess.Popen(
                    self._cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
            except OSError as exc:
                self.last_error = str(exc)
                logger.error("No se pudo iniciar detector de %s: %s", self.cam["id"], exc)
                return
            self.last_error = ""
            self._last_frame = time.monotonic()
            threading.Thread(
                target=self._watchdog, args=(self.proc,),
                name=f"motion-wd-{self.cam['id']}", daemon=True,
            ).start()

            previous: bytes | None = None
            while not self._stop.is_set():
                frame = _read_exact(self.proc.stdout, FRAME_BYTES)
                if frame is None:
                    break
                self._last_frame = time.monotonic()
                if previous is not None:
                    score = self._diff(previous, frame)
                    self.last_score = score
                    now = time.time()
                    if score >= threshold and now - self.last_event >= cooldown:
                        self.last_event = now
                        try:
                            self.on_event(self.cam, score)
                        except Exception:  # noqa: BLE001
                            logger.exception("Error en callback de movimiento")
                previous = frame
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
            self.proc = None
            if self._stop.is_set():
                break
            # Si la cámara está caída reintentamos con espera creciente.
            self._stop.wait(backoff)
            backoff = min(backoff * 1.5, 60)
        self.running = False

    def _watchdog(self, proc: subprocess.Popen) -> None:
        """Si el stream se queda congelado, reinicia ffmpeg."""
        while proc.poll() is None and not self._stop.is_set():
            self._stop.wait(3)
            if time.monotonic() - getattr(self, "_last_frame", 0) > 20:
                logger.warning("Detector de %s congelado, reiniciando", self.cam["id"])
                try:
                    proc.kill()
                except OSError:
                    pass
                return

    @staticmethod
    def _diff(a: bytes, b: bytes) -> float:
        """Porcentaje de píxeles que han cambiado de forma apreciable."""
        changed = 0
        aa = array("B", a)
        bb = array("B", b)
        for x, y in zip(aa, bb):
            d = x - y
            if d > PIXEL_DELTA or d < -PIXEL_DELTA:
                changed += 1
        return changed * 100.0 / FRAME_BYTES
