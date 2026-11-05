package de.lytex

import android.content.Intent
import android.content.res.ColorStateList
import android.net.VpnService
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.format.DateUtils
import android.text.format.Formatter
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlin.concurrent.thread

/** One button home. The access itself was made when this phone was paired. */
class VpnActivity : AppCompatActivity() {
    private lateinit var store: Store
    private val main = Handler(Looper.getMainLooper())
    private var busy = false
    private var problem: Vpn.Problem? = null

    private val askVpn = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == RESULT_OK) connect() else refused() // also what an always-on VPN elsewhere gives
    }
    private val tick = object : Runnable {
        override fun run() {
            show()
            main.postDelayed(this, 1000)
        }
    }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContentView(R.layout.activity_vpn)
        store = Store(this)
        findViewById<MaterialToolbar>(R.id.toolbar).setNavigationOnClickListener { finish() }
        findViewById<Button>(R.id.toggle).setOnClickListener { if (Vpn.running) disconnect() else connect() }
        findViewById<Button>(R.id.phone_settings).setOnClickListener { phoneSettings() }
    }

    override fun onResume() {
        super.onResume()
        main.post(tick)
        if (store.vpnConfig == null && store.token != null) thread { Vpn.sync(store) }
    }

    override fun onPause() {
        super.onPause()
        main.removeCallbacks(tick)
    }

    private fun connect() {
        problem = null
        VpnService.prepare(this)?.let { return askVpn.launch(it) }
        work { Vpn.up(this, store) }
    }

    private fun disconnect() = work { Vpn.down(this, store) }

    private fun work(job: () -> Unit) {
        busy = true
        show()
        thread {
            val failed = try {
                job()
                null
            } catch (e: Vpn.Failed) {
                e.problem
            }
            runOnUiThread {
                busy = false
                problem = failed
                show()
                if (failed == Vpn.Problem.REFUSED) refused()
            }
        }
    }

    private fun refused() {
        MaterialAlertDialogBuilder(this).setTitle(R.string.vpn_refused_title).setMessage(R.string.vpn_refused)
            .setPositiveButton(R.string.vpn_settings) { _, _ -> phoneSettings() }
            .setNegativeButton(R.string.cancel, null).show()
    }

    private fun phoneSettings() = startActivity(Intent(Settings.ACTION_VPN_SETTINGS))

    private fun show() {
        val status = Vpn.status()
        val now = System.currentTimeMillis()
        val fresh = status != null && now - status.handshake < HANDSHAKE_VALID
        val waiting = status != null && !fresh && now - Vpn.upSince < PATIENCE
        val (title, color) = when {
            busy || waiting -> R.string.vpn_connecting to R.color.warn
            fresh -> R.string.vpn_on to R.color.ok
            status != null -> R.string.vpn_no_answer to R.color.danger
            problem != null -> R.string.vpn_failed to R.color.danger
            else -> R.string.vpn_off to R.color.muted
        }
        findViewById<ImageView>(R.id.shield).imageTintList = ColorStateList.valueOf(getColor(color))
        findViewById<TextView>(R.id.title).setText(title)
        findViewById<TextView>(R.id.detail).setText(when {
            busy || waiting -> R.string.vpn_off_text
            fresh -> R.string.vpn_on_text
            status != null -> R.string.vpn_no_answer_text
            else -> when (problem) {
                Vpn.Problem.NOT_PAIRED -> R.string.vpn_not_paired
                Vpn.Problem.AWAY -> R.string.vpn_away
                Vpn.Problem.NOT_READY -> R.string.vpn_not_ready
                Vpn.Problem.NO_INTERNET -> R.string.vpn_no_internet
                Vpn.Problem.REFUSED -> R.string.vpn_refused_title
                Vpn.Problem.FAILED -> R.string.vpn_failed
                null -> R.string.vpn_off_text
            }
        })
        findViewById<Button>(R.id.toggle).apply {
            isEnabled = !busy
            setText(if (status != null) R.string.disconnect_vpn else R.string.connect_vpn)
        }
        val access = Vpn.access(store)
        row(R.id.device, store.vpnName ?: if (access == null) getString(R.string.vpn_waiting) else null)
        row(R.id.address, access?.address)
        row(R.id.server, access?.server)
        row(R.id.traffic, status?.let {
            "↓ ${Formatter.formatShortFileSize(this, it.received)}   ↑ ${Formatter.formatShortFileSize(this, it.sent)}"
        })
        row(R.id.last, status?.takeIf { it.handshake > 0 }?.let {
            DateUtils.getRelativeTimeSpanString(it.handshake, now, DateUtils.SECOND_IN_MILLIS).toString()
        })
        findViewById<View>(R.id.other).visibility = if (Vpn.otherActive(this)) View.VISIBLE else View.GONE
    }

    /** The value's row disappears with its value. */
    private fun row(id: Int, value: String?) {
        val text = findViewById<TextView>(id)
        text.text = value
        (text.parent as View).visibility = if (value == null) View.GONE else View.VISIBLE
    }

    private companion object {
        const val HANDSHAKE_VALID = 200_000L // WireGuard renews every two minutes while connected
        const val PATIENCE = 20_000L
    }
}
