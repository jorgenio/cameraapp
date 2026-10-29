package com.jorgenio.cameraapp.ui.screens.events

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.PlayCircle
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.jorgenio.cameraapp.data.api.ApiClient
import com.jorgenio.cameraapp.data.model.Event
import com.jorgenio.cameraapp.ui.theme.*
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EventsScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var events by remember { mutableStateOf<List<Event>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf<Event?>(null) }

    fun load() {
        scope.launch {
            loading = true
            try {
                events = ApiClient.service().getEvents(limit = 200).events
                error = ""
            } catch (e: Exception) {
                error = e.message ?: "Error al cargar eventos"
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { load() }

    selected?.let { ev ->
        val url = ApiClient.absolute(ev.snapshot_url)
        androidx.compose.ui.window.Dialog(onDismissRequest = { selected = null }) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Card, RoundedCornerShape(12.dp))
                    .padding(8.dp)
            ) {
                AsyncImage(
                    model = url,
                    contentDescription = "Evento",
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp)),
                    contentScale = ContentScale.Fit
                )
                Row(modifier = Modifier.padding(8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(formatTs(ev.ts), color = TextSecondary)
                    TextButton(onClick = { selected = null }) { Text("Cerrar") }
                }
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Eventos") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Volver", tint = TextPrimary)
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

                events.isEmpty() -> Column(
                    modifier = Modifier.align(Alignment.Center),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Icon(
                        Icons.Default.Event,
                        contentDescription = null,
                        tint = TextSecondary,
                        modifier = Modifier.size(48.dp)
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(error.ifEmpty { "Sin eventos" }, color = TextSecondary)
                }

                else -> LazyColumn(
                    contentPadding = PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    items(events, key = { it.id }) { ev ->
                        Card(
                            modifier = Modifier.fillMaxWidth(),
                            colors = CardDefaults.cardColors(containerColor = Card),
                            shape = RoundedCornerShape(12.dp)
                        ) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        if (ev.snapshot_url.isNotEmpty()) selected = ev
                                        else if (ev.clip_url.isNotEmpty()) openClip(context, ev.clip_url)
                                    }
                                    .padding(10.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(72.dp)
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(Border)
                                ) {
                                    if (ev.snapshot_url.isNotEmpty()) {
                                        AsyncImage(
                                            model = ApiClient.absolute(ev.snapshot_url),
                                            contentDescription = null,
                                            modifier = Modifier.fillMaxSize(),
                                            contentScale = ContentScale.Crop
                                        )
                                    }
                                    if (ev.clip_url.isNotEmpty()) {
                                        Icon(
                                            Icons.Default.PlayCircle,
                                            contentDescription = null,
                                            tint = Color.White,
                                            modifier = Modifier
                                                .align(Alignment.Center)
                                                .size(28.dp)
                                        )
                                    }
                                }
                                Spacer(Modifier.width(12.dp))
                                Column(modifier = Modifier.weight(1f)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            ev.camera_id,
                                            color = TextPrimary,
                                            fontWeight = FontWeight.SemiBold,
                                            modifier = Modifier.weight(1f)
                                        )
                                        CategoryBadge(ev)
                                    }
                                    val desc = ev.description.ifBlank { ev.message }
                                    if (desc.isNotBlank()) {
                                        Spacer(Modifier.height(2.dp))
                                        Text(
                                            desc,
                                            color = TextPrimary,
                                            style = MaterialTheme.typography.bodySmall
                                        )
                                    }
                                    Text(
                                        listOfNotNull(
                                            formatTs(ev.ts),
                                            formatDuration(ev.meta?.duration)
                                        ).joinToString(" · "),
                                        color = TextSecondary,
                                        style = MaterialTheme.typography.bodySmall
                                    )
                                }
                                if (ev.clip_url.isNotEmpty()) {
                                    TextButton(onClick = { openClip(context, ev.clip_url) }) {
                                        Text("Clip")
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun openClip(context: android.content.Context, url: String) {
    val intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(Uri.parse(ApiClient.absolute(url)), "video/mp4")
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    try {
        context.startActivity(intent)
    } catch (_: Exception) {
    }
}

private fun formatTs(ts: Double): String {
    val millis = (ts * 1000).toLong()
    val fmt = SimpleDateFormat("dd/MM/yyyy HH:mm:ss", Locale.getDefault())
    return fmt.format(Date(millis))
}

private fun eventCategory(ev: Event): String {
    val cat = ev.meta?.category
    if (!cat.isNullOrBlank()) return cat
    return if (ev.type == "snapshot") "snapshot" else "movimiento"
}

private fun formatDuration(seconds: Double?): String? {
    val s = seconds ?: return null
    if (s < 1) return null
    return if (s < 60) "${s.toInt()} s"
    else "${(s / 60).toInt()} min ${(s % 60).toInt()} s"
}

@Composable
private fun CategoryBadge(ev: Event) {
    val kind = eventCategory(ev)
    val (label, color) = when (kind) {
        "persona" -> "Persona" to Color(0xFFFDA4AF)
        "vehiculo" -> "Vehículo" to Color(0xFF93C5FD)
        "animal" -> "Animal" to Success
        "objeto" -> "Objeto" to Color(0xFFC4B5FD)
        "snapshot" -> "Captura" to Color(0xFF67E8F9)
        else -> "Movimiento" to Color(0xFFFBBF24)
    }
    Text(
        label,
        color = color,
        style = MaterialTheme.typography.labelSmall,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier
            .clip(RoundedCornerShape(5.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    )
}
