"""Configuración persistente (JSON, sin dependencias externas)."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

from .util import CONFIG_PATH, load_json, save_json, slug

DEFAULT_CAMERA: dict[str, Any] = {
    "id": "",
    "name": "",
    "enabled": True,
    "host": "",
    "username": "admin",
    "password": "",
    "onvif_port": 80,
    "onvif_path": "/onvif/device_service",
    "ptz_driver": "",  # "" = auto (ONVIF y, si falla, CGI) | "onvif" | "cgi"
    "cgi_port": 0,     # 0 = deducir (81 en cámaras VStarcam)
    "rtsp_url": "",
    "snapshot_url": "",
    "profile_token": "",
    "ptz_profile_token": "",
    "audio": True,
    "motion": {
        "enabled": False,
        "threshold": 1.5,
        "cooldown": 30,
        "min_area": 0.5,
    },
    "record": {
        "mode": "off",  # off | motion | continuous
        "segment_seconds": 300,
        "clip_seconds": 15,
    },
    "analytics": {
        "objects": True,       # etiquetar personas/objetos (si hay numpy+onnxruntime)
        "min_confidence": 0.45,
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "password": "",
    },
    "go2rtc": {
        "api_port": 1984,
        "webrtc_port": 8555,
        "candidate_ip": "",
        "enabled": True,
    },
    "storage": {
        "keep_days": 14,
        "max_gb": 100,
    },
    "cloud": {
        "enabled": False,
        "type": "webdav",  # webdav | rclone
        "url": "",
        "username": "",
        "password": "",
        "folder": "vstarcam",
        "rclone_remote": "",
        "delete_after_upload": False,
    },
    "alerts": {
        "ntfy": {"enabled": False, "url": "https://ntfy.sh", "topic": ""},
        "telegram": {"enabled": False, "token": "", "chat_id": ""},
        "webhook": "",
    },
    "analytics": {
        "enabled": True,
        "model": "",             # ruta alternativa al .onnx (vacío = data/models)
        "auto_download": True,   # descarga el modelo la primera vez
        "min_confidence": 0.45,
        "max_objects": 6,
    },
    "cameras": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def normalize_camera(cam: dict, index: int = 0) -> dict:
    cam = _merge(DEFAULT_CAMERA, cam or {})
    if not cam.get("id"):
        cam["id"] = slug(cam.get("name") or cam.get("host") or f"cam{index + 1}", f"cam{index + 1}")
    cam["id"] = slug(cam["id"], f"cam{index + 1}")
    if not cam.get("name"):
        cam["name"] = cam["id"]
    return cam


class Config:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict:
        raw = load_json(self.path, {})
        cfg = _merge(DEFAULT_CONFIG, raw)
        cfg["cameras"] = [normalize_camera(c, i) for i, c in enumerate(cfg.get("cameras") or [])]
        seen: set[str] = set()
        for cam in cfg["cameras"]:
            base = cam["id"]
            n = 2
            while cam["id"] in seen:
                cam["id"] = f"{base}-{n}"
                n += 1
            seen.add(cam["id"])
        if not self.path.exists():
            save_json(self.path, cfg)
        return cfg

    # ------------------------------------------------------------------ API
    def get(self, *keys: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return copy.deepcopy(node)

    def save(self) -> None:
        with self._lock:
            save_json(self.path, self.data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            allowed = {k: v for k, v in (patch or {}).items() if k in DEFAULT_CONFIG and k != "cameras"}
            self.data = _merge(self.data, allowed)
            self.save()
            return self.data

    # -------------------------------------------------------------- Cámaras
    def cameras(self, only_enabled: bool = False) -> list[dict]:
        with self._lock:
            cams = copy.deepcopy(self.data.get("cameras") or [])
        return [c for c in cams if c.get("enabled", True)] if only_enabled else cams

    def camera(self, cam_id: str) -> dict | None:
        for cam in self.cameras():
            if cam["id"] == cam_id:
                return cam
        return None

    def upsert_camera(self, cam: dict) -> dict:
        with self._lock:
            cams = self.data.setdefault("cameras", [])
            current = None
            for i, existing in enumerate(cams):
                if existing["id"] == cam.get("id"):
                    current = existing
                    break
            merged = normalize_camera(_merge(current or {}, cam), len(cams))
            if current is None:
                cams.append(merged)
            else:
                idx = cams.index(current)
                cams[idx] = merged
            self.save()
            return copy.deepcopy(merged)

    def delete_camera(self, cam_id: str) -> bool:
        with self._lock:
            cams = self.data.setdefault("cameras", [])
            before = len(cams)
            self.data["cameras"] = [c for c in cams if c["id"] != cam_id]
            if len(self.data["cameras"]) != before:
                self.save()
                return True
            return False
