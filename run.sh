#!/usr/bin/env bash
# Arranca el panel local de VStarcam.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: hace falta python3" >&2
  exit 1
fi

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')
if [[ "$PY_OK" != "1" ]]; then
  echo "ERROR: se necesita Python 3.9 o superior (tienes $(python3 -V))" >&2
  exit 1
fi

if [[ ! -x bin/ffmpeg || ! -x bin/go2rtc ]]; then
  echo "Faltan binarios, preparando por primera vez…"
  ./setup.sh
fi

mkdir -p data

exec python3 -m vstarcam "$@"
