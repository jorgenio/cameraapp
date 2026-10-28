package com.jorgenio.cameraapp.data.api

import com.jorgenio.cameraapp.data.prefs.ServerPrefs
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.Response

class AuthInterceptor(private val prefs: ServerPrefs) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val cookie = runBlocking { prefs.sessionCookie.first() }
        val request = if (cookie.isNotEmpty()) {
            original.newBuilder()
                .header("Cookie", "vs_session=$cookie")
                .build()
        } else {
            original
        }
        return chain.proceed(request)
    }
}
