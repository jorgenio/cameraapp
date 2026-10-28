#!/usr/bin/env bash
# Descarga los binarios que necesita VStarcam local en ./bin
# No usa sudo ni toca el sistema: todo queda dentro del proyecto.
set -euo pipefail

cd "$(dirname "$0")"

BIN="bin"
mkdir -p "$BIN" data/logs

need_ffmpeg=1
need_go2rtc=1
need_rclone=1

[[ -x "$BIN/ffmpeg" && -x "$BIN/ffprobe" ]] && need_ffmpeg=0
[[ -x "$BIN/go2rtc" ]] && need_go2rtc=0
[[ -x "$BIN/rclone" ]] && need_rclone=0

# Argumentos: --no-rclone, --force
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --no-rclone) need_rclone=0 ;;
    --force) FORCE=1 ;;
  esac
done
if [[ "$FORCE" == "1" ]]; then
  need_ffmpeg=1; need_go2rtc=1
fi

fetch() { # fetch URL DEST
  local url="$1" dest="$2"
  echo "  ↓ $(basename "$dest")"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 15 -o "$dest" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "$dest" "$url"
  else
    echo "ERROR: necesitas curl o wget" >&2
    exit 1
  fi
}

unzip_to() { # unzip_to FILE DIR
  local file="$1" dir="$2"
  mkdir -p "$dir"
  if command -v unzip >/dev/null 2>&1; then
    unzip -oq "$file" -d "$dir"
  else
    python3 - "$file" "$dir" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    z.extractall(sys.argv[2])
PY
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64)  FF_ARCH=amd64;  GO_ARCH=amd64;  RC_ARCH=amd64 ;;
  aarch64|arm64) FF_ARCH=arm64;  GO_ARCH=arm64;  RC_ARCH=arm64 ;;
  armv7l|armv6l) FF_ARCH=armhf;  GO_ARCH=arm;    RC_ARCH=arm   ;;
  *) echo "Arquitectura no soportada: $ARCH" >&2; exit 1 ;;
esac

echo "== VStarcam local · preparación de binarios ($ARCH) =="

# ------------------------------------------------------------------- ffmpeg
if [[ "$need_ffmpeg" == "1" ]]; then
  echo "• ffmpeg estático"
  ok=0
  for base in \
    "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${FF_ARCH}-static.tar.xz" \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
  do
    if [[ "$FF_ARCH" != "amd64" && "$base" == *BtbN* ]]; then continue; fi
    if fetch "$base" "$TMP/ffmpeg.tar.xz" 2>/dev/null; then
      if tar -xJf "$TMP/ffmpeg.tar.xz" -C "$TMP" 2>/dev/null; then
        src="$(find "$TMP" -maxdepth 3 -type f -name ffmpeg -perm -u+x | head -1)"
        probe="$(find "$TMP" -maxdepth 3 -type f -name ffprobe -perm -u+x | head -1)"
        if [[ -n "$src" ]]; then
          cp "$src" "$BIN/ffmpeg"
          [[ -n "$probe" ]] && cp "$probe" "$BIN/ffprobe"
          chmod +x "$BIN/ffmpeg" ${probe:+"$BIN/ffprobe"}
          ok=1
          break
        fi
      fi
    fi
  done
  if [[ "$ok" != "1" ]]; then
    echo "AVISO: no se pudo descargar ffmpeg." >&2
    echo "Instálalo con: sudo apt install ffmpeg" >&2
  fi
else
  echo "• ffmpeg ya presente"
fi

# ------------------------------------------------------------------ go2rtc
if [[ "$need_go2rtc" == "1" ]]; then
  echo "• go2rtc (WebRTC de baja latencia)"
  if fetch "https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_${GO_ARCH}" "$TMP/go2rtc"; then
    cp "$TMP/go2rtc" "$BIN/go2rtc"
    chmod +x "$BIN/go2rtc"
  else
    echo "AVISO: no se pudo descargar go2rtc; el vídeo usará modo imágenes." >&2
  fi
else
  echo "• go2rtc ya presente"
fi

# ------------------------------------------------------------------ rclone
if [[ "$need_rclone" == "1" ]]; then
  echo "• rclone (opcional, para tu nube)"
  if fetch "https://downloads.rclone.org/rclone-current-linux-${RC_ARCH}.zip" "$TMP/rclone.zip"; then
    unzip_to "$TMP/rclone.zip" "$TMP/rclone"
    rc="$(find "$TMP/rclone" -type f -name rclone | head -1)"
    if [[ -n "$rc" ]]; then
      cp "$rc" "$BIN/rclone"
      chmod +x "$BIN/rclone"
    fi
  else
    echo "AVISO: rclone no descargado (solo hace falta para nubes tipo Drive/S3)." >&2
  fi
else
  echo "• rclone omitido"
fi

echo
echo "Comprobación:"
for p in ffmpeg ffprobe go2rtc rclone; do
  if [[ -x "$BIN/$p" ]]; then
    printf '  ✔ %-8s %s\n' "$p" "$("$BIN/$p" --version 2>/dev/null | head -1 || echo ok)"
  else
    printf '  · %-8s no instalado\n' "$p"
  fi
done
echo
echo "Listo. Arranca con: ./run.sh"
