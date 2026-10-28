package com.jorgenio.cameraapp.ui.screens.player

import android.content.ContentValues
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowLeft
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.jorgenio.cameraapp.data.api.ApiClient
import com.jorgenio.cameraapp.data.model.PtzReq
import com.jorgenio.cameraapp.data.prefs.ServerPrefs
import com.jorgenio.cameraapp.ui.theme.*
import com.jorgenio.cameraapp.webrtc.WebRTCManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.webrtc.EglBase
import org.webrtc.SurfaceViewRenderer
import java.text.SimpleDateFormat
import java.util.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlayerScreen(cameraId: String, cameraName: String, onBack: () -> Unit) {
    val context = LocalContext.current
    val prefs = remember { ServerPrefs(context) }
    val scope = rememberCoroutineScope()

    var state by remember { mutableStateOf("connecting") }
    var eglContext by remember { mutableStateOf<EglBase.Context?>(null) }
    var manager by remember { mutableStateOf<WebRTCManager?>(null) }
    var backendMessage by remember { mutableStateOf("") }
    var cookie by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        ApiClient.init(context)
        cookie = prefs.sessionCookie.first()

        val mgr = WebRTCManager(context, ApiClient.baseUrl(), cookie)
        manager = mgr
        mgr.onState = { st -> state = st }
        mgr.onError = { msg -> backendMessage = msg }
        mgr.init()
        eglContext = mgr.eglContext()
        mgr.start(cameraId)
    }

    DisposableEffect(Unit) {
        onDispose { manager?.dispose() }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(cameraName) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Volver", tint = TextPrimary)
                    }
                },
                actions = {
                    IconButton(onClick = {
                        state = "connecting"
                        backendMessage = ""
                        manager?.start(cameraId)
                    }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Reconectar", tint = TextPrimary)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Bg,
                    titleContentColor = TextPrimary
                )
            )
        },
        containerColor = Bg
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .background(Color.Black)
            ) {
                val ctx = eglContext
                if (ctx != null) {
                    AndroidView(
                        factory = { viewContext ->
                            SurfaceViewRenderer(viewContext).apply {
                                init(ctx, null)
                                setEnableHardwareScaler(true)
                                setMirror(false)
                                manager?.attachRenderer(this)
                            }
                        },
                        modifier = Modifier.fillMaxSize()
                    )
                }
                if (state != "playing") {
                    Column(modifier = Modifier.align(Alignment.Center)) {
                        Text(
                            when (state) {
                                "connecting" -> "Conectando…"
                                "reconnecting" -> "Reconectando…"
                                "failed" -> "No se pudo conectar"
                                else -> state
                            },
                            color = TextSecondary
                        )
                        if (backendMessage.isNotEmpty()) {
                            Spacer(Modifier.height(6.dp))
                            Text(backendMessage, color = Danger)
                        }
                    }
                }
            }

            // PTZ
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically
            ) {
                PtzButton(Icons.Default.KeyboardArrowLeft) {
                    scope.launch { ptz(cameraId, "left") }
                }
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    PtzButton(Icons.Default.KeyboardArrowUp) {
                        scope.launch { ptz(cameraId, "up") }
                    }
                    PtzButton(Icons.Default.Home) {
                        scope.launch { ptz(cameraId, "home") }
                    }
                    PtzButton(Icons.Default.KeyboardArrowDown) {
                        scope.launch { ptz(cameraId, "down") }
                    }
                }
                PtzButton(Icons.Default.KeyboardArrowRight) {
                    scope.launch { ptz(cameraId, "right") }
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedButton(
                    onClick = { scope.launch { ptz(cameraId, "zoom_in") } },
                    modifier = Modifier.weight(1f)
                ) { Text("Zoom +") }
                OutlinedButton(
                    onClick = { scope.launch { ptz(cameraId, "zoom_out") } },
                    modifier = Modifier.weight(1f)
                ) { Text("Zoom −") }
                Button(
                    onClick = {
                        scope.launch {
                            try {
                                val bytes = withContext(Dispatchers.IO) {
                                    val req = Request.Builder()
                                        .url(
                                            ApiClient.absolute(
                                                "/api/cameras/$cameraId/snapshot.jpg?fresh=1"
                                            )
                                        )
                                        .apply {
                                            if (cookie.isNotEmpty()) header("Cookie", "vs_session=$cookie")
                                        }
                                        .build()
                                    ApiClient.client().newCall(req).execute().use { it.body?.bytes() }
                                }
                                if (bytes != null) {
                                    saveToGallery(context, bytes, cameraId)
                                    Toast.makeText(context, "Captura guardada en Pictures/CameraApp", Toast.LENGTH_SHORT).show()
                                } else {
                                    Toast.makeText(context, "No se pudo obtener la imagen", Toast.LENGTH_SHORT).show()
                                }
                            } catch (e: Exception) {
                                Toast.makeText(context, "Error: ${e.message}", Toast.LENGTH_SHORT).show()
                            }
                        }
                    },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(containerColor = Accent)
                ) {
                    Icon(Icons.Default.PhotoCamera, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Captura")
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

private suspend fun ptz(cameraId: String, action: String) {
    withContext(Dispatchers.IO) {
        try {
            ApiClient.service().ptz(cameraId, PtzReq(action = action))
        } catch (_: Exception) {
        }
    }
}

@Composable
private fun PtzButton(icon: ImageVector, onClick: () -> Unit) {
    IconButton(
        onClick = onClick,
        modifier = Modifier
            .size(54.dp)
            .background(Card, CircleShape)
    ) {
        Icon(icon, contentDescription = null, tint = TextPrimary, modifier = Modifier.size(28.dp))
    }
}

private fun saveToGallery(context: android.content.Context, bytes: ByteArray, cameraId: String) {
    val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
    val fileName = "${cameraId}_$stamp.jpg"

    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, fileName)
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/CameraApp")
        }
        val uri = context.contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            ?: throw IllegalStateException("No se pudo crear el archivo")
        context.contentResolver.openOutputStream(uri)?.use { out -> out.write(bytes) }
    } else {
        val dir = java.io.File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES),
            "CameraApp"
        )
        dir.mkdirs()
        java.io.File(dir, fileName).writeBytes(bytes)
    }
}
