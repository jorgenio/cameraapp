"""Análisis opcional de eventos: detección de objetos y descripciones.

La detección usa ONNX Runtime + NumPy, que son dependencias **opcionales**.
Si no están instaladas, el backend sigue funcionando con las descripciones
básicas (movimiento, zona y duración).

Modelo: SSD MobileNet v1 (COCO), Apache-2.0, del ONNX Model Zoo.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import threading
import urllib.request
from pathlib import Path

from . import ffmpeg
from .util import MODELS

logger = logging.getLogger("vstarcam.analytics")

MODEL_FILE = "ssd_mobilenet_v1.onnx"
MODEL_URL = (
    "https://github.com/onnx/models/raw/main/validated/vision/"
    "object_detection_segmentation/ssd-mobilenetv1/model/ssd_mobilenet_v1_10.onnx"
)
MODEL_SHA256 = "1fbcf47654165f2e0b5f1bdf3f123b9e9e1128cd6463717767b76ab4b5246f9a"
MODEL_MIN_BYTES = 25 * 1024 * 1024
MODEL_INPUT = 300

# Ids COCO que devuelve el modelo (0 = fondo, no aparece).
COCO_LABELS: dict[int, str] = {
    1: "person", 2: "bicycle", 3: "car", 4: "motorcycle", 5: "airplane",
    6: "bus", 7: "train", 8: "truck", 9: "boat", 10: "traffic light",
    11: "fire hydrant", 13: "stop sign", 14: "parking meter", 15: "bench",
    16: "bird", 17: "cat", 18: "dog", 19: "horse", 20: "sheep", 21: "cow",
    22: "elephant", 23: "bear", 24: "zebra", 25: "giraffe", 27: "backpack",
    28: "umbrella", 31: "handbag", 32: "tie", 33: "suitcase", 34: "frisbee",
    35: "skis", 36: "snowboard", 37: "sports ball", 38: "kite",
    39: "baseball bat", 40: "baseball glove", 41: "skateboard",
    42: "surfboard", 43: "tennis racket", 44: "bottle", 46: "wine glass",
    47: "cup", 48: "fork", 49: "knife", 50: "spoon", 51: "bowl",
    52: "banana", 53: "apple", 54: "sandwich", 55: "orange",
    56: "broccoli", 57: "carrot", 58: "hot dog", 59: "pizza", 60: "donut",
    61: "cake", 62: "chair", 63: "couch", 64: "potted plant", 65: "bed",
    67: "dining table", 70: "toilet", 72: "tv", 73: "laptop", 74: "mouse",
    75: "remote", 76: "keyboard", 77: "cell phone", 78: "microwave",
    79: "oven", 80: "toaster", 81: "sink", 82: "refrigerator", 84: "book",
    85: "clock", 86: "vase", 87: "scissors", 88: "teddy bear",
    89: "hair drier", 90: "toothbrush",
}

LABELS_ES: dict[str, str] = {
    "person": "persona", "bicycle": "bicicleta", "car": "coche",
    "motorcycle": "moto", "airplane": "avión", "bus": "autobús",
    "train": "tren", "truck": "camión", "boat": "barco",
    "traffic light": "semáforo", "fire hydrant": "hidrante",
    "stop sign": "señal de stop", "parking meter": "parquímetro",
    "bench": "banco", "bird": "pájaro", "cat": "gato", "dog": "perro",
    "horse": "caballo", "sheep": "oveja", "cow": "vaca",
    "elephant": "elefante", "bear": "oso", "zebra": "cebra",
    "giraffe": "jirafa", "backpack": "mochila", "umbrella": "paraguas",
    "handbag": "bolso", "tie": "corbata", "suitcase": "maleta",
    "sports ball": "balón", "bottle": "botella", "wine glass": "copa",
    "cup": "taza", "bowl": "cuenco", "banana": "plátano",
    "apple": "manzana", "sandwich": "bocadillo", "orange": "naranja",
    "broccoli": "brócoli", "carrot": "zanahoria", "pizza": "pizza",
    "donut": "dónut", "cake": "tarta", "chair": "silla",
    "couch": "sofá", "potted plant": "planta", "bed": "cama",
    "dining table": "mesa", "toilet": "inodoro", "tv": "televisor",
    "laptop": "portátil", "mouse": "ratón", "remote": "mando",
    "keyboard": "teclado", "cell phone": "móvil", "microwave": "microondas",
    "oven": "horno", "toaster": "tostadora", "sink": "fregadero",
    "refrigerator": "nevera", "book": "libro", "clock": "reloj",
    "vase": "jarrón", "scissors": "tijeras", "teddy bear": "peluche",
    "hair drier": "secador", "toothbrush": "cepillo de dientes",
}

PLURALS: dict[str, str] = {
    "persona": "personas", "coche": "coches", "autobús": "autobuses",
    "camión": "camiones", "moto": "motos",
    "bicicleta": "bicicletas", "perro": "perros", "gato": "gatos",
    "pájaro": "pájaros", "caballo": "caballos", "vaca": "vacas",
    "oveja": "ovejas",
}

# Categorías para filtrar en la interfaz.
CATEGORY_PERSON = "persona"
CATEGORY_VEHICLE = "vehiculo"
CATEGORY_ANIMAL = "animal"
CATEGORY_OBJECT = "objeto"
CATEGORY_MOTION = "movimiento"

_VEHICLES = {
    "coche", "bicicleta", "moto", "autobús", "camión", "tren", "barco",
    "avión",
}
_ANIMALS = {
    "pájaro", "gato", "perro", "caballo", "oveja", "vaca", "elefante",
    "oso", "cebra", "jirafa",
}


def label_es(name: str) -> str:
    return LABELS_ES.get((name or "").lower(), name or "")


def _plural(label: str, count: int) -> str:
    if count <= 1:
        return label
    if label in PLURALS:
        return PLURALS[label]
    if label.endswith(("a", "e", "i", "o", "u", "á", "é", "í", "ó", "ú")):
        return label + "s"
    return label + "es"


def zone_from_bbox(bbox) -> str:
    """Zona de la imagen a partir de una caja normalizada [x0, y0, x1, y1]."""
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return ""
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    col = "izquierda" if cx < 0.34 else ("derecha" if cx > 0.66 else "centro")
    row = "arriba" if cy < 0.34 else ("abajo" if cy > 0.66 else "centro")
    if row == "centro" and col == "centro":
        return "centro"
    if row == "centro":
        return f"a la {col}"
    if col == "centro":
        return row
    return f"{row} a la {col}"


def event_category(detections: list[dict]) -> str:
    labels = {str(d.get("label") or "") for d in detections}
    if CATEGORY_PERSON in labels:
        return CATEGORY_PERSON
    if labels & _VEHICLES:
        return CATEGORY_VEHICLE
    if labels & _ANIMALS:
        return CATEGORY_ANIMAL
    if labels:
        return CATEGORY_OBJECT
    return CATEGORY_MOTION


def build_description(
    detections: list[dict], zone: str = "", motion_score: float = 0.0,
) -> str:
    """Frase corta en español para el evento."""
    if detections:
        counts: dict[str, int] = {}
        best: dict[str, float] = {}
        for det in detections:
            label = str(det.get("label") or "").strip() or "objeto"
            counts[label] = counts.get(label, 0) + 1
            best[label] = max(best.get(label, 0.0), float(det.get("score") or 0))
        ordered = sorted(
            counts, key=lambda name: (0 if name == "persona" else 1, -best[name])
        )
        parts = []
        for label in ordered[:3]:
            count = counts[label]
            name = _plural(label, count)
            parts.append(f"{count} {name}")
        text = "Detectado: " + ", ".join(parts)
        confidence = best[ordered[0]]
        if confidence:
            text += f" ({confidence * 100:.0f} %)"
    else:
        text = "Movimiento detectado"
        if motion_score:
            text += f" ({motion_score:.1f} % de la imagen)"
    if zone:
        text += f" en la zona {zone}"
    return text


class Analytics:
    """Detector de objetos opcional, con carga perezosa y en segundo plano."""

    def __init__(self, config) -> None:
        self.config = config
        self._session = None
        self._np = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._state = "idle"
        self._error = ""
        self._thread: threading.Thread | None = None
        self._model_path: Path | None = None

    # ------------------------------------------------------------------ API
    def _cfg(self) -> dict:
        return self.config.get("analytics") or {}

    def enabled(self) -> bool:
        return bool(self._cfg().get("enabled", True))

    def deps_available(self) -> bool:
        try:
            import numpy  # noqa: F401
            import onnxruntime  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def status(self) -> dict:
        return {
            "enabled": self.enabled(),
            "state": self._state,
            "ready": self._ready.is_set(),
            "model": str(self._model_path or self.model_path()),
            "error": self._error,
            "deps": self.deps_available(),
        }

    def start(self) -> None:
        if self._ready.is_set() or self._state == "preparing":
            return
        if not self.enabled():
            self._state = "disabled"
            return
        if not self.deps_available():
            self._state = "missing_deps"
            self._error = (
                "Faltan numpy y onnxruntime. Instala requirements-analytics.txt"
            )
            logger.info("Detección de objetos desactivada: %s", self._error)
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._prepare, name="analytics", daemon=True
        )
        self._thread.start()

    def model_path(self) -> Path:
        custom = str(self._cfg().get("model") or "").strip()
        if custom:
            return Path(custom).expanduser()
        return MODELS / MODEL_FILE

    def detect(self, jpeg: bytes, min_confidence: float | None = None) -> list[dict]:
        if not self._ready.is_set() or not jpeg:
            return []
        frame = self._decode(jpeg)
        if frame is None:
            return []
        if min_confidence is None:
            min_confidence = float(self._cfg().get("min_confidence", 0.45))
        max_objects = max(1, min(20, int(self._cfg().get("max_objects", 6))))
        try:
            with self._lock:
                outputs = self._session.run(
                    self._output_names, {self._input_name: frame}
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallo al inferir objetos: %s", exc)
            return []
        return self._postprocess(outputs, min_confidence, max_objects)

    # -------------------------------------------------------------- interno
    def _prepare(self) -> None:
        self._state = "preparing"
        try:
            path = self._ensure_model()
            self._model_path = path
            self._load(path)
            self._warmup()
            self._state = "ready"
            self._ready.set()
            logger.info("Detección de objetos lista (%s)", path.name)
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            self._state = "error"
            logger.warning("No se pudo preparar la detección de objetos: %s", exc)

    def _ensure_model(self) -> Path:
        path = self.model_path()
        if path.is_file() and path.stat().st_size >= MODEL_MIN_BYTES:
            return path
        if str(self._cfg().get("model") or "").strip():
            raise RuntimeError(f"El modelo configurado no existe: {path}")
        if not self._cfg().get("auto_download", True):
            raise RuntimeError(
                f"Falta el modelo {path.name} y auto_download está desactivado"
            )
        self._download(path)
        return path

    def _download(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        logger.info("Descargando modelo de detección (%.0f MB)…", 28)
        req = urllib.request.Request(
            MODEL_URL, headers={"User-Agent": "vstarcam"}
        )
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    digest.update(chunk)
                    fh.write(chunk)
            if tmp.stat().st_size < MODEL_MIN_BYTES:
                raise RuntimeError("la descarga del modelo quedó incompleta")
            if digest.hexdigest() != MODEL_SHA256:
                raise RuntimeError("el modelo descargado no coincide con el SHA256")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def _load(self, path: Path) -> None:
        import numpy as np
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        session = ort.InferenceSession(
            str(path), options, providers=["CPUExecutionProvider"]
        )
        self._session = session
        self._np = np
        self._input_name = session.get_inputs()[0].name
        self._output_names = [o.name for o in session.get_outputs()]

    def _warmup(self) -> None:
        np = self._np
        blank = np.zeros((1, MODEL_INPUT, MODEL_INPUT, 3), dtype=np.uint8)
        with self._lock:
            self._session.run(self._output_names, {self._input_name: blank})

    def _decode(self, jpeg: bytes):
        """JPEG → tensor uint8 NHWC usando ffmpeg (sin Pillow)."""
        try:
            proc = subprocess.run(
                [
                    ffmpeg.ffmpeg(), "-hide_banner", "-loglevel", "error",
                    "-i", "pipe:0",
                    "-vf", f"scale={MODEL_INPUT}:{MODEL_INPUT}",
                    "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                ],
                input=jpeg,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("No se pudo decodificar la imagen para análisis: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("ffmpeg no disponible para análisis: %s", exc)
            return None
        expected = MODEL_INPUT * MODEL_INPUT * 3
        if proc.returncode != 0 or len(proc.stdout) < expected:
            return None
        return self._np.frombuffer(
            proc.stdout[:expected], dtype=self._np.uint8
        ).reshape(1, MODEL_INPUT, MODEL_INPUT, 3)

    def _postprocess(self, outputs, min_confidence: float, max_objects: int) -> list[dict]:
        data = dict(zip(self._output_names, outputs))
        boxes = data.get("detection_boxes:0")
        scores = data.get("detection_scores:0")
        classes = data.get("detection_classes:0")
        count = data.get("num_detections:0")
        if boxes is None or scores is None or classes is None:
            return []
        total = int(count[0]) if count is not None else len(scores[0])
        found: list[dict] = []
        for i in range(min(total, len(scores[0]))):
            score = float(scores[0][i])
            if score < min_confidence:
                continue
            class_id = int(classes[0][i])
            name = COCO_LABELS.get(class_id)
            if not name:
                continue
            ymin, xmin, ymax, xmax = (float(v) for v in boxes[0][i])
            found.append(
                {
                    "class": name,
                    "label": label_es(name),
                    "score": round(score, 3),
                    "box": [
                        round(xmin, 3), round(ymin, 3),
                        round(max(0.0, xmax - xmin), 3),
                        round(max(0.0, ymax - ymin), 3),
                    ],
                }
            )
            if len(found) >= max_objects:
                break
        return found
