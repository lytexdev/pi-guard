package de.lytex

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

/** Sends when the owner is busy in the next two weeks. Titles, places and people stay on the phone. */
object CalendarSync {
    private const val DAYS = 14L
    private const val LIMIT = 400

    fun run(context: Context, store: Store) {
        if (context.checkSelfPermission(Manifest.permission.READ_CALENDAR) != PackageManager.PERMISSION_GRANTED) return
        val now = System.currentTimeMillis()
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            ContentUris.appendId(it, now)
            ContentUris.appendId(it, now + DAYS * 86_400_000L)
        }.build()
        val columns = arrayOf(CalendarContract.Instances.BEGIN, CalendarContract.Instances.END, CalendarContract.Instances.AVAILABILITY, CalendarContract.Instances.ALL_DAY)
        val events = JSONArray()
        context.contentResolver.query(uri, columns, null, null, null)?.use {
            while (it.moveToNext() && events.length() < LIMIT) {
                val busy = it.getInt(2) == CalendarContract.Instances.AVAILABILITY_BUSY
                if (busy && it.getInt(3) == 0) {
                    events.put(JSONObject().put("start", Instant.ofEpochMilli(it.getLong(0)).toString()).put("end", Instant.ofEpochMilli(it.getLong(1)).toString()))
                }
            }
        }
        Net.post(store, "/api/mobile/calendar", JSONObject().put("events", events)).close()
    }
}
