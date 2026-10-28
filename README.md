# CameraApp

Visor y control local de cámaras IP VStarcam/PTZ.

Aplicación local para visualizar, controlar PTZ, grabar, detectar movimiento y enviar alertas por Telegram sin depender de la nube del fabricante.

## Características

- **Vista en directo**: WebRTC vía go2rtc con fallback a imágenes
- **Control PTZ**: Movimiento, zoom, home y presets (ONVIF + CGI)
- **Múltiples cámaras**: Cuadrícula con todas las cámaras a la vez
- **Grabación**: Continua por segmentos o solo por movimiento
- **Detección de movimiento**: Sin dependencias, eficiente
- **Capturas**: Guardado automático y manual
- **Eventos**: Historial con galería y navegación
- **Alertas**: Telegram, ntfy, webhook
- **Subida a nube**: WebDAV / rclone (MEGA, FTP, S3, etc.)
- **Escaneo ONVIF**: Descubrimiento automático en red local
- **Acceso por red local**: Todo corre en local

## Requisitos

- Linux (probado en Ubuntu)
- Python 3.9+
- FFmpeg (se descarga automáticamente con `setup.sh`)
- go2rtc (se descarga automáticamente con `setup.sh`)

## Instalación

### Linux / macOS

```bash
git clone git@github.com:jorgenio/cameraapp.git
cd cameraapp
./setup.sh
./run.sh
```

Abre [http://127.0.0.1:8080](http://127.0.0.1:8080) en tu navegador.

### Windows

El proyecto incluye scripts para compilar un `.exe` con PyInstaller:

```bat
build_windows.bat
run_windows.bat
```

Ver [BUILD_WINDOWS.md](BUILD_WINDOWS.md) para el paso a paso (incluye descarga de binarios para Windows).

### Android

App nativa en Kotlin + Jetpack Compose que consume la API del servidor:

```text
android/
```

Ver [android/README.md](android/README.md) para compilar e instalar el APK.

## Estructura

- `vstarcam/` - Backend Python (API, ONVIF, PTZ, detección, grabación)
- `web/` - Interfaz web (HTML/CSS/JS)
- `android/` - App Android nativa (Kotlin + Compose)
- `data/` - Configuración, grabaciones, capturas, logs
- `bin/` - Binarios descargados (ffmpeg, go2rtc, rclone)

## Licencia

MIT License. Ver [LICENSE](LICENSE).
