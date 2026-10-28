package com.jorgenio.cameraapp.webrtc

import android.content.Context
import kotlinx.coroutines.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.webrtc.*
import java.util.concurrent.TimeUnit

/**
 * Cliente WebRTC para go2rtc usando la señalización del servidor vstarcam.
 * Equivalente a web/webrtc.js del panel web.
 */
class WebRTCManager(
    private val context: Context,
    private val baseUrl: String,
    private val cookie: String
) {
    private var eglBase: EglBase? = null
    private var factory: PeerConnectionFactory? = null
    private var peerConnection: PeerConnection? = null
    private var pendingTrack: VideoTrack? = null
    private var renderer: SurfaceViewRenderer? = null
    private var scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    var onState: ((String) -> Unit)? = null
    var onError: ((String) -> Unit)? = null

    fun init() {
        if (factory != null) return
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(context.applicationContext)
                .setEnableInternalTracer(false)
                .createInitializationOptions()
        )
        val egl = EglBase.create()
        eglBase = egl
        val encoder = DefaultVideoEncoderFactory(egl.eglBaseContext, true, true)
        val decoder = DefaultVideoDecoderFactory(egl.eglBaseContext)
        factory = PeerConnectionFactory.builder()
            .setVideoEncoderFactory(encoder)
            .setVideoDecoderFactory(decoder)
            .createPeerConnectionFactory()
    }

    fun eglContext(): EglBase.Context? = eglBase?.eglBaseContext

    /** Adjunta el SurfaceViewRenderer y reproduce la pista si ya llegó. */
    fun attachRenderer(view: SurfaceViewRenderer) {
        renderer = view
        pendingTrack?.let { track ->
            try {
                track.addSink(view)
            } catch (_: Exception) {
            }
        }
    }

    fun detachRenderer() {
        renderer?.let { view ->
            pendingTrack?.let { track ->
                try {
                    track.removeSink(view)
                } catch (_: Exception) {
                }
            }
        }
        renderer = null
    }

    fun start(camId: String) {
        scope.launch {
            try {
                connect(camId)
            } catch (e: Exception) {
                onState?.invoke("failed")
                onError?.invoke(e.message ?: "Error WebRTC")
            }
        }
    }

    private suspend fun connect(camId: String) = withContext(Dispatchers.IO) {
        init()
        closePeer()
        onState?.invoke("connecting")

        val rtcConfig = PeerConnection.RTCConfiguration(
            listOf(PeerConnection.IceServer.builder(emptyList()).createIceServer())
        ).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
            bundlePolicy = PeerConnection.BundlePolicy.MAXBUNDLE
        }

        val pc = factory!!.createPeerConnection(rtcConfig, object : PeerConnection.Observer {
            override fun onIceCandidate(candidate: IceCandidate?) {}
            override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>?) {}
            override fun onSignalingChange(state: PeerConnection.SignalingState?) {}
            override fun onIceConnectionChange(state: PeerConnection.IceConnectionState?) {}
            override fun onIceConnectionReceivingChange(receiving: Boolean) {}
            override fun onIceGatheringChange(state: PeerConnection.IceGatheringState?) {}
            override fun onAddStream(stream: MediaStream?) {}
            override fun onRemoveStream(stream: MediaStream?) {}
            override fun onDataChannel(channel: DataChannel?) {}
            override fun onRenegotiationNeeded() {}
            override fun onAddTrack(receiver: RtpReceiver?, streams: Array<out MediaStream>?) {
                val track = receiver?.track()
                if (track is VideoTrack) {
                    pendingTrack = track
                    renderer?.let { view ->
                        try {
                            track.addSink(view)
                        } catch (_: Exception) {
                        }
                    }
                    onState?.invoke("playing")
                }
            }

            override fun onConnectionChange(newState: PeerConnection.PeerConnectionState?) {
                when (newState) {
                    PeerConnection.PeerConnectionState.CONNECTED -> onState?.invoke("playing")
                    PeerConnection.PeerConnectionState.FAILED -> onState?.invoke("failed")
                    PeerConnection.PeerConnectionState.DISCONNECTED -> onState?.invoke("reconnecting")
                    PeerConnection.PeerConnectionState.CLOSED -> onState?.invoke("stopped")
                    else -> {}
                }
            }
        }) ?: throw IllegalStateException("No se pudo crear PeerConnection")

        peerConnection = pc

        val recvOnly = RtpTransceiver.RtpTransceiverInit(
            RtpTransceiver.RtpTransceiverDirection.RECV_ONLY
        )
        pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO, recvOnly)
        pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO, recvOnly)

        val constraints = MediaConstraints().apply {
            mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveVideo", "true"))
            mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveAudio", "true"))
        }

        val offer = suspendCancellableCoroutine<SessionDescription> { cont ->
            pc.createOffer(object : SdpObserver {
                override fun onCreateSuccess(sdp: SessionDescription?) {
                    if (sdp != null) cont.resumeWith(Result.success(sdp))
                    else cont.resumeWith(Result.failure(IllegalStateException("SDP vacío")))
                }

                override fun onCreateFailure(error: String?) {
                    cont.resumeWith(Result.failure(IllegalStateException(error ?: "createOffer falló")))
                }

                override fun onSetSuccess() {}
                override fun onSetFailure(error: String?) {}
            }, constraints)
        }

        suspendCancellableCoroutine<Unit> { cont ->
            pc.setLocalDescription(object : SdpObserver {
                override fun onSetSuccess() = cont.resumeWith(Result.success(Unit))
                override fun onSetFailure(error: String?) =
                    cont.resumeWith(Result.failure(IllegalStateException(error ?: "setLocalDescription falló")))

                override fun onCreateSuccess(sdp: SessionDescription?) {}
                override fun onCreateFailure(error: String?) {}
            }, offer)
        }

        waitIceComplete(pc, 2500)

        val sdp = pc.localDescription?.description ?: throw IllegalStateException("Sin SDP local")
        val url = "$baseUrl/api/webrtc?src=${java.net.URLEncoder.encode(camId, "UTF-8")}"
        val request = Request.Builder()
            .url(url)
            .post(sdp.toRequestBody("application/sdp".toMediaType()))
            .apply { if (cookie.isNotEmpty()) header("Cookie", "vs_session=$cookie") }
            .build()

        val answer = httpClient.newCall(request).execute().use { resp ->
            if (!resp.isSuccessful) {
                throw IllegalStateException("El servidor devolvió HTTP ${resp.code}")
            }
            resp.body?.string() ?: throw IllegalStateException("Respuesta SDP vacía")
        }

        val remote = SessionDescription(SessionDescription.Type.ANSWER, answer)
        suspendCancellableCoroutine<Unit> { cont ->
            pc.setRemoteDescription(object : SdpObserver {
                override fun onSetSuccess() {
                    onState?.invoke("playing")
                    cont.resumeWith(Result.success(Unit))
                }

                override fun onSetFailure(error: String?) =
                    cont.resumeWith(Result.failure(IllegalStateException(error ?: "setRemoteDescription falló")))

                override fun onCreateSuccess(sdp: SessionDescription?) {}
                override fun onCreateFailure(error: String?) {}
            }, remote)
        }
    }

    private suspend fun waitIceComplete(pc: PeerConnection, timeoutMs: Long) {
        val start = System.currentTimeMillis()
        while (pc.iceGatheringState() != PeerConnection.IceGatheringState.COMPLETE) {
            if (System.currentTimeMillis() - start > timeoutMs) break
            delay(100)
        }
    }

    private fun closePeer() {
        pendingTrack = null
        peerConnection?.let {
            try {
                it.close()
            } catch (_: Exception) {
            }
        }
        peerConnection = null
    }

    fun stop() {
        closePeer()
        onState?.invoke("stopped")
    }

    fun dispose() {
        detachRenderer()
        closePeer()
        scope.cancel()
        factory?.dispose()
        factory = null
        eglBase?.release()
        eglBase = null
    }
}
