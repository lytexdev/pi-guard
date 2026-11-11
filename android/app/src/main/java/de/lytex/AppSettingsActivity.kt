package de.lytex

import android.Manifest
import android.content.Intent
import android.os.Bundle
import android.webkit.CookieManager
import android.widget.Button
import android.widget.CompoundButton
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.appbar.MaterialToolbar
import kotlin.concurrent.thread

/** What belongs to this phone. Everything about pi-guard itself is in the dashboard. */
class AppSettingsActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var vpnState: TextView
    private val askCalendar = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        store.useCalendar = it
        findViewById<CompoundButton>(R.id.calendar).isChecked = it
    }
    private val askLocation = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        store.shareLocation = it[Manifest.permission.ACCESS_FINE_LOCATION] == true
        findViewById<CompoundButton>(R.id.location).isChecked = store.shareLocation
        if (store.shareLocation) askBackground.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
    }
    private val askBackground = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
    private val askSosMedia = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        store.sosMedia = it.values.any { granted -> granted }
        findViewById<CompoundButton>(R.id.sos_media).isChecked = store.sosMedia
    }
    private var shortcutPicture: android.net.Uri? = null
    private val pickPicture = registerForActivityResult(ActivityResultContracts.GetContent()) { shortcutPicture = it }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContentView(R.layout.activity_settings)
        store = Store(this)
        findViewById<MaterialToolbar>(R.id.toolbar).setNavigationOnClickListener { finish() }
        vpnState = findViewById(R.id.vpn_state)
        findViewById<Button>(R.id.vpn_toggle).setOnClickListener { startActivity(Intent(this, VpnActivity::class.java)) }
        findViewById<Button>(R.id.look_choose).setOnClickListener { chooseLook() }
        findViewById<Button>(R.id.look_shortcut).setOnClickListener { makeShortcut() }
        check(R.id.share_crashes, store.shareCrashes) { store.shareCrashes = it }
        check(R.id.share_usage, store.shareUsage) { store.shareUsage = it }
        check(R.id.location, store.shareLocation) {
            if (it) askLocation.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)) else store.shareLocation = false
        }
        check(R.id.sos_media, store.sosMedia) {
            if (it) askSosMedia.launch(arrayOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)) else store.sosMedia = false
        }
        check(R.id.calendar, store.useCalendar) {
            if (it) askCalendar.launch(Manifest.permission.READ_CALENDAR) else store.useCalendar = false
        }
        val strong = BiometricManager.from(this).canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS
        findViewById<CompoundButton>(R.id.biometric).isEnabled = strong
        check(R.id.biometric, store.useBiometric) { store.useBiometric = it }
        findViewById<Button>(R.id.pin_set).setOnClickListener { setPin() }
        findViewById<Button>(R.id.pin_remove).setOnClickListener { store.setPin(null) }
        findViewById<Button>(R.id.update).setOnClickListener { update() }
        val address = findViewById<EditText>(R.id.address)
        address.setText(store.url)
        findViewById<Button>(R.id.address_save).setOnClickListener { store.url = address.text.toString().trim().trimEnd('/') }
        findViewById<Button>(R.id.unpair).setOnClickListener {
            stopService(Intent(this, GuardService::class.java))
            thread { runCatching { Vpn.down(this, store) } }
            CookieManager.getInstance().removeAllCookies(null)
            store.clear()
            QuickActions.publish(this)
            startActivity(Intent(this, SetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK or Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    private fun check(id: Int, value: Boolean, changed: (Boolean) -> Unit) {
        val box = findViewById<CompoundButton>(id)
        box.isChecked = value
        box.setOnCheckedChangeListener { _, on -> changed(on) }
    }

    override fun onResume() {
        super.onResume()
        vpnState.setText(if (Vpn.running) R.string.vpn_on else R.string.vpn_off)
    }

    private fun chooseLook() {
        val names = Disguise.looks.map { getString(it.name) }.toTypedArray()
        val now = Disguise.looks.indexOf(Disguise.current(this))
        MaterialAlertDialogBuilder(this).setTitle(R.string.look_choose).setSingleChoiceItems(names, now) { dialog, chosen ->
            Disguise.apply(this, Disguise.looks[chosen])
            QuickActions.publish(this) // a disguised app shows no "Alarm" on its icon
            dialog.dismiss()
            Toast.makeText(this, R.string.look_done, Toast.LENGTH_LONG).show()
        }.setNegativeButton(R.string.cancel, null).show()
    }

    private fun makeShortcut() {
        shortcutPicture = null
        val name = EditText(this).apply { setHint(R.string.look_shortcut_name); setText(Disguise.current(this@AppSettingsActivity).name) }
        val side = (24 * resources.displayMetrics.density).toInt()
        val hint = TextView(this).apply { setText(R.string.look_shortcut_hint) }
        val pick = Button(this).apply { setText(R.string.look_shortcut_image); setOnClickListener { pickPicture.launch("image/*") } }
        val box = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(side, side / 3, side, 0)
            addView(hint); addView(name); addView(pick)
        }
        MaterialAlertDialogBuilder(this).setTitle(R.string.look_shortcut).setView(box)
            .setPositiveButton(R.string.look_shortcut_create) { _, _ ->
                val label = name.text.toString().trim()
                if (label.isNotEmpty()) Disguise.pinShortcut(this, label, shortcutPicture)
            }.setNegativeButton(R.string.cancel, null).show()
    }

    /** The same number pad as the lock screen, and the PIN typed twice. */
    private fun setPin() {
        var first: String? = null
        val title = android.widget.TextView(this).apply {
            setText(R.string.pin_hint)
            gravity = android.view.Gravity.CENTER
            setTextColor(getColor(R.color.muted))
        }
        lateinit var dialog: androidx.appcompat.app.AlertDialog
        val pad = PinPad(this) { pin ->
            when {
                pin.length < 4 -> Toast.makeText(this, R.string.pin_hint, Toast.LENGTH_SHORT).show()
                first == null -> { first = pin; title.setText(R.string.pin_repeat) }
                pin == first -> { store.setPin(pin); Toast.makeText(this, R.string.pin_saved, Toast.LENGTH_SHORT).show(); dialog.dismiss() }
                else -> { first = null; title.setText(R.string.pin_mismatch) }
            }
        }
        val side = (16 * resources.displayMetrics.density).toInt()
        val box = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            gravity = android.view.Gravity.CENTER_HORIZONTAL
            setPadding(side, side, side, 0)
            addView(title)
            addView(pad)
        }
        dialog = MaterialAlertDialogBuilder(this).setTitle(R.string.pin_set).setView(box)
            .setNegativeButton(R.string.cancel, null).show()
    }

    private fun update() {
        thread {
            val meta = runCatching { Updater.latest(store) }.getOrNull()
            runOnUiThread {
                if (meta == null || !Updater.isNewer(this, meta)) {
                    Toast.makeText(this, R.string.update_none, Toast.LENGTH_LONG).show()
                } else {
                    MaterialAlertDialogBuilder(this).setMessage(getString(R.string.update_found, meta.getString("version_name")))
                        .setPositiveButton(R.string.update_install) { _, _ ->
                            thread {
                                val fine = runCatching { Updater.install(this, store, meta) }.getOrDefault(false)
                                if (!fine) runOnUiThread { Toast.makeText(this, R.string.update_failed, Toast.LENGTH_LONG).show() }
                            }
                        }.setNegativeButton(R.string.cancel, null).show()
                }
            }
        }
    }
}
