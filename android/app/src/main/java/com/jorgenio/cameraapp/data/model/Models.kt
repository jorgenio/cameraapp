package com.jorgenio.cameraapp.data.model

data class LoginReq(
    val password: String
)

data class Camera(
    val id: String,
    val name: String,
    val enabled: Boolean = true,
    val host: String? = null,
    val username: String? = null,
    val has_password: Boolean = false,
    val onvif_port: Int? = null,
    val rtsp_url: String? = null,
    val audio: Boolean? = null,
    val motion: Motion? = null,
    val record: Record? = null,
    val ptz_driver: String? = null
)

data class CameraRes(
    val ok: Boolean = true,
    val camera: Camera
)

data class Motion(
    val enabled: Boolean? = null,
    val threshold: Double? = null,
    val cooldown: Int? = null
)

data class Record(
    val mode: String? = null,
    val segment_seconds: Int? = null,
    val clip_seconds: Int? = null
)

data class CamerasRes(
    val cameras: List<Camera> = emptyList()
)

data class Event(
    val id: Long = 0,
    val camera_id: String = "",
    val type: String = "",
    val ts: Double = 0.0,
    val message: String = "",
    val snapshot: String = "",
    val clip: String = "",
    val description: String = "",
    val labels: List<EventLabel>? = null,
    val meta: EventMeta? = null,
    var snapshot_url: String = "",
    var clip_url: String = ""
)

data class EventLabel(
    val `class`: String? = null,
    val label: String? = null,
    val score: Double? = null,
    val box: List<Double>? = null
)

data class EventMeta(
    val zone: String? = null,
    val score: Double? = null,
    val duration: Double? = null,
    val category: String? = null
)

data class EventsRes(
    val events: List<Event> = emptyList()
)

data class RecordingsRes(
    val recordings: List<Recording> = emptyList()
)

data class Recording(
    val path: String = "",
    val camera: String = "",
    val name: String = "",
    val size: Long = 0,
    val size_human: String = "",
    val mtime: Double = 0.0,
    val url: String = ""
)

data class PtzReq(
    val action: String,
    val speed: Float = 0.6f,
    val duration: Float = 0.6f,
    val preset: String = ""
)

data class PtzRes(
    val ok: Boolean = false,
    val error: String? = null
)
