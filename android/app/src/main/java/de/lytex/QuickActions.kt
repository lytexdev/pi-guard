package de.lytex

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.ShortcutManager
import android.widget.Toast
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Legacy quick actions and launcher shortcuts. The in-app menu is gone; publish() only clears
 * leftover shortcuts. Deep links from older shortcuts still work through QuickActionActivity.
 */
object QuickActions {
    class Action(val id: String, val label: Int, val icon: Int, val confirm: Boolean = false)

    val all = listOf(
        Action("ack", R.string.qa_ack, R.drawable.ic_check),
        Action("call", R.string.qa_call, R.drawable.ic_phone),
        Action("away", R.string.qa_away, R.drawable.ic_shield),
        Action("home", R.string.qa_home, R.drawable.ic_home),
        Action("alarm", R.string.qa_alarm, R.drawable.ic_bell, confirm = true),
        Action("alarm_stop", R.string.qa_alarm_stop, R.drawable.ic_bell_off),
        Action("sos", R.string.sos, R.drawable.ic_alert_triangle),
        Action("tutorial", R.string.qa_tutorial, R.drawable.ic_search),
    )
    const val EXTRA = "de.lytex.QUICK"
    const val ROUTE = "de.lytex.ROUTE"

    /** Clears dynamic shortcuts; the quick-actions menu is no longer offered. */
    fun publish(context: Context) {
        context.getSystemService(ShortcutManager::class.java)?.removeAllDynamicShortcuts()
    }

    /** Runs an action; the alarm asks first, since it is loud for everyone at home. */
    fun start(activity: Activity, id: String, done: () -> Unit = {}) {
        val action = all.firstOrNull { it.id == id } ?: return done()
        when (id) {
            "sos" -> { activity.startActivity(Intent(activity, EmergencyActivity::class.java)); done() }
            // A call is the Live page's call button: the browser needs a tap there to use the microphone.
            "call" -> {
                activity.startActivity(Intent(activity, MainActivity::class.java).putExtra(ROUTE, "#/live").addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP))
                done()
            }
            // The dashboard's help page with its tour; nothing goes to the Pi.
            "tutorial" -> {
                activity.startActivity(Intent(activity, MainActivity::class.java).putExtra(ROUTE, "#/help/tour").addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP))
                done()
            }
            else -> if (action.confirm) {
                MaterialAlertDialogBuilder(activity).setMessage(R.string.qa_alarm_confirm)
                    .setPositiveButton(action.label) { _, _ -> send(activity, id, done) }
                    .setNegativeButton(R.string.cancel) { _, _ -> done() }
                    .setOnCancelListener { done() }.show()
            } else {
                send(activity, id, done)
            }
        }
    }

    private fun send(activity: Activity, id: String, done: () -> Unit) {
        val store = Store(activity)
        thread {
            val message = runCatching {
                Net.post(store, "/api/mobile/action", JSONObject().put("action", id)).use {
                    when (it.code) {
                        200 -> {
                            val body = JSONObject(it.body!!.string())
                            when (id) {
                                "ack" -> activity.getString(R.string.qa_done_ack, body.optInt("acknowledged"))
                                "away", "home" -> activity.getString(R.string.qa_done_mode, activity.getString(if (id == "away") R.string.qa_away else R.string.qa_home))
                                else -> activity.getString(R.string.qa_done)
                            }
                        }
                        403 -> activity.getString(R.string.action_admin_only)
                        else -> activity.getString(R.string.action_failed)
                    }
                }
            }.getOrElse { activity.getString(R.string.action_failed) }
            activity.runOnUiThread {
                Toast.makeText(activity, message, Toast.LENGTH_LONG).show()
                done()
            }
        }
    }
}
