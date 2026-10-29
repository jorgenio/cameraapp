# CameraApp

Visor y control local de cámaras IP VStarcam/PTZ.

[![Android APK](https://github.com/jorgenio/cameraapp/actions/workflows/android.yml/badge.svg)](https://github.com/jorgenio/cameraapp/actions/workflows/android.yml)

Aplicación local para visualizar, controlar PTZ, grabar, detectar movimiento y enviar alertas por Telegram sin depender de la nube del fabricante.

## Características

- **Vista en directo**: WebRTC vía go2rtc con fallback a imágenes
- **Control PTZ**: Movimiento, zoom, home y presets (ONVIF + CGI)
- **Múltiples cámaras**: Cuadrícula con todas las cámaras a la vez
- **Grabación**: Continua por segmentos o solo por movimiento
- **Detección de movimiento**: Sin dependencias, eficiente
- **Descripción de eventos**: Zona, duración e intensidad del movimiento
- **Detección de objetos (opcional)**: Personas, vehículos y animales en local, sin nube
- **Capturas**: Guardado automático y manual
- **Eventos**: Historial con descripción, filtros por tipo y galería
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

#### Detección de objetos (opcional)

Los eventos se pueden analizar en local para describir **qué** aparece
(personas, coches, motos, perros, gatos…), sin enviar imágenes a ninguna nube.
Son dependencias opcionales: si no las instalas, todo funciona igual con
descripciones básicas de movimiento, zona y duración.

```bash
./setup_analytics.sh   # crea .venv e instala numpy + onnxruntime
./run.sh               # detecta .venv y lo usa automáticamente
```

La primera vez se descarga el modelo SSD MobileNet v1 (~28 MB, Apache-2.0) en
`data/models/`. La detección se ejecuta solo sobre la captura del evento, así
que el consumo es mínimo. Con poca luz o visión nocturna la precisión baja:
el modelo está pensado para imágenes con iluminación normal.

Ajustes en `config.json`:

```json
"analytics": {
  "enabled": true,
  "auto_download": true,
  "min_confidence": 0.45,
  "max_objects": 6
}
```

Para desactivarla en una cámara concreta: `"analytics": { "objects": false }`.

Desde la web puedes comprobar el estado en `GET /api/analytics` y probar una
captura con `POST /api/cameras/<id>/detect`.

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

**Descargar el APK ya compilado:** [última release](https://github.com/jorgenio/cameraapp/releases/latest) → `cameraapp-debug.apk`.

Compilar en local:

```bash
cd android
./build_apk.sh
```

Ver [android/README.md](android/README.md) para más detalles.

## PTZ

El backend usa **ONVIF** y, si la cámara no lo soporta correctamente, cae
automáticamente al **CGI propietario de VStarcam** (`decoder_control.cgi`).
Para forzar un driver concreto, añade a la cámara en `config.json`:

```json
{
  "id": "tres",
  "host": "192.168.1.2",
  "username": "admin",
  "password": "...",
  "ptz_driver": "cgi",
  "cgi_port": 81
}
```

- `ptz_driver`: `"cgi"`, `"onvif"` o vacío para automático.
- `cgi_port`: puerto del panel web de la cámara (81 en VStarcam).

> Algunos modelos VStarcam no soportan zoom ni presets por CGI; en ese caso el
> movimiento (arriba/abajo/izquierda/derecha, diagonales y `home`) sí funciona.

## Estructura

- `vstarcam/` - Backend Python (API, ONVIF, PTZ, detección, grabación)
- `web/` - Interfaz web (HTML/CSS/JS)
- `android/` - App Android nativa (Kotlin + Compose)
- `data/` - Configuración, grabaciones, capturas, logs
- `bin/` - Binarios descargados (ffmpeg, go2rtc, rclone)

## Licencia

MIT License. Ver [LICENSE](LICENSE).

El modelo opcional de detección (SSD MobileNet v1) procede del
[ONNX Model Zoo](https://github.com/onnx/models) y tiene licencia Apache-2.0.
