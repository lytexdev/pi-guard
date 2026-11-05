package de.lytex

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat

/**
 * How the app shows up on the phone. Android fixes the name and icon of a launcher entry when the
 * app is built, so there is a choice of entries. Anything else is a shortcut on the home screen.
 */
object Disguise {
    class Look(val alias: String, val name: Int)

    val looks = listOf(
        Look(".LookPiGuard", R.string.look_default),
        Look(".LookCalculator", R.string.look_calculator),
        Look(".LookNotes", R.string.look_notes),
        Look(".LookWeather", R.string.look_weather),
        Look(".LookClock", R.string.look_clock),
        Look(".LookFiles", R.string.look_files),
        Look(".LookMusic", R.string.look_music),
    )

    private fun component(context: Context, look: Look) = ComponentName(context.packageName, "de.lytex${look.alias}")

    private fun enabled(context: Context, look: Look): Boolean =
        when (context.packageManager.getComponentEnabledSetting(component(context, look))) {
            PackageManager.COMPONENT_ENABLED_STATE_ENABLED -> true
            PackageManager.COMPONENT_ENABLED_STATE_DISABLED -> false
            else -> look === looks.first() // as the manifest says
        }

    fun current(context: Context): Look = looks.firstOrNull { enabled(context, it) } ?: looks.first()

    fun isDefault(context: Context) = current(context) === looks.first()

    /** The new entry first: for a moment there are two, never none. */
    fun apply(context: Context, chosen: Look) {
        val manager = context.packageManager
        manager.setComponentEnabledSetting(component(context, chosen), PackageManager.COMPONENT_ENABLED_STATE_ENABLED, PackageManager.DONT_KILL_APP)
        looks.filter { it !== chosen }.forEach {
            manager.setComponentEnabledSetting(component(context, it), PackageManager.COMPONENT_ENABLED_STATE_DISABLED, PackageManager.DONT_KILL_APP)
        }
    }

    fun pinShortcut(context: Context, label: String, picture: Uri?) {
        val icon = picture?.let { square(context, it) }?.let(IconCompat::createWithBitmap)
            ?: IconCompat.createWithResource(context, R.mipmap.ic_launcher)
        val open = Intent(Intent.ACTION_MAIN).setClass(context, MainActivity::class.java)
        val shortcut = ShortcutInfoCompat.Builder(context, "own-${label.hashCode()}").setShortLabel(label).setIcon(icon).setIntent(open).build()
        ShortcutManagerCompat.requestPinShortcut(context, shortcut, null)
    }

    private fun square(context: Context, picture: Uri): Bitmap? = runCatching {
        val image = context.contentResolver.openInputStream(picture).use(BitmapFactory::decodeStream) ?: return null
        val side = minOf(image.width, image.height)
        val cut = Bitmap.createBitmap(image, (image.width - side) / 2, (image.height - side) / 2, side, side)
        Bitmap.createScaledBitmap(cut, ICON_SIZE, ICON_SIZE, true)
    }.getOrNull()

    private const val ICON_SIZE = 192
}
