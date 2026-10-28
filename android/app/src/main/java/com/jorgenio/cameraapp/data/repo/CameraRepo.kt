package com.jorgenio.cameraapp.data.repo

import com.jorgenio.cameraapp.data.api.ApiService
import com.jorgenio.cameraapp.data.model.*

class CameraRepo(private val api: ApiService) {
    suspend fun cameras() = api.getCameras()
    suspend fun events(limit: Int = 100) = api.getEvents(limit)
    suspend fun recordings() = api.getRecordings()
    suspend fun ptz(id: String, action: String) = api.ptz(id, PtzReq(action = action))
    suspend fun login(password: String) = api.login(LoginReq(password))
    suspend fun health() = api.health()
}
