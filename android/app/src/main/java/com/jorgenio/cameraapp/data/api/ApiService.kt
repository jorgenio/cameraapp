package com.jorgenio.cameraapp.data.api

import com.jorgenio.cameraapp.data.model.*
import okhttp3.RequestBody
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.*

interface ApiService {
    @GET("/api/health")
    suspend fun health(): Response<ResponseBody>

    @POST("/api/login")
    suspend fun login(@Body req: LoginReq): Response<ResponseBody>

    @GET("/api/cameras")
    suspend fun getCameras(): CamerasRes

    @GET("/api/cameras/{id}/snapshot.jpg")
    suspend fun getSnapshot(
        @Path("id") id: String,
        @Query("fresh") fresh: String = "1"
    ): ResponseBody

    @GET("/api/frame")
    suspend fun getFrame(@Query("src") src: String): ResponseBody

    @POST("/api/ptz/{id}")
    suspend fun ptz(@Path("id") id: String, @Body req: PtzReq): PtzRes

    @GET("/api/events")
    suspend fun getEvents(
        @Query("limit") limit: Int = 100,
        @Query("camera") camera: String? = null
    ): EventsRes

    @DELETE("/api/events")
    suspend fun deleteEvent(@Query("id") id: Long): ResponseBody

    @GET("/api/recordings")
    suspend fun getRecordings(@Query("camera") camera: String? = null): RecordingsRes

    @DELETE("/api/recordings")
    suspend fun deleteRecording(@Query("path") path: String): ResponseBody

    @POST("/api/webrtc")
    suspend fun webrtcOffer(
        @Query("src") src: String,
        @Body offer: RequestBody
    ): ResponseBody
}
