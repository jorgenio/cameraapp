"""Avisos por Telegram, ntfy o webhook genérico (sin dependencias)."""

from __future__ import annotations

import json
import logging
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

logger = logging.getLogger("vstarcam.alerts")


def _multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----vstarcam{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for name, path in files.items():
        if not path or not Path(path).is_file():
            continue
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{name}"; filename="{Path(path).name}"\r\n'
        ).encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += Path(path).read_bytes()
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class Alerter:
    def __init__(self, config) -> None:
        self.config = config

    def send(self, title: str, message: str, image: Path | None = None) -> dict:
        cfg = self.config.get("alerts") or {}
        results = []
        ntfy = cfg.get("ntfy") or {}
        if ntfy.get("enabled") and ntfy.get("topic"):
            results.append(("ntfy", self._ntfy(ntfy, title, message)))
        tg = cfg.get("telegram") or {}
        if tg.get("enabled") and tg.get("token") and tg.get("chat_id"):
            results.append(("telegram", self._telegram(tg, title, message, image)))
        webhook = (cfg.get("webhook") or "").strip()
        if webhook:
            results.append(("webhook", self._webhook(webhook, title, message, image)))
        return {"results": [{"channel": c, "ok": ok, "error": err} for c, (ok, err) in results]}

    def enabled(self) -> bool:
        cfg = self.config.get("alerts") or {}
        ntfy = cfg.get("ntfy") or {}
        tg = cfg.get("telegram") or {}
        return bool(
            (ntfy.get("enabled") and ntfy.get("topic"))
            or (tg.get("enabled") and tg.get("token"))
            or (cfg.get("webhook") or "").strip()
        )

    # --------------------------------------------------------------- canales
    def _ntfy(self, cfg: dict, title: str, message: str) -> tuple[bool, str]:
        base = (cfg.get("url") or "https://ntfy.sh").rstrip("/")
        topic = cfg.get("topic")
        url = f"{base}/{urllib.parse.quote(topic, safe='')}"
        data = message.encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Title": title, "Tags": "camera", "Priority": "default"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read(64)
            return True, ""
        except (urllib.error.URLError, OSError) as exc:
            return False, str(exc)

    def _telegram(self, cfg: dict, title: str, message: str, image: Path | None) -> tuple[bool, str]:
        token = cfg.get("token")
        chat_id = str(cfg.get("chat_id"))
        text = f"*{title}*\n{message}"
        if image and Path(image).is_file():
            body, ctype = _multipart(
                {"chat_id": chat_id, "caption": text, "parse_mode": "Markdown"},
                {"photo": image},
            )
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
        else:
            body, ctype = _multipart({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, {})
            url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read() or b"{}")
            if not payload.get("ok", False):
                return False, str(payload.get("description", "Telegram rechazó el mensaje"))
            return True, ""
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            return False, str(exc)

    def _webhook(self, url: str, title: str, message: str, image: Path | None) -> tuple[bool, str]:
        payload = {
            "title": title,
            "message": message,
            "image": str(image) if image else None,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read(64)
            return True, ""
        except (urllib.error.URLError, OSError) as exc:
            return False, str(exc)
