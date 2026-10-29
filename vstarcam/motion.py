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
QUIET_SECONDS = 1.5  # sin movimiento este tiempo → se cierra el episodio


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

    def __init__(self, cam: dict, on_event, on_end=None) -> None:
        self.cam = cam
        self.on_event = on_event
        self.on_end = on_end
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
            active = False
            episode_start = 0.0
            last_active = 0.0
            peak = 0.0
            bbox = None
            frames = 0
            event_id = None
            while not self._stop.is_set():
                frame = _read_exact(self.proc.stdout, FRAME_BYTES)
                if frame is None:
                    break
                self._last_frame = time.monotonic()
                if previous is not None:
                    score, changed = self._diff(previous, frame)
                    self.last_score = score
                    now = time.time()
                    if score >= threshold:
                        frames += 1
                        if not active:
                            active = True
                            episode_start = now
                            peak = score
                            bbox = changed
                            event_id = None
                            if now - self.last_event >= cooldown:
                                self.last_event = now
                                try:
                                    event_id = self.on_event(
                                        self.cam, score,
                                        {"bbox": bbox, "duration": 0.0},
                                    )
                                except Exception:  # noqa: BLE001
                                    logger.exception("Error en callback de movimiento")
                        else:
                            peak = max(peak, score)
                            bbox = _union(bbox, changed)
                        last_active = now
                    elif active and now - last_active >= QUIET_SECONDS:
                        duration = max(0.0, last_active - episode_start)
                        if event_id and self.on_end:
                            try:
                                self.on_end(
                                    self.cam, event_id,
                                    {
                                        "duration": duration,
                                        "peak": peak,
                                        "bbox": bbox,
                                        "frames": frames,
                                    },
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception("Error al cerrar evento de movimiento")
                        active = False
                        event_id = None
                        bbox = None
                        frames = 0
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
    def _diff(a: bytes, b: bytes) -> tuple[float, list[float] | None]:
        """Porcentaje de píxeles cambiados y caja que los engloba (0..1)."""
        changed = 0
        x0, y0, x1, y1 = WIDTH, HEIGHT, 0, 0
        aa = array("B", a)
        bb = array("B", b)
        for i, (x, y) in enumerate(zip(aa, bb)):
            d = x - y
            if d > PIXEL_DELTA or d < -PIXEL_DELTA:
                changed += 1
                px = i % WIDTH
                py = i // WIDTH
                if px < x0:
                    x0 = px
                if px > x1:
                    x1 = px
                if py < y0:
                    y0 = py
                if py > y1:
                    y1 = py
        score = changed * 100.0 / FRAME_BYTES
        if not changed:
            return score, None
        return score, [
            round(x0 / WIDTH, 3),
            round(y0 / HEIGHT, 3),
            round((x1 + 1) / WIDTH, 3),
            round((y1 + 1) / HEIGHT, 3),
        ]


def _union(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    if not a:
        return b
    if not b:
        return a
    return [
        min(a[0], b[0]), min(a[1], b[1]),
        max(a[2], b[2]), max(a[3], b[3]),
    ]
