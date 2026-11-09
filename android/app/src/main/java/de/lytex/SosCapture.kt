package de.lytex

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.ImageReader
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Size
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.abs

/**
 * Photos and a sound clip after an SOS, only if the person switched this on (Store.sosMedia).
 * Runs while the emergency screen is open: Android gives camera and microphone only to an app
 * in front, and nothing is taken secretly in the background.
 */
object SosCapture {
    const val CLIP_SECONDS = 20
    private const val WARM_UP_MS = 1200L // lets exposure settle, there is no preview
    private const val TARGET_PIXELS = 2_000_000

    fun allowed(context: Context, permission: String) =
        context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    /** Back camera first, it shows the place; then the front one. Each is sent at once. */
    fun photos(context: Context, store: Store): Int {
        if (!allowed(context, Manifest.permission.CAMERA)) return 0
        val manager = context.getSystemService(CameraManager::class.java)
        val ids = listOf(CameraCharacteristics.LENS_FACING_BACK, CameraCharacteristics.LENS_FACING_FRONT).mapNotNull { facing ->
            manager.cameraIdList.firstOrNull { manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == facing }
        }
        return ids.count { id -> runCatching { photo(manager, id)?.let { upload(store, "photo", it, "image/jpeg") } == true }.getOrDefault(false) }
    }

    @SuppressLint("MissingPermission") // checked in photos()
    private fun photo(manager: CameraManager, id: String): ByteArray? {
        val thread = HandlerThread("sos-camera").apply { start() }
        val handler = Handler(thread.looper)
        val sizes = manager.getCameraCharacteristics(id).get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return null
        val size = sizes.getOutputSizes(ImageFormat.JPEG).minByOrNull { abs(it.width * it.height - TARGET_PIXELS) } ?: return null
        val small = sizes.getOutputSizes(ImageFormat.YUV_420_888).minByOrNull { abs(it.width * it.height - 640 * 480) } ?: Size(640, 480)
        val still = ImageReader.newInstance(size.width, size.height, ImageFormat.JPEG, 1)
        val warm = ImageReader.newInstance(small.width, small.height, ImageFormat.YUV_420_888, 2)
        warm.setOnImageAvailableListener({ it.acquireLatestImage()?.close() }, handler)
        val result = AtomicReference<ByteArray?>()
        val done = CountDownLatch(1)
        still.setOnImageAvailableListener({ reader ->
            reader.acquireLatestImage()?.use { image ->
                val buffer = image.planes[0].buffer
                result.set(ByteArray(buffer.remaining()).also { buffer.get(it) })
            }
            done.countDown()
        }, handler)
        var camera: CameraDevice? = null
        try {
            manager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    camera = device
                    @Suppress("DEPRECATION")
                    device.createCaptureSession(listOf(warm.surface, still.surface), object : CameraCaptureSession.StateCallback() {
                        override fun onConfigured(session: CameraCaptureSession) {
                            val preview = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply { addTarget(warm.surface) }.build()
                            session.setRepeatingRequest(preview, null, handler)
                            handler.postDelayed({
                                runCatching {
                                    val shot = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                                        addTarget(still.surface)
                                        set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
                                    }.build()
                                    session.capture(shot, null, handler)
                                }.onFailure { done.countDown() }
                            }, WARM_UP_MS)
                        }

                        override fun onConfigureFailed(session: CameraCaptureSession) = done.countDown()
                    }, handler)
                }

                override fun onDisconnected(device: CameraDevice) { device.close(); done.countDown() }
                override fun onError(device: CameraDevice, error: Int) { device.close(); done.countDown() }
            }, handler)
            done.await(8, TimeUnit.SECONDS)
        } finally {
            runCatching { camera?.close() }
            still.close()
            warm.close()
            thread.quitSafely()
        }
        return result.get()
    }

    /** CLIP_SECONDS of sound as M4A. Blocks for that long. */
    fun clip(context: Context, store: Store): Boolean {
        if (!allowed(context, Manifest.permission.RECORD_AUDIO)) return false
        val file = File(context.cacheDir, "sos.m4a")
        return try {
            @Suppress("DEPRECATION")
            val recorder = (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(context) else MediaRecorder()).apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioEncodingBitRate(64_000)
                setAudioSamplingRate(44_100)
                setOutputFile(file)
                prepare()
                start()
            }
            Thread.sleep(CLIP_SECONDS * 1000L)
            runCatching { recorder.stop() }
            recorder.release()
            upload(store, "audio", file.readBytes(), "audio/mp4")
        } catch (_: Exception) {
            false
        } finally {
            file.delete() // nothing of it stays on the phone
        }
    }

    private fun upload(store: Store, kind: String, bytes: ByteArray, type: String): Boolean =
        Net.client(store).newCall(
            Net.request(store, "/api/mobile/emergency/media?kind=$kind").post(bytes.toRequestBody(type.toMediaType())).build(),
        ).execute().use { it.isSuccessful }
}
