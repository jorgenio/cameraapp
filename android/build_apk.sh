#!/usr/bin/env bash
# Compila la app Android localmente (Linux/macOS).
#
# Requisitos:
#   - JDK 17 (JAVA_HOME o en PATH)
#   - Android SDK con platform 34 y build-tools 34.0.0 (ANDROID_HOME o ANDROID_SDK_ROOT)
#
# Uso:
#   ./build_apk.sh            # APK debug (instalable)
#   ./build_apk.sh release    # APK release sin firmar
#   ./build_apk.sh install    # compila debug e instala por adb
set -euo pipefail

cd "$(dirname "$0")"

MODE="${1:-debug}"

if ! command -v java >/dev/null 2>&1 && [ -z "${JAVA_HOME:-}" ]; then
  echo "ERROR: no hay Java. Instala JDK 17 y define JAVA_HOME." >&2
  exit 1
fi

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$SDK" ]; then
  for candidate in "$HOME/Android/Sdk" "$HOME/toolchains/android-sdk" "/usr/lib/android-sdk"; do
    if [ -d "$candidate" ]; then SDK="$candidate"; break; fi
  done
fi
if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
  echo "ERROR: no se encontró el Android SDK. Define ANDROID_HOME." >&2
  exit 1
fi

echo "sdk.dir=$SDK" > local.properties
chmod +x gradlew

case "$MODE" in
  debug)
    ./gradlew --no-daemon assembleDebug
    echo
    echo "APK: android/app/build/outputs/apk/debug/app-debug.apk"
    ;;
  release)
    ./gradlew --no-daemon assembleRelease
    echo
    echo "APK: android/app/build/outputs/apk/release/app-release-unsigned.apk"
    ;;
  install)
    ./gradlew --no-daemon assembleDebug
    adb install -r app/build/outputs/apk/debug/app-debug.apk
    ;;
  *)
    echo "Uso: $0 [debug|release|install]" >&2
    exit 1
    ;;
esac
