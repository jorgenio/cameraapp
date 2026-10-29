"""Utilidades comunes: rutas, logging, ficheros y red."""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def _get_root() -> Path:
    """Detecta la raíz tanto en desarrollo como empaquetado con PyInstaller."""
    if getattr(sys, "frozen", False):
        # PyInstaller: onefile usa _MEIPASS, onedir usa el directorio del exe
        if hasattr(sys, "_MEIPASS"):
            meipass = Path(sys._MEIPASS)
            # En onedir también puede estar junto al ejecutable
            exe_dir = Path(sys.executable).parent
            # Preferir _MEIPASS si contiene web/
            if (meipass / "web").exists() or (meipass / "vstarcam").exists():
                return meipass
            if (exe_dir / "web").exists():
                return exe_dir
            return meipass
        exe_dir = Path(sys.executable).parent
        return exe_dir
    return Path(__file__).resolve().parent.parent


ROOT = _get_root()
BIN = ROOT / "bin"
WEB = ROOT / "web"
# En modo empaquetado, guardar datos junto al ejecutable
if getattr(sys, "frozen", False):
    DATA = Path(sys.executable).parent / "data"
else:
    DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
RECORDINGS = DATA / "recordings"
MODELS = DATA / "models"
CONFIG_PATH = DATA / "config.json" if getattr(sys, "frozen", False) else ROOT / "config.json"
LOG_DIR = DATA / "logs"

logger = logging.getLogger("vstarcam")


def setup_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(level)
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler(LOG_DIR / "vstarcam.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


def ensure_dirs() -> None:
    for d in (BIN, DATA, SNAPSHOTS, RECORDINGS, MODELS, LOG_DIR, WEB):
        d.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("No se pudo leer %s: %s", path, exc)
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def slug(text: str, fallback: str = "cam") -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    out = "".join(c if (c.isalnum() or c in "-_") else "-" for c in normalized).strip("-").lower()
    return out or fallback


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def which(name: str) -> str | None:
    """Busca un binario en bin/ y luego en el PATH."""
    candidate = BIN / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(name)


def local_ip() -> str:
    """IP de la interfaz principal (la que se usa para salir a la red)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def all_ips() -> list[str]:
    """Todas las IPv4 locales (sin loopback), útil para candidatos ICE."""
    ips: list[str] = []
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=3
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 3 and parts[2] == "inet":
                addr, _, iface = parts[3].partition("/")
                if addr.startswith("127.") or iface == "lo":
                    continue
                if addr not in ips:
                    ips.append(addr)
    except (OSError, subprocess.SubprocessError):
        pass
    if not ips:
        ips.append(local_ip())
    return ips


def port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


class Throttle:
    """Evita ejecutar una acción más de una vez cada N segundos."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._last = 0.0
        self._lock = threading.Lock()

    def ready(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if now - self._last >= self.seconds:
                self._last = now
                return True
            return False


def free_port(start: int, end: int) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No hay puertos libres entre {start} y {end}")


def temp_file(suffix: str = "") -> str:
    fd, name = tempfile.mkstemp(suffix=suffix, dir=str(DATA))
    os.close(fd)
    return name
