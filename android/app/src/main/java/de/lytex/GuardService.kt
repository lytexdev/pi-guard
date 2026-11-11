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
import android.net.ConnectivityManager
import android.net.Network
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
    // A lost phone, or after an SOS: the place every minute, until found or the time is up.
    private var trackUntil = 0L
    private var lost = false
    private var trackEvery = TRACK_EVERY
    private val tracking = object : Runnable {
        override fun run() {
            if (System.currentTimeMillis() > trackUntil) return stopTracking()
            // After an SOS the person asked for it; a lost phone only if sharing is on.
            work.execute { runCatching { send(Locator.report(this@GuardService, !lost || store.shareLocation)) } }
            main.postDelayed(this, trackEvery)
        }
    }
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

    private val networkChange = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            work.execute { Vpn.autoConnect(this@GuardService, store) }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_shield).setContentTitle(if (Disguise.isDefault(this)) getString(R.string.service_title) else getString(Disguise.current(this).name))
            .setContentIntent(open).setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(SERVICE_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(SERVICE_ID, notification)
        }
        connect()
        main.post(periodic)
        work.execute { Crashes.sendPending(this, store) }
        work.execute { Vpn.resume(this, store) }
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(networkChange)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            // A lost phone stays lost: only the dashboard ends that, whoever holds the phone.
            ACTION_TRACK -> if (!lost) startTracking(System.currentTimeMillis() + EMERGENCY_FOR, false, TRACK_EVERY, getString(R.string.sos_tracking))
            ACTION_SAFE -> if (!lost) {
                main.post(::stopTracking)
                work.execute { runCatching { Net.post(store, "/api/mobile/emergency/end", JSONObject()).close() } }
            }
            ACTION_STOP_RING -> stopRing()
            ACTION_ALERT_ACK -> act("ack", intent.getIntExtra(EXTRA_ALERT, 0))
            ACTION_ALERT_ALARM -> act("alarm", intent.getIntExtra(EXTRA_ALERT, 0))
        }
        return START_STICKY
    }

    /** A button under an alert. The Pi decides whether this phone's owner may do it. */
    private fun act(action: String, alert: Int) {
        val notifications = getSystemService(NotificationManager::class.java)
        if (action == "ack") notifications.cancel(alert)
        work.execute {
            val code = runCatching { Net.post(store, "/api/mobile/action", JSONObject().put("action", action)).use { it.code } }.getOrDefault(0)
            val text = when {
                code == 200 && action == "ack" -> getString(R.string.action_ack_done)
                code == 200 -> getString(R.string.action_alarm_done)
                code == 403 -> getString(R.string.action_admin_only)
                else -> getString(R.string.action_failed)
            }
            val note = NotificationCompat.Builder(this, CHANNEL_ALERTS).setSmallIcon(R.drawable.ic_bell)
                .setContentTitle(getString(R.string.alert_title)).setContentText(text).setAutoCancel(true)
                .setTimeoutAfter(15_000).build()
            notifications.notify(ACTION_RESULT_ID, note)
        }
    }

    /** On Wi-Fi only: an app update is no reason to use up someone's mobile data. */
    private fun offerUpdate() {
        if (System.currentTimeMillis() - checkedUpdate < UPDATE_EVERY) return
        if (getSystemService(ConnectivityManager::class.java).isActiveNetworkMetered) return
        checkedUpdate = System.currentTimeMillis()
        val meta = Updater.latest(store) ?: return
        if (Updater.isNewer(this, meta)) Updater.install(this, store, meta)
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
        val wait = retryAfter
        retryAfter = minOf(retryAfter * 2, RETRY_MAX) // away from home this saves the battery
        work.execute {
            runCatching { Net.pick(store) }
            main.postDelayed(reconnect, wait)
        }
    }

    private fun send(report: JSONObject) {
        socket?.send(report.put("type", "report").toString())
    }

    private fun startTracking(until: Long, isLost: Boolean, every: Long, text: String) {
        main.post {
            lost = isLost
            trackUntil = until
            trackEvery = every.coerceIn(30_000L, 600_000L)
            main.removeCallbacks(tracking)
            main.post(tracking)
            val builder = NotificationCompat.Builder(this, CHANNEL_EMERGENCY).setSmallIcon(R.drawable.ic_map_pin)
                .setContentTitle(getString(if (isLost) R.string.lost_title else R.string.sos_tracking_title))
                .setContentText(text).setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setOngoing(true).setPriority(NotificationCompat.PRIORITY_HIGH).setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            if (!isLost) builder.addAction(0, getString(R.string.sos_safe), PendingIntent.getService(this, 5,
                Intent(this, GuardService::class.java).setAction(ACTION_SAFE), PendingIntent.FLAG_IMMUTABLE))
            getSystemService(NotificationManager::class.java).notify(TRACK_ID, builder.build())
        }
    }

    private fun stopTracking() {
        main.removeCallbacks(tracking)
        trackUntil = 0L
        lost = false
        getSystemService(NotificationManager::class.java).cancel(TRACK_ID)
    }

    private fun received(message: JSONObject) {
        val every = message.optLong("every", 60) * 1000
        when (message.optString("type")) {
            "alert" -> showAlert(message)
            "lost" -> startTracking(Long.MAX_VALUE, true, every,
                listOf(message.optString("message"), getString(R.string.lost_text)).filter { it.isNotBlank() }.joinToString("\n"))
            "emergency" -> if (trackUntil == 0L) startTracking(System.currentTimeMillis() + EMERGENCY_FOR, false, every, getString(R.string.sos_tracking))
            "found" -> main.post(::stopTracking)
            // A fresh place for a map link, without ringing; only if this phone shares it.
            "locate" -> work.execute { runCatching { send(Locator.report(this, store.shareLocation)) } }
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
        val id = alerts++
        fun button(action: String, label: Int) = builder.addAction(0, getString(label), PendingIntent.getService(
            this, id * 2 + if (action == ACTION_ALERT_ACK) 0 else 1,
            Intent(this, GuardService::class.java).setAction(action).putExtra(EXTRA_ALERT, id), PendingIntent.FLAG_IMMUTABLE))
        button(ACTION_ALERT_ACK, R.string.action_ack)
        button(ACTION_ALERT_ALARM, R.string.action_alarm)
        message.optString("photo").takeIf { it.isNotEmpty() }?.let {
            val bytes = Base64.decode(it, Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.let { picture ->
                builder.setStyle(NotificationCompat.BigPictureStyle().bigPicture(picture).setSummaryText(text))
            }
        }
        getSystemService(NotificationManager::class.java).notify(id, builder.build())
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
        getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(networkChange)
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
        const val CHANNEL_EMERGENCY = "emergency"
        const val ACTION_TRACK = "de.lytex.TRACK"
        const val ACTION_SAFE = "de.lytex.SAFE"
        private const val TRACK_ID = 5
        private const val TRACK_EVERY = 60_000L
        private const val EMERGENCY_FOR = 30 * 60_000L
        private const val SERVICE_ID = 1
        private const val FIND_ID = 2
        const val UPDATE_ID = 3
        private const val UPDATE_EVERY = 24 * 3600_000L
        private const val ALERT_BASE = 100
        private const val RETRY_AFTER = 15_000L
        private const val RETRY_MAX = 300_000L
        private const val REPORT_EVERY = 15 * 60_000L
        private const val RING_FOR = 120_000L
        private const val ACTION_STOP_RING = "de.lytex.STOP_RING"
        private const val ACTION_ALERT_ACK = "de.lytex.ALERT_ACK"
        private const val ACTION_ALERT_ALARM = "de.lytex.ALERT_ALARM"
        private const val EXTRA_ALERT = "alert"
        private const val ACTION_RESULT_ID = 4

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
            manager.createNotificationChannel(NotificationChannel(CHANNEL_EMERGENCY, context.getString(R.string.channel_emergency), NotificationManager.IMPORTANCE_HIGH).apply { lockscreenVisibility = Notification.VISIBILITY_PUBLIC })
            manager.createNotificationChannel(NotificationChannel(CHANNEL_FIND, context.getString(R.string.channel_find), NotificationManager.IMPORTANCE_HIGH).apply { lockscreenVisibility = Notification.VISIBILITY_PUBLIC })
        }
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // After an update Android does not start anything by itself.
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) GuardService.start(context)
    }
}
