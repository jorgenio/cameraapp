package com.jorgenio.cameraapp.ui.screens.cameras

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.jorgenio.cameraapp.data.api.ApiClient
import com.jorgenio.cameraapp.data.model.Camera
import com.jorgenio.cameraapp.ui.theme.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CamerasListScreen(
    onCameraClick: (Camera) -> Unit,
    onOpenEvents: () -> Unit,
    onOpenRecordings: () -> Unit,
    onOpenSettings: () -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var cameras by remember { mutableStateOf<List<Camera>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    var tick by remember { mutableStateOf(0L) }

    fun load() {
        scope.launch {
            try {
                cameras = ApiClient.service().getCameras().cameras.filter { it.enabled }
                error = ""
            } catch (e: Exception) {
                error = e.message ?: "Error de conexión"
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) {
        ApiClient.init(context)
        load()
        while (true) {
            delay(4000)
            tick += 1
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Cámaras") },
                actions = {
                    IconButton(onClick = { load() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Recargar", tint = TextPrimary)
                    }
                    IconButton(onClick = onOpenEvents) {
                        Icon(Icons.Default.Event, contentDescription = "Eventos", tint = TextPrimary)
                    }
                    IconButton(onClick = onOpenRecordings) {
                        Icon(Icons.Default.Movie, contentDescription = "Grabaciones", tint = TextPrimary)
                    }
                    IconButton(onClick = onOpenSettings) {
                        Icon(Icons.Default.Settings, contentDescription = "Ajustes", tint = TextPrimary)
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
        Box(modifier = Modifier.fillMaxSize().padding(padding)) {
            when {
                loading -> CircularProgressIndicator(
                    modifier = Modifier.align(Alignment.Center),
                    color = Accent
                )

                cameras.isEmpty() -> Column(
                    modifier = Modifier.align(Alignment.Center),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Icon(
                        Icons.Default.Videocam,
                        contentDescription = null,
                        tint = TextSecondary,
                        modifier = Modifier.size(48.dp)
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        if (error.isNotEmpty()) error else "No hay cámaras configuradas",
                        color = TextSecondary
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(onClick = { load() }) { Text("Reintentar") }
                }

                else -> LazyVerticalGrid(
                    columns = GridCells.Adaptive(minSize = 160.dp),
                    contentPadding = PaddingValues(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    items(cameras, key = { it.id }) { cam ->
                        Card(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { onCameraClick(cam) },
                            colors = CardDefaults.cardColors(containerColor = Card),
                            shape = RoundedCornerShape(12.dp)
                        ) {
                            Column {
                                Box(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(110.dp)
                                        .clip(RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp))
                                        .background(Border)
                                ) {
                                    AsyncImage(
                                        model = ApiClient.absolute(
                                            "/api/cameras/${cam.id}/snapshot.jpg?fresh=1&t=$tick"
                                        ),
                                        contentDescription = cam.name,
                                        modifier = Modifier.fillMaxSize(),
                                        contentScale = ContentScale.Crop
                                    )
                                }
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(10.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Box(
                                        modifier = Modifier
                                            .size(8.dp)
                                            .clip(RoundedCornerShape(50))
                                            .background(if (cam.enabled) Success else TextSecondary)
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    Text(
                                        cam.name,
                                        color = TextPrimary,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
