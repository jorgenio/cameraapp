package com.jorgenio.cameraapp

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory
import com.jorgenio.cameraapp.data.prefs.ServerPrefs
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Application con un ImageLoader de Coil que añade la cookie de sesión
 * a las peticiones de miniaturas/snapshots.
 */
class CameraApp : Application(), ImageLoaderFactory {

    override fun newImageLoader(): ImageLoader {
        val prefs = ServerPrefs(this)
        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .addInterceptor { chain ->
                val cookie = runBlocking { prefs.sessionCookie.first() }
                val request = if (cookie.isNotEmpty()) {
                    chain.request().newBuilder()
                        .header("Cookie", "vs_session=$cookie")
                        .build()
                } else {
                    chain.request()
                }
                chain.proceed(request)
            }
            .build()

        return ImageLoader.Builder(this)
            .okHttpClient(client)
            .build()
    }
}
