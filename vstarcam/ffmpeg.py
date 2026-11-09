"""Envoltorio de ffmpeg: probar streams, capturas y grabación."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .util import LOG_DIR, RECORDINGS, ensure_dirs, which

logger = logging.getLogger("vstarcam.ffmpeg")


def ffmpeg() -> str:
    path = which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg no encontrado. Ejecuta ./setup.sh para descargarlo en bin/."
        )
    return path


def ffprobe() -> str | None:
    return which("ffprobe")


def probe(url: str, timeout: float = 15.0) -> dict:
    """Comprueba si un stream RTSP/HTTP es accesible y devuelve sus datos."""
    exe = ffprobe()
    if not exe:
        return {"ok": False, "error": "ffprobe no disponible"}
    cmd = [exe, "-v", "error"]
    if url.lower().startswith("rtsp"):
        cmd += ["-rtsp_transport", "tcp", "-timeout", "8000000"]
    else:
        cmd += ["-rw_timeout", "8000000"]
    cmd += ["-print_format", "json", "-show_streams", "-show_format", url]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Tiempo de espera agotado (¿cámara apagada o IP incorrecta?)"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        msg = err[-1] if err else "stream no accesible"
        if "401" in msg or "Unauthorized" in msg:
            msg = "Credenciales incorrectas (401 Unauthorized)"
        elif "404" in msg:
            msg = "Ruta RTSP no existe (404)"
        elif "timed out" in msg.lower():
            msg = "Tiempo de espera agotado"
        return {"ok": False, "error": msg}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "Respuesta no válida de ffprobe"}
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video:
        return {"ok": False, "error": "El stream no contiene vídeo"}
    return {
        "ok": True,
        "codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": video.get("r_frame_rate"),
        "audio": bool(audio),
        "audio_codec": audio.get("codec_name") if audio else None,
        "url": url,
    }


def snapshot(url: str, out_path: Path, width: int = 1600, timeout: float = 15.0) -> bool:
    """Captura un JPEG del stream."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
    ]
    if url.lower().startswith("rtsp"):
        cmd += ["-rtsp_transport", "tcp", "-timeout", "8000000"]
    cmd += [
        "-i", url,
        "-frames:v", "1",
        "-vf", f"scale='min({width},iw)':-2",
        "-q:v", "3",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Snapshot falló (%s): %s", out_path.name, exc)
        return False
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 200:
        logger.warning(
            "Snapshot falló (%s): %s",
            out_path.name,
            (proc.stderr or b"").decode("utf-8", "replace").strip()[-300:],
        )
        return False
    return True


class Recorder:
    """Grabación continua por segmentos o clips puntuales."""

    def __init__(self, cam: dict) -> None:
        self.cam = cam
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._log_file = None
        self._log_path: Path | None = None
        self.started_at: float | None = None
        self.current_dir: Path | None = None

    @property
    def url(self) -> str:
        return self.cam.get("rtsp_url") or ""

    def _base_cmd(self, interactive: bool = False) -> list[str]:
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "warning"]
        if not interactive:
            cmd.append("-nostdin")
        if self.url.lower().startswith("rtsp"):
            # Estas cámaras repiten DTS; con marcas de reloj la duración del
            # MP4 coincide con el tiempo real y los saltos desaparecen.
            cmd += [
                "-use_wallclock_as_timestamps", "1",
                "-rtsp_transport", "tcp",
                "-timeout", "10000000",
            ]
        cmd += ["-i", self.url, "-map", "0:v:0"]
        if self.cam.get("audio", True):
            cmd += ["-map", "0:a:0?"]
        return cmd

    def _codec_args(self) -> list[str]:
        """Copia el vídeo y convierte el audio a AAC.

        El MP4 no admite G.711 (pcm_alaw/pcm_mulaw), que es el audio habitual
        de estas cámaras: con ``-c copy`` ffmpeg abortaba al escribir la
        cabecera y dejaba ficheros de 0 bytes.
        """
        if not self.cam.get("audio", True):
            return ["-c:v", "copy", "-an"]
        return [
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "64k", "-ar", "16000", "-ac", "1",
            "-af", "aresample=async=1:first_pts=0",
        ]

    def start_continuous(self, segment_seconds: int = 300) -> Path:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return self.current_dir or RECORDINGS
            day = time.strftime("%Y-%m-%d")
            out_dir = RECORDINGS / self.cam["id"] / day
            out_dir.mkdir(parents=True, exist_ok=True)
            self.current_dir = out_dir
            pattern = str(out_dir / f"{self.cam['id']}_%Y%m%d_%H%M%S.mp4")
            cmd = self._base_cmd(interactive=True) + self._codec_args() + [
                "-f", "segment",
                "-segment_time", str(max(10, segment_seconds)),
                "-segment_format", "mp4",
                "-reset_timestamps", "1",
                "-strftime", "1",
                "-movflags", "+faststart",
                pattern,
            ]
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._log_path = LOG_DIR / f"record_{self.cam['id']}.log"
            self._log_file = open(self._log_path, "wb")
            logger.info("Grabación continua %s → %s", self.cam["id"], out_dir)
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._log_file
            )
            self.started_at = time.time()
            threading.Thread(target=self._watch, args=(self.proc,), daemon=True).start()
            return out_dir

    def _watch(self, proc: subprocess.Popen) -> None:
        code = proc.wait()
        with self._lock:
            if self.proc is proc:
                self.proc = None
                self.started_at = None
        tail = ""
        with self._lock:
            if self._log_file:
                try:
                    self._log_file.close()
                except OSError:
                    pass
                self._log_file = None
            if self._log_path:
                try:
                    tail = self._log_path.read_text("utf-8", "replace").strip()[-400:]
                except OSError:
                    tail = ""
        if code not in (0, 255):
            logger.warning(
                "ffmpeg de grabación de %s terminó con código %s%s",
                self.cam["id"], code, f": {tail}" if tail else "",
            )
        if self.current_dir:
            self._cleanup_empty(self.current_dir)

    @staticmethod
    def _cleanup_empty(folder: Path) -> None:
        """Borra los MP4 de 0 bytes que deja ffmpeg cuando falla al arrancar."""
        try:
            for f in folder.glob("*.mp4"):
                if f.stat().st_size == 0:
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    def stop(self) -> None:
        with self._lock:
            proc = self.proc
            self.proc = None
            self.started_at = None
        if proc and proc.poll() is None:
            # "q" cierra el segmento actual y escribe el moov; SIGINT puede
            # hacer que ffmpeg salga sin finalizar el MP4.
            try:
                if proc.stdin:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    proc.send_signal(2)
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    proc.kill()
            except (BrokenPipeError, OSError, ValueError):
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def is_running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def clip(self, seconds: int = 15, tag: str = "motion") -> Path | None:
        """Graba un clip corto (sin pre-roll)."""
        day = time.strftime("%Y-%m-%d")
        out_dir = RECORDINGS / self.cam["id"] / day
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{self.cam['id']}_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        cmd = self._base_cmd() + self._codec_args() + [
            "-t", str(max(2, seconds)),
            "-movflags", "+faststart",
            "-y", str(out),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 30)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("Clip falló %s: %s", self.cam["id"], exc)
            return None
        if out.exists() and out.stat().st_size > 1000:
            return out
        detail = (proc.stderr or "").strip().splitlines()
        logger.warning(
            "Clip vacío %s: %s",
            self.cam["id"],
            detail[-1] if detail else f"código {proc.returncode}",
        )
        out.unlink(missing_ok=True)
        return None


def disk_usage(path: Path) -> dict:
    ensure_dirs()
    try:
        usage = shutil.disk_usage(str(path if path.exists() else RECORDINGS))
    except OSError:
        return {"total": 0, "used": 0, "free": 0}
    return {"total": usage.total, "used": usage.total - usage.free, "free": usage.free}
