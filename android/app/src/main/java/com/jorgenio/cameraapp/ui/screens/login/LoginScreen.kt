package com.jorgenio.cameraapp.ui.screens.login

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.jorgenio.cameraapp.data.api.ApiClient
import com.jorgenio.cameraapp.data.model.LoginReq
import com.jorgenio.cameraapp.data.prefs.ServerPrefs
import com.jorgenio.cameraapp.ui.theme.Accent
import com.jorgenio.cameraapp.ui.theme.Card
import com.jorgenio.cameraapp.ui.theme.Danger
import com.jorgenio.cameraapp.ui.theme.TextPrimary
import com.jorgenio.cameraapp.ui.theme.TextSecondary
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

@Composable
fun LoginScreen(onLoginSuccess: () -> Unit) {
    val context = LocalContext.current
    val prefs = remember { ServerPrefs(context) }
    val scope = rememberCoroutineScope()

    var baseUrl by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        baseUrl = prefs.baseUrl.first()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("CameraApp", style = MaterialTheme.typography.headlineMedium, color = TextPrimary)
        Spacer(Modifier.height(6.dp))
        Text(
            "Conectar al servidor vstarcam de tu red local",
            color = TextSecondary,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(28.dp))

        Card(colors = CardDefaults.cardColors(containerColor = Card)) {
            Column(modifier = Modifier.padding(20.dp)) {
                OutlinedTextField(
                    value = baseUrl,
                    onValueChange = { baseUrl = it },
                    label = { Text("URL del servidor") },
                    placeholder = { Text("http://192.168.1.100:8080") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text("Contraseña (si está protegido)") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password)
                )
                Spacer(Modifier.height(18.dp))
                Button(
                    onClick = {
                        scope.launch {
                            loading = true
                            error = ""
                            try {
                                val url = baseUrl.trim().trimEnd('/')
                                if (!url.startsWith("http")) {
                                    error = "La URL debe empezar por http://"
                                    return@launch
                                }
                                prefs.saveBaseUrl(url)
                                ApiClient.reset()

                                val client = OkHttpClient.Builder()
                                    .connectTimeout(8, TimeUnit.SECONDS)
                                    .readTimeout(8, TimeUnit.SECONDS)
                                    .build()
                                val probe = Retrofit.Builder()
                                    .baseUrl("$url/")
                                    .client(client)
                                    .addConverterFactory(GsonConverterFactory.create())
                                    .build()
                                    .create(com.jorgenio.cameraapp.data.api.ApiService::class.java)

                                val health = probe.health()
                                if (!health.isSuccessful) {
                                    error = "El servidor respondió ${health.code()}"
                                    return@launch
                                }

                                if (password.isNotEmpty()) {
                                    val resp = probe.login(LoginReq(password))
                                    if (!resp.isSuccessful) {
                                        error = "Contraseña incorrecta"
                                        return@launch
                                    }
                                    val setCookie = resp.headers()["Set-Cookie"].orEmpty()
                                    if (setCookie.contains("vs_session=")) {
                                        val cookie = setCookie.substringAfter("vs_session=").substringBefore(";")
                                        prefs.saveCookie(cookie)
                                    }
                                }

                                ApiClient.init(context)
                                onLoginSuccess()
                            } catch (e: Exception) {
                                error = "No se pudo conectar: ${e.message ?: e::class.simpleName}"
                            } finally {
                                loading = false
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !loading && baseUrl.isNotBlank(),
                    colors = ButtonDefaults.buttonColors(containerColor = Accent)
                ) {
                    if (loading) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = TextPrimary
                        )
                        Spacer(Modifier.width(10.dp))
                    }
                    Text(if (loading) "Conectando…" else "Conectar")
                }
                if (error.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    Text(error, color = Danger, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}
