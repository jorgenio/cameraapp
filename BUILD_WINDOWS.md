# Compilar CameraApp para Windows con PyInstaller

Este proyecto está preparado para generar un ejecutable Windows (.exe) usando PyInstaller desde tu propio PC con Windows.

## 1) Requisitos previos

- Windows 10/11
- [Python 3.9+](https://www.python.org/downloads/) - **Importante:** Marcar `Add Python to PATH` durante la instalación
- [Git](https://git-scm.com/download/win) (opcional, pero recomendable)
- Conexión a internet (para descargar PyInstaller)

## 2) Descargar el proyecto

Clona o descarga el repositorio:

```bash
git clone https://github.com/jorgenio/cameraapp.git
cd cameraapp
```

O descarga el ZIP desde GitHub y extrae en una carpeta.

## 3) Descargar binarios para Windows

El proyecto necesita `ffmpeg`, `ffprobe`, `go2rtc` y `rclone` para Windows. Puedes descargarlos manualmente o usar el script `setup.sh` como referencia.

### Opción A: Descarga manual

Crea la carpeta `bin\` en la raíz del proyecto y coloca ahí estos archivos:

| Archivo | Fuente | Notas |
|---:|---|---|
| `ffmpeg.exe` | [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) o [ffmpeg.org](https://ffmpeg.org/download.html#build-windows) | Descarga la build `essentials` o `full`. Extrae `ffmpeg.exe` y `ffprobe.exe` |
| `ffprobe.exe` | Mismo paquete que ffmpeg | Junto a ffmpeg.exe |
| `go2rtc.exe` | [github.com/AlexxIT/go2rtc/releases](https://github.com/AlexxIT/go2rtc/releases) | Descarga `go2rtc_win64.zip`, extrae `go2rtc.exe` |
| `rclone.exe` | [rclone.org/downloads](https://rclone.org/downloads/) | Descarga `rclone-v*-windows-amd64.zip`, extrae `rclone.exe` |

### Opción B: Usar winget/choco (si los tienes)

```powershell
winget install Gyan.FFmpeg
# go2rtc y rclone descargar desde sus releases
```

Una vez descargados, deben quedar así:

```text
cameraapp\
├── bin\
│   ├── ffmpeg.exe
│   ├── ffprobe.exe
│   ├── go2rtc.exe
│   └── rclone.exe
├── vstarcam\
├── web\
├── vstarcam.spec
├── build_windows.bat
└── run_windows.bat
```

## 4) Compilar el ejecutable

Abre una **Símbolo del sistema (cmd)** o **PowerShell** en la carpeta `cameraapp/`.

Ejecuta:

```bat
build_windows.bat
```

El script:
1. Instala PyInstaller
2. Limpia `build/` y `dist/`
3. Genera el ejecutable con `vstarcam.spec`

Cuando termine, tendrás:

```text
dist\vstarcam\
├── vstarcam.exe
├── _internal\
│   ├── vstarcam\
│   ├── web\
│   └── bin\
└── ...
```

### Detección de objetos en el .exe (opcional)

Si quieres que el ejecutable describa **qué** aparece en cada evento (personas,
coches, animales…), instala las dependencias opcionales con el mismo Python
que compila, antes de ejecutar `build_windows.bat`:

```bat
pip install -r requirements-analytics.txt
build_windows.bat
```

PyInstaller las incluirá en el ejecutable. El modelo (~28 MB) se descarga la
primera vez que arranca en `dist\vstarcam\data\models\`. Si compilas sin estas
dependencias el .exe funciona igual, con descripciones básicas de movimiento,
zona y duración.

## 5) Ejecutar CameraApp

Tienes dos opciones:

**Opción 1:** Doble clic en `run_windows.bat` (más cómodo)

**Opción 2:** Ir a `dist\vstarcam\` y ejecutar `vstarcam.exe` directamente

Al arrancar se abrirá el navegador en `http://127.0.0.1:8080`. Los datos (config, grabaciones, capturas) se guardarán en `dist\vstarcam\data\`.

## 6) Notas importantes

- **Modo onedir:** Usamos `onedir` en lugar de `onefile`. Esto es más estable con `ffmpeg`/`go2rtc` y evita problemas al extraer archivos.
- **Datos junto al exe:** Cuando está empaquetado, `data/`, `config.json` y logs se guardan junto a `vstarcam.exe` (en `dist\vstarcam\`).
- **Antivirus:** En algunos antivirus Windows Defender puede marcar falsamente el `.exe`. Si pasa, añade una excepción para la carpeta `dist\vstarcam\`.
- **Red local:** Para acceder desde otros dispositivos de tu red, usa la IP que te muestra al arrancar.
- **Primera vez:** La primera ejecución puede tardar un poco al arrancar go2rtc/ffmpeg.
