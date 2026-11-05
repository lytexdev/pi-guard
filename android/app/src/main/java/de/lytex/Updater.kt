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

/** Updates from your own pi-guard. Android refuses any file not signed with this app's key. */
object Updater {
    fun latest(store: Store): JSONObject? =
        Net.client(store).newCall(Net.request(store, "/api/mobile/app/latest").build()).execute().use {
            if (it.isSuccessful) JSONObject(it.body!!.string()).takeIf { meta -> meta.has("version_code") } else null
        }

    fun isNewer(context: Context, meta: JSONObject): Boolean =
        meta.getLong("version_code") > context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode

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
