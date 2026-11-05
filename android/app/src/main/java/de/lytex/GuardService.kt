package de.lytex

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.BitmapFactory
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Base64
import android.webkit.CookieManager
import androidx.core.app.NotificationCompat
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.Executors

/** The live connection to pi-guard. Alerts arrive here, without Google or anyone else in between. */
class GuardService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val work = Executors.newSingleThreadExecutor()
    private var socket: WebSocket? = null
    private var ringtone: Ringtone? = null
    private var alerts = ALERT_BASE
    private var checkedUpdate = 0L
    private var retryAfter = RETRY_AFTER
    private lateinit var store: Store

    private val reconnect = Runnable { connect() }
    private val periodic = object : Runnable {
        override fun run() {
            work.execute {
                runCatching { send(Locator.report(this@GuardService, store.shareLocation)) }
                if (store.useCalendar) runCatching { CalendarSync.run(this@GuardService, store) }
                runCatching { offerUpdate() }
            }
            main.postDelayed(this, REPORT_EVERY)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_shield).setContentTitle(getString(R.string.service_title))
            .setContentIntent(open).setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(SERVICE_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(SERVICE_ID, notification)
        }
        connect()
        main.post(periodic)
        work.execute { Vpn.resume(this, store) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP_RING) stopRing()
        return START_STICKY
    }

    /** Android asks before installing an app that does not come from a store, so this only offers. */
    private fun offerUpdate() {
        if (System.currentTimeMillis() - checkedUpdate < UPDATE_EVERY) return
        checkedUpdate = System.currentTimeMillis()
        val meta = Updater.latest(store) ?: return
        if (!Updater.isNewer(this, meta)) return
        val open = PendingIntent.getActivity(this, 3, Intent(this, AppSettingsActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, CHANNEL_ALERTS).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(getString(R.string.update_found, meta.getString("version_name")))
            .setContentIntent(open).setAutoCancel(true).build()
        getSystemService(NotificationManager::class.java).notify(UPDATE_ID, notification)
    }

    private fun connect() {
        if (store.token == null) return stopSelf()
        val url = store.url!!.replaceFirst("https://", "wss://")
        val request = Net.request(store, "").url("$url/api/mobile/ws").build()
        socket = Net.client(store).newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                main.removeCallbacks(reconnect)
                retryAfter = RETRY_AFTER
            }

            override fun onMessage(webSocket: WebSocket, text: String) = received(JSONObject(text))
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (response?.code == 401) {
                    store.forgetLogin()
                    main.post {
                        CookieManager.getInstance().removeAllCookies(null)
                        stopSelf()
                    }
                } else {
                    retry()
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = retry()
        })
    }

    private fun retry() {
        main.removeCallbacks(reconnect)
        main.postDelayed(reconnect, retryAfter)
        retryAfter = minOf(retryAfter * 2, RETRY_MAX) // away from home this saves the battery
    }

    private fun send(report: JSONObject) {
        socket?.send(report.put("type", "report").toString())
    }

    private fun received(message: JSONObject) {
        when (message.optString("type")) {
            "alert" -> showAlert(message)
            "find" -> if (message.optBoolean("ring")) {
                main.post(::ring)
                // The owner decides: the phone rings either way, its location only goes out if shared.
                work.execute { runCatching { send(Locator.report(this, store.shareLocation)) } }
            } else {
                main.post(::stopRing)
            }
        }
    }

    private fun showAlert(message: JSONObject) {
        val text = message.optString("text")
        val open = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val builder = NotificationCompat.Builder(this, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_bell).setContentTitle(getString(R.string.alert_title))
            .setContentText(text).setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open).setAutoCancel(true).setPriority(NotificationCompat.PRIORITY_HIGH)
        message.optString("photo").takeIf { it.isNotEmpty() }?.let {
            val bytes = Base64.decode(it, Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.let { picture ->
                builder.setStyle(NotificationCompat.BigPictureStyle().bigPicture(picture).setSummaryText(text))
            }
        }
        getSystemService(NotificationManager::class.java).notify(alerts++, builder.build())
    }

    private fun ring() {
        stopRing()
        val audio = getSystemService(AudioManager::class.java)
        audio.setStreamVolume(AudioManager.STREAM_ALARM, audio.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0)
        val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM) ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
        ringtone = RingtoneManager.getRingtone(this, uri)?.apply {
            audioAttributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).build()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) isLooping = true
            play()
        }
        @Suppress("DEPRECATION")
        getSystemService(Vibrator::class.java).vibrate(VibrationEffect.createWaveform(longArrayOf(0, 800, 400), 0))
        val stop = PendingIntent.getService(this, 2, Intent(this, GuardService::class.java).setAction(ACTION_STOP_RING), PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, CHANNEL_FIND).setSmallIcon(R.drawable.ic_bell)
            .setContentTitle(getString(R.string.find_title)).setPriority(NotificationCompat.PRIORITY_MAX)
            .addAction(0, getString(R.string.find_stop), stop).setOngoing(true).build()
        getSystemService(NotificationManager::class.java).notify(FIND_ID, notification)
        main.postDelayed(::stopRing, RING_FOR)
    }

    private fun stopRing() {
        ringtone?.stop()
        ringtone = null
        @Suppress("DEPRECATION")
        getSystemService(Vibrator::class.java).cancel()
        getSystemService(NotificationManager::class.java).cancel(FIND_ID)
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        socket?.cancel()
        work.shutdown()
        stopRing()
        super.onDestroy()
    }

    companion object {
        const val CHANNEL_ALERTS = "alerts"
        const val CHANNEL_SERVICE = "service"
        const val CHANNEL_FIND = "find"
        private const val SERVICE_ID = 1
        private const val FIND_ID = 2
        private const val UPDATE_ID = 3
        private const val UPDATE_EVERY = 24 * 3600_000L
        private const val ALERT_BASE = 100
        private const val RETRY_AFTER = 15_000L
        private const val RETRY_MAX = 300_000L
        private const val REPORT_EVERY = 15 * 60_000L
        private const val RING_FOR = 120_000L
        private const val ACTION_STOP_RING = "de.lytex.STOP_RING"

        fun start(context: Context) {
            if (Store(context).token == null) return
            try {
                context.startForegroundService(Intent(context, GuardService::class.java))
            } catch (_: IllegalStateException) {
                // Android may refuse this from the background; opening the app starts it again.
            }
        }

        fun channels(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL_ALERTS, context.getString(R.string.channel_alerts), NotificationManager.IMPORTANCE_HIGH))
            manager.createNotificationChannel(NotificationChannel(CHANNEL_SERVICE, context.getString(R.string.channel_service), NotificationManager.IMPORTANCE_MIN))
            manager.createNotificationChannel(NotificationChannel(CHANNEL_FIND, context.getString(R.string.channel_find), NotificationManager.IMPORTANCE_HIGH).apply { lockscreenVisibility = Notification.VISIBILITY_PUBLIC })
        }
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) GuardService.start(context)
    }
}
