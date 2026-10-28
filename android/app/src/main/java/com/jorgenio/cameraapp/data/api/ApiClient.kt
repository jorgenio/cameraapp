package com.jorgenio.cameraapp.data.api

import android.content.Context
import com.jorgenio.cameraapp.data.prefs.ServerPrefs
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Cliente HTTP/Retrofit único para toda la app.
 * Lee la URL base y la cookie de sesión desde DataStore.
 */
object ApiClient {
    @Volatile private var api: ApiService? = null
    @Volatile private var http: OkHttpClient? = null
    @Volatile private var baseUrl: String = ""

    fun init(context: Context) {
        if (api != null) return
        val prefs = ServerPrefs(context.applicationContext)
        val url = runBlocking { prefs.baseUrl.first() }
        baseUrl = url

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(prefs))
            .addInterceptor(logging)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()

        http = client
        api = Retrofit.Builder()
            .baseUrl(if (url.endsWith("/")) url else "$url/")
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }

    fun service(): ApiService = api ?: error("ApiClient no inicializado")
    fun client(): OkHttpClient = http ?: error("ApiClient no inicializado")

    fun baseUrl(): String = baseUrl

    fun reset() {
        api = null
        http = null
        baseUrl = ""
    }

    fun absolute(path: String): String {
        if (path.startsWith("http")) return path
        val base = baseUrl.trimEnd('/')
        return if (path.startsWith("/")) base + path else "$base/$path"
    }
}
