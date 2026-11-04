package de.lytex

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
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
        val uri = FileProvider.getUriForFile(context, "de.lytex.files", file)
        context.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
        return true
    }
}
