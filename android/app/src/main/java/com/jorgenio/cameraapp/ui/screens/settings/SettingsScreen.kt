package com.jorgenio.cameraapp.ui.screens.settings

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.jorgenio.cameraapp.data.api.ApiClient
import com.jorgenio.cameraapp.data.model.LoginReq
import com.jorgenio.cameraapp.data.prefs.ServerPrefs
import com.jorgenio.cameraapp.ui.theme.*
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit, onDisconnect: () -> Unit) {
    val context = LocalContext.current
    val prefs = remember { ServerPrefs(context) }
    val scope = rememberCoroutineScope()

    var baseUrl by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var message by remember { mutableStateOf("") }
    var working by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        baseUrl = prefs.baseUrl.first()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Ajustes") },
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
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text("Servidor", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = baseUrl,
                onValueChange = { baseUrl = it },
                label = { Text("URL del servidor") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true
            )
            Text(
                "Ejemplo: http://192.168.1.100:8080",
                color = TextSecondary,
                style = MaterialTheme.typography.bodySmall
            )
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Contraseña (opcional)") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                visualTransformation = PasswordVisualTransformation()
            )
            Button(
                onClick = {
                    scope.launch {
                        working = true
                        message = ""
                        try {
                            val url = baseUrl.trim().trimEnd('/')
                            prefs.saveBaseUrl(url)
                            ApiClient.reset()
                            ApiClient.init(context)
                            if (password.isNotEmpty()) {
                                val resp = ApiClient.service().login(LoginReq(password))
                                val cookie = resp.headers()["Set-Cookie"].orEmpty()
                                if (cookie.contains("vs_session=")) {
                                    prefs.saveCookie(
                                        cookie.substringAfter("vs_session=").substringBefore(";")
                                    )
                                }
                            }
                            message = "Guardado correctamente"
                        } catch (e: Exception) {
                            message = "Error: ${e.message}"
                        } finally {
                            working = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = !working,
                colors = ButtonDefaults.buttonColors(containerColor = Accent)
            ) {
                Text(if (working) "Guardando…" else "Guardar")
            }
            if (message.isNotEmpty()) {
                Text(message, color = if (message.startsWith("Error")) Danger else Success)
            }

            HorizontalDivider(color = Border)
            Spacer(Modifier.height(4.dp))

            OutlinedButton(
                onClick = {
                    scope.launch {
                        prefs.clearCookie()
                        ApiClient.reset()
                        onDisconnect()
                    }
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Desconectar y cambiar de servidor", color = Danger)
            }

            Spacer(Modifier.weight(1f))
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text("CameraApp 1.0.0", color = TextSecondary)
                Text("Cliente Android para vstarcam", color = TextSecondary, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
