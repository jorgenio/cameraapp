"""Subida automática a la nube: WebDAV, rclone o MEGAcmd."""

from __future__ import annotations

import base64
import logging
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import DATA, which

logger = logging.getLogger("vstarcam.cloud")


class CloudUploader:
    def __init__(self, config, db) -> None:
        self.config = config
        self.db = db
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self.last_status = ""

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="cloud", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=30)
            self._wake.clear()
            if self._stop.is_set():
                break
            if not self.config.get("cloud", "enabled", default=False):
                continue
            items = self.db.pending_uploads(limit=5)
            for item in items:
                if self._stop.is_set():
                    break
                path = Path(item["path"])
                if not path.exists():
                    self.db.set_upload_status(item["id"], "missing", "fichero no existe")
                    continue
                try:
                    self._upload_file(path)
                except Exception as exc:  # noqa: BLE001
                    self.last_status = f"error: {exc}"
                    logger.warning("Subida falló %s: %s", path.name, exc)
                    self.db.set_upload_status(item["id"], "pending", str(exc))
                    break  # no insistas con el resto ahora mismo
                else:
                    self.last_status = f"subido {path.name}"
                    logger.info("Subido a la nube: %s", path.name)
                    self.db.set_upload_status(item["id"], "done")
                    if self.config.get("cloud", "delete_after_upload", default=False):
                        try:
                            path.unlink()
                        except OSError:
                            pass

    def enqueue(self, path: Path, kind: str = "recording") -> None:
        if not self.config.get("cloud", "enabled", default=False):
            return
        self.db.enqueue_upload(str(path), kind)
        self.notify()

    # ---------------------------------------------------------------- upload
    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(DATA.resolve()))
        except ValueError:
            return path.name

    def _remote_path(self, path: Path) -> str:
        folder = (self.config.get("cloud", "folder", default="vstarcam") or "").strip("/")
        rel = self._relative(path)
        return f"{folder}/{rel}" if folder else rel

    def _upload_file(self, path: Path) -> None:
        ctype = self.config.get("cloud", "type", default="webdav")
        if ctype == "rclone":
            self._upload_rclone(path)
        elif ctype == "megacmd":
            self._upload_megacmd(path)
        else:
            self._upload_webdav(path)

    def _upload_rclone(self, path: Path) -> None:
        exe = which("rclone")
        if not exe:
            raise RuntimeError("rclone no está instalado (ejecuta ./setup.sh)")
        remote = (self.config.get("cloud", "rclone_remote", default="") or "").strip()
        if not remote:
            raise RuntimeError("Configura 'rclone_remote' (p. ej. minkube:Nextcloud/vstarcam)")

        target = remote.rstrip("/") + "/" + self._relative(path)
        proc = subprocess.run(
            [exe, "copyto", str(path), target, "--stats-one-line", "--log-level", "ERROR"],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip()[-300:] or "rclone falló")

    # ------------------------------------------------------------- MEGAcmd
    def _mega_argv(self, command: str, *args: str) -> list[str] | None:
        """Construye la orden usando mega-<cmd> o mega-exec <cmd>."""
        exe = which(f"mega-{command}")
        if exe:
            return [exe, *args]
        exe = which("mega-exec")
        if exe:
            return [exe, command, *args]
        return None

    def _mega_run(self, command: str, *args: str, timeout: float = 60,
                  check: bool = True) -> subprocess.CompletedProcess:
        argv = self._mega_argv(command, *args)
        if not argv:
            raise RuntimeError("MEGAcmd no está instalado (paquete 'megacmd')")
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"mega-{command} tardó demasiado en responder") from exc
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-300:]
            raise RuntimeError(detail or f"mega-{command} falló (código {proc.returncode})")
        return proc

    def _mega_mkdir(self, remote_dir: str) -> None:
        if not self._mega_argv("mkdir"):
            return
        proc = self._mega_run("mkdir", "-p", remote_dir, timeout=180, check=False)
        if proc.returncode == 0:
            return
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        # MEGAcmd devuelve error si la carpeta ya existe; no es un fallo real.
        if "already exists" in detail.lower():
            return
        raise RuntimeError(detail or f"mega-mkdir falló (código {proc.returncode})")

    def _mega_logged_in(self) -> bool:
        argv = self._mega_argv("whoami")
        if not argv:
            return False
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0

    def _mega_ensure_login(self) -> None:
        if self._mega_logged_in():
            return
        email = str(self.config.get("cloud", "mega_email", default="") or "").strip()
        password = str(self.config.get("cloud", "mega_password", default="") or "")
        if not email or not password:
            raise RuntimeError(
                "MEGAcmd no ha iniciado sesión. Configura el email y la contraseña "
                "de MEGA, o ejecuta 'mega-login' una vez en una terminal."
            )
        self._mega_run("login", email, password, timeout=180)

    def _mega_remote_dir(self, path: Path) -> str:
        folder = (self.config.get("cloud", "folder", default="vstarcam") or "").strip("/")
        parent = Path(self._relative(path)).parent
        parts = [p for p in [folder, *parent.parts] if p and p != "."]
        return "/" + "/".join(parts)

    def _upload_megacmd(self, path: Path) -> None:
        self._mega_ensure_login()
        remote_dir = self._mega_remote_dir(path)
        self._mega_mkdir(remote_dir)
        self._mega_run("put", "-c", str(path), remote_dir, timeout=1800)

    def _upload_webdav(self, path: Path) -> None:
        base = (self.config.get("cloud", "url", default="") or "").strip()
        if not base:
            raise RuntimeError("Configura la URL WebDAV")
        base = base.rstrip("/")
        user = self.config.get("cloud", "username", default="") or ""
        password = self.config.get("cloud", "password", default="") or ""
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()

        remote_path = self._remote_path(path)
        # Crea la jerarquía de carpetas (MKCOL).
        parts = remote_path.split("/")[:-1]
        current = base
        for part in parts:
            current = f"{current}/{urllib.parse.quote(part)}"
            self._request("MKCOL", current, auth, allow=(201, 405, 301))

        url = f"{base}/{urllib.parse.quote(remote_path)}"
        content_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "video/mp4"
        self._request("PUT", url, auth, data=path.read_bytes(), content_type=content_type,
                      timeout=1800, expect=(200, 201, 204))

    def _request(self, method: str, url: str, auth: str, data: bytes | None = None,
                 content_type: str = "", timeout: float = 15.0,
                 allow: tuple = (), expect: tuple = ()) -> None:
        headers = {"Authorization": f"Basic {auth}"}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(256)
                if expect and resp.status not in expect:
                    raise RuntimeError(f"HTTP {resp.status} en {method} {url}")
        except urllib.error.HTTPError as exc:
            if exc.code in allow:
                return
            raise RuntimeError(f"HTTP {exc.code} en {method} {url}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"No se pudo conectar a {url}: {exc.reason}") from None

    # ------------------------------------------------------------------ test
    def test(self) -> dict:
        cfg_type = self.config.get("cloud", "type", default="webdav")
        if cfg_type == "rclone":
            exe = which("rclone")
            if not exe:
                return {"ok": False, "error": "rclone no instalado"}

            remote = (self.config.get("cloud", "rclone_remote", default="") or "").strip()
            if not remote:
                return {"ok": False, "error": "falta rclone_remote"}
            proc = subprocess.run(
                [exe, "lsd", remote.split(":")[0] + ":"], capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                return {"ok": False, "error": (proc.stderr or "").strip()[-300:]}
            return {"ok": True, "message": "rclone accesible"}

        if cfg_type == "megacmd":
            if not self._mega_argv("whoami"):
                return {"ok": False, "error": "MEGAcmd no instalado (paquete 'megacmd')"}
            try:
                self._mega_ensure_login()
                folder = (self.config.get("cloud", "folder", default="vstarcam") or "").strip("/")
                self._mega_mkdir(f"/{folder}")
                return {"ok": True, "message": "MEGAcmd conectado y carpeta creada"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

        url = (self.config.get("cloud", "url", default="") or "").rstrip("/")
        if not url:
            return {"ok": False, "error": "falta la URL WebDAV"}
        user = self.config.get("cloud", "username", default="") or ""
        password = self.config.get("cloud", "password", default="") or ""
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        folder = (self.config.get("cloud", "folder", default="vstarcam") or "").strip("/")
        try:
            self._request("MKCOL", f"{url}/{urllib.parse.quote(folder)}", auth, allow=(201, 405, 301))
            return {"ok": True, "message": "WebDAV accesible y carpeta creada"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
