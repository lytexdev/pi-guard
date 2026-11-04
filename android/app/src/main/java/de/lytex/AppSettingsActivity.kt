package de.lytex

import android.Manifest
import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.webkit.CookieManager
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import kotlin.concurrent.thread

/** What belongs to this phone. Everything about pi-guard itself is in the dashboard. */
class AppSettingsActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var vpnState: TextView
    private val askCalendar = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        store.useCalendar = it
        findViewById<CheckBox>(R.id.calendar).isChecked = it
    }
    private val askLocation = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        store.shareLocation = it[Manifest.permission.ACCESS_FINE_LOCATION] == true
        findViewById<CheckBox>(R.id.location).isChecked = store.shareLocation
        if (store.shareLocation) askBackground.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
    }
    private val askVpn = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == RESULT_OK) toggleVpn()
    }
    private val askBackground = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContentView(R.layout.activity_settings)
        store = Store(this)
        vpnState = findViewById(R.id.vpn_state)
        showVpn()
        findViewById<Button>(R.id.vpn_toggle).setOnClickListener { toggleVpn() }
        check(R.id.location, store.shareLocation) {
            if (it) askLocation.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)) else store.shareLocation = false
        }
        check(R.id.calendar, store.useCalendar) {
            if (it) askCalendar.launch(Manifest.permission.READ_CALENDAR) else store.useCalendar = false
        }
        val strong = BiometricManager.from(this).canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS
        findViewById<CheckBox>(R.id.biometric).isEnabled = strong
        check(R.id.biometric, store.useBiometric) { store.useBiometric = it }
        findViewById<Button>(R.id.pin_set).setOnClickListener { setPin() }
        findViewById<Button>(R.id.pin_remove).setOnClickListener { store.setPin(null) }
        findViewById<Button>(R.id.update).setOnClickListener { update() }
        val address = findViewById<EditText>(R.id.address)
        address.setText(store.url)
        findViewById<Button>(R.id.address_save).setOnClickListener { store.url = address.text.toString().trim().trimEnd('/') }
        findViewById<Button>(R.id.unpair).setOnClickListener {
            stopService(Intent(this, GuardService::class.java))
            CookieManager.getInstance().removeAllCookies(null)
            store.clear()
            startActivity(Intent(this, SetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK or Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    private fun check(id: Int, value: Boolean, changed: (Boolean) -> Unit) {
        val box = findViewById<CheckBox>(id)
        box.isChecked = value
        box.setOnCheckedChangeListener { _, on -> changed(on) }
    }

    private fun showVpn() {
        vpnState.setText(if (Vpn.running) R.string.vpn_on else R.string.vpn_off)
        findViewById<Button>(R.id.vpn_toggle).setText(if (Vpn.running) R.string.disconnect_vpn else R.string.connect_vpn)
    }

    private fun toggleVpn() {
        VpnService.prepare(this)?.let { return askVpn.launch(it) }
        thread {
            val done = runCatching { if (Vpn.running) Vpn.down(this) else Vpn.up(this, store) }.isSuccess
            runOnUiThread {
                showVpn()
                if (!done) Toast.makeText(this, R.string.vpn_failed, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun setPin() {
        val input = EditText(this).apply {
            inputType = android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_VARIATION_PASSWORD
            setHint(R.string.pin_hint)
        }
        AlertDialog.Builder(this).setTitle(R.string.pin_set).setView(input)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                if (input.text.length >= 4) store.setPin(input.text.toString())
            }.setNegativeButton(R.string.cancel, null).show()
    }

    private fun update() {
        thread {
            val meta = runCatching { Updater.latest(store) }.getOrNull()
            runOnUiThread {
                if (meta == null || !Updater.isNewer(this, meta)) {
                    Toast.makeText(this, R.string.update_none, Toast.LENGTH_LONG).show()
                } else {
                    AlertDialog.Builder(this).setMessage(getString(R.string.update_found, meta.getString("version_name")))
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
