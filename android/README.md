# CameraApp para Android

App Android nativa (Kotlin + Jetpack Compose) para ver y controlar las cámaras del servidor **vstarcam** en tu red local.

Consume la API REST del servidor `vstarcam` (Python) que ya conoces. **No** habla directamente con la cámara: se conecta al servidor, igual que el panel web.

## Funcionalidades

- **Conexión al servidor**: URL `http://IP:PUERTO` + contraseña opcional
- **Listado de cámaras**: cuadrícula con miniaturas actualizadas cada 4 s
- **Vista en directo**: **WebRTC** (H.264) vía go2rtc, el mismo flujo que usa el panel web
- **Control PTZ**: izquierda/derecha/arriba/abajo, home y zoom
- **Capturas**: guarda el snapshot en `Pictures/CameraApp/`
- **Eventos**: historial con miniaturas y clip
- **Grabaciones**: listado y reproducción con el reproductor del sistema
- **Ajustes**: cambiar servidor o desconectar

## Requisitos

- Android 7.0 (API 24) o superior
- Servidor vstarcam accesible en la misma red local
- El móvil y el PC del servidor en la **misma red**

## APK ya compilado

Cada push a `main` que modifique `android/` compila el APK automáticamente con GitHub Actions:

- Descarga directa: [última release](https://github.com/jorgenio/cameraapp/releases/latest) → `cameraapp-debug.apk`
- O desde la pestaña **Actions** → run más reciente → artefacto `cameraapp-apk`

El APK debug está firmado con clave de debug, así que se instala directamente (puede requerir permitir "orígenes desconocidos").

## Cómo compilar

### Opción 0: script local

```bash
cd android
./build_apk.sh           # APK debug
./build_apk.sh install   # compila e instala por adb
```

Requiere JDK 17 y Android SDK (platform 34 + build-tools 34.0.0).

### Opción 1: Android Studio

1. Abre Android Studio
2. `File > Open` y selecciona la carpeta `android/`
3. Deja que sincronice Gradle (la primera vez descarga dependencias)
4. Conecta el móvil por USB con depuración activada o usa un emulador
5. Pulsa **Run ▶**

### Opción 2: línea de comandos

Necesitas JDK 17 y el Android SDK con `ANDROID_HOME` configurado.

```bash
cd android
./gradlew assembleDebug
```

El APK queda en:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

Instálalo en el móvil:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Para una build de release (sin firmar):

```bash
./gradlew assembleRelease
```

## Primer uso

1. Abre la app
2. Escribe la URL del servidor. Ejemplo:
   - `http://192.168.1.100:8080`
   - `http://192.168.1.100:8091` si usas otro puerto
3. Si el panel tiene contraseña, escríbela
4. Pulsa **Conectar**
5. Toca una cámara para abrir el directo

> La IP del servidor te la muestra `vstarcam` al arrancar: `En tu red: http://192.168.x.x:8080`

## Notas técnicas

| Pieza | Uso |
|---|---|
| **Retrofit + OkHttp** | API REST y descarga de snapshots |
| **DataStore** | Guarda URL y cookie de sesión |
| **Jetpack Compose + Material 3** | UI |
| **Coil** | Miniaturas |
| **stream-webrtc-android** | WebRTC nativo (go2rtc) |

- La app usa HTTP en claro (`usesCleartextTraffic`) porque el servidor es local.
- La autenticación reutiliza `/api/login` y la cookie `vs_session`.
- Si WebRTC falla, la miniatura de la cámara sigue funcionando vía snapshot.

## Estructura

```text
android/
├── app/src/main/java/com/jorgenio/cameraapp/
│   ├── data/          # API, modelos, preferencias
│   ├── ui/            # Compose: pantallas, tema, navegación
│   ├── webrtc/        # Cliente WebRTC para go2rtc
│   ├── MainActivity.kt
│   └── CameraApp.kt
├── app/build.gradle.kts
├── build.gradle.kts
└── settings.gradle.kts
```
