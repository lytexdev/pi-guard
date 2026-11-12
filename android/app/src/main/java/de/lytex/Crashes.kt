package de.lytex

import android.content.Context
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Crash reports, only with the person's consent (Store.shareCrashes, off by default).
 *
 * A crash writes the exception's type and where in the code it happened, never its message,
 * which can hold addresses or names. On the next start the report goes to the person's own
 * pi-guard if they agreed, and is deleted either way. Their pi-guard passes it on only if its
 * owner switched on the anonymous statistics.
 */
object Crashes {
    private const val FILE = "crash.json"
    private const val FRAMES = 12

    fun install(context: Context) {
        val app = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching { File(app.filesDir, FILE).writeText(describe(app, error).toString()) }
            previous?.uncaughtException(thread, error)
        }
    }

    fun describe(context: Context, error: Throwable): JSONObject {
        var root = error
        while (root.cause != null && root.cause !== root) root = root.cause!!
        val frames = JSONArray()
        root.stackTrace.take(FRAMES).forEach { frames.put("${it.className}.${it.methodName}:${it.lineNumber}") }
        return JSONObject()
            .put("exception", root.javaClass.name)
            .put("frames", frames)
            .put("app", runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull() ?: "")
            .put("sdk", Build.VERSION.SDK_INT)
    }

    /** On start: the anonymous statistics are off for now, so a waiting report is only deleted. */
    @Suppress("UNUSED_PARAMETER")
    fun sendPending(context: Context, store: Store) {
        File(context.filesDir, FILE).delete()
    }
}
