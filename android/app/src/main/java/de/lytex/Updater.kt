package de.lytex

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** Updates from your own pi-guard. Android refuses any file not signed with this app's key. */
object Updater {
    /** With [ask], pi-guard looks on GitHub first, which can take a minute. */
    fun latest(store: Store, ask: Boolean = false): JSONObject? {
        val client = if (ask) Net.client(store).newBuilder().readTimeout(100, TimeUnit.SECONDS).build() else Net.client(store)
        val path = if (ask) "/api/mobile/app/latest?check=1" else "/api/mobile/app/latest"
        return client.newCall(Net.request(store, path).build()).execute().use {
            if (it.isSuccessful) JSONObject(it.body!!.string()).takeIf { meta -> meta.has("version_code") } else null
        }
    }

    fun isNewer(context: Context, meta: JSONObject): Boolean =
        meta.getLong("version_code") > context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode

    /**
     * "major", "minor" or "patch" for a newer build, as `step` in core/app_updates.py on the Pi:
     * a new major or minor version installs by itself, a patch is only offered.
     */
    fun step(context: Context, meta: JSONObject): String? {
        if (!isNewer(context, meta)) return null
        val parts = { name: String? -> name?.let { Regex("""^(\d+)\.(\d+)\.(\d+)""").find(it) }?.groupValues?.drop(1) }
        val new = parts(meta.optString("version_name")) ?: return "patch"
        val old = parts(context.packageManager.getPackageInfo(context.packageName, 0).versionName) ?: return "patch"
        return when {
            new[0] != old[0] -> "major"
            new[1] != old[1] -> "minor"
            else -> "patch"
        }
    }

    /** "Version X available" (or "being installed"), once per version. A tap installs it. */
    fun announce(context: Context, store: Store, meta: JSONObject, installing: Boolean) {
        val code = meta.getLong("version_code")
        if (store.updateTold >= code) return
        store.updateTold = code
        val name = meta.optString("version_name")
        val tap = PendingIntent.getBroadcast(context, 5, Intent(context, InstallReceiver::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val text = context.getString(if (installing) R.string.update_installing else R.string.update_offer)
        val note = NotificationCompat.Builder(context, GuardService.CHANNEL_ALERTS).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(context.getString(R.string.update_found, name)).setContentText(text)
            .setContentIntent(tap).setAutoCancel(true).build()
        context.getSystemService(NotificationManager::class.java).notify(GuardService.UPDATE_ID, note)
    }

    /**
     * Android checks that the file carries this app's signature, and without it refuses to update.
     * From the second update on it needs no tap: the app is then the installer of its own updates.
     */
    private fun commit(context: Context, file: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("PiGuard.apk", 0, file.length()).use { out ->
                file.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val done = Intent(context, UpdateReceiver::class.java)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE // Android adds the result
            session.commit(PendingIntent.getBroadcast(context, id, done, flags).intentSender)
        }
        file.delete()
    }

    /** Returns false if the download does not match the checksum. */
    fun install(context: Context, store: Store, meta: JSONObject): Boolean {
        val folder = File(context.cacheDir, "updates").apply { mkdirs() }
        val file = File(folder, "PiGuard.apk")
        val digest = MessageDigest.getInstance("SHA-256")
        Net.client(store).newCall(Net.request(store, "/api/mobile/app/apk").build()).execute().use {
            check(it.isSuccessful)
            file.outputStream().use { out ->
                val input = it.body!!.byteStream()
                val buffer = ByteArray(1 shl 16)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    digest.update(buffer, 0, read)
                    out.write(buffer, 0, read)
                }
            }
        }
        if (digest.digest().joinToString("") { "%02x".format(it) } != meta.getString("sha256")) {
            file.delete()
            return false
        }
        commit(context, file)
        return true
    }
}

/** A tap on "Version X available": fetch and install it. Only this app's own signed update. */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        val store = Store(context)
        thread {
            try {
                runCatching { Updater.latest(store)?.takeIf { Updater.isNewer(context, it) }?.let { Updater.install(context, store, it) } }
            } finally {
                pending.finish()
            }
        }
    }
}

/** Android's answer to an update. The first one still needs a tap, which comes as a notification. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, 1) != PackageInstaller.STATUS_PENDING_USER_ACTION) return
        @Suppress("DEPRECATION")
        val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
        val open = PendingIntent.getActivity(context, 4, confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val note = NotificationCompat.Builder(context, GuardService.CHANNEL_ALERTS).setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(context.getString(R.string.update_ready)).setContentIntent(open).setAutoCancel(true).build()
        context.getSystemService(NotificationManager::class.java).notify(GuardService.UPDATE_ID, note)
    }
}
