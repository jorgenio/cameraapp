package com.jorgenio.cameraapp.data.prefs

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "cameraapp_prefs")

class ServerPrefs(private val context: Context) {
    companion object {
        val KEY_BASE_URL = stringPreferencesKey("base_url")
        val KEY_COOKIE = stringPreferencesKey("vs_session")
        val KEY_PASSWORD = stringPreferencesKey("password")
    }

    val baseUrl: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_BASE_URL] ?: "http://192.168.1.100:8080"
    }

    val sessionCookie: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_COOKIE] ?: ""
    }

    suspend fun saveBaseUrl(url: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_BASE_URL] = url.trimEnd('/')
        }
    }

    suspend fun saveCookie(cookie: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_COOKIE] = cookie
        }
    }

    suspend fun clearCookie() {
        context.dataStore.edit { prefs ->
            prefs.remove(KEY_COOKIE)
        }
    }
}
