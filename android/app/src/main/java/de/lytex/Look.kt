package de.lytex

import android.content.Context
import android.util.TypedValue
import android.webkit.JavascriptInterface
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate

/**
 * The design chosen in the dashboard (as the system, dark, light or high contrast), for the
 * app's own screens and the system bars too. The dashboard tells it through [Bridge].
 */
object Look {
    private val THEMES = setOf("auto", "dark", "light", "contrast")

    fun theme(context: Context): String =
        context.getSharedPreferences("piguard", Context.MODE_PRIVATE).getString("theme", null)?.takeIf { it in THEMES } ?: "auto"

    /** Call before super.onCreate. */
    fun apply(activity: AppCompatActivity) {
        val theme = theme(activity)
        activity.delegate.localNightMode = when (theme) {
            "dark", "contrast" -> AppCompatDelegate.MODE_NIGHT_YES
            "light" -> AppCompatDelegate.MODE_NIGHT_NO
            else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        }
        if (theme == "contrast") activity.setTheme(R.style.Theme_PiGuard_Contrast)
    }

    /** A colour of the current theme, such as R.attr.pgText. */
    fun color(context: Context, attr: Int): Int {
        val value = TypedValue()
        context.theme.resolveAttribute(attr, value, true)
        return value.data
    }

    /** Seen by the dashboard as window.PiGuardLook. Only takes one of the known names. */
    class Bridge(private val activity: AppCompatActivity, private val changed: () -> Unit) {
        @JavascriptInterface
        fun theme(name: String) {
            if (name !in THEMES || name == theme(activity)) return
            activity.getSharedPreferences("piguard", Context.MODE_PRIVATE).edit().putString("theme", name).apply()
            activity.runOnUiThread(changed)
        }
    }
}
