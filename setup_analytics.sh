#!/usr/bin/env bash
# Instala las dependencias opcionales de análisis (numpy + onnxruntime) en .venv
# y deja el panel listo para detectar personas/objetos en los eventos.
#
# Todo queda dentro del proyecto: no usa sudo ni toca el Python del sistema.
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: hace falta python3" >&2
  exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creando entorno virtual en $VENV…"
  # --without-pip evita depender de ensurepip (no siempre está instalado).
  if ! python3 -m venv --without-pip "$VENV"; then
    python3 -m venv "$VENV"
  fi
fi

if ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "Instalando pip en el entorno virtual…"
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' EXIT
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --connect-timeout 15 -o "$tmp" https://bootstrap.pypa.io/get-pip.py
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$tmp" https://bootstrap.pypa.io/get-pip.py
  else
    echo "ERROR: necesitas curl o wget" >&2
    exit 1
  fi
  "$VENV/bin/python" "$tmp"
  rm -f "$tmp"
  trap - EXIT
fi

echo "Instalando dependencias de análisis…"
"$VENV/bin/python" -m pip install --upgrade pip -q
"$VENV/bin/python" -m pip install -r requirements-analytics.txt

"$VENV/bin/python" - <<'PY'
import numpy
import onnxruntime

print(f"OK: numpy {numpy.__version__}, onnxruntime {onnxruntime.__version__}")
PY

echo
echo "Listo. Arranca con ./run.sh (usará .venv automáticamente)."
echo "El modelo de detección se descarga solo la primera vez en data/models/."
