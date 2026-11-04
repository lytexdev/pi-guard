package de.lytex

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlin.concurrent.thread

/** First start: find pi-guard in the home network, or type its address. */
class SetupActivity : AppCompatActivity() {
    private lateinit var status: TextView
    private lateinit var address: EditText

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContentView(R.layout.activity_setup)
        status = findViewById(R.id.status)
        address = findViewById(R.id.address)
        findViewById<Button>(R.id.connect).setOnClickListener { connect(address.text.toString().trim().trimEnd('/')) }
        findViewById<Button>(R.id.search).setOnClickListener { search() }
        address.setOnEditorActionListener { _, _, _ ->
            connect(address.text.toString().trim().trimEnd('/'))
            true
        }
        search()
    }

    private fun search() {
        status.setText(R.string.setup_searching)
        thread {
            val found = Net.discover(this)
            runOnUiThread {
                if (found != null) {
                    if (address.text.isBlank()) address.setText(found) // never over what was typed
                    status.text = getString(R.string.setup_found, found)
                } else {
                    status.setText(R.string.setup_none)
                }
            }
        }
    }

    private fun connect(typed: String) {
        val url = if (typed.startsWith("https://")) typed else "https://$typed"
        thread {
            val authority = if (Net.isPiGuard(url)) Net.fetchAuthority(url) else null
            runOnUiThread { if (authority != null) confirm(url, authority) else status.setText(R.string.setup_failed) }
        }
    }

    private fun confirm(url: String, authority: String) {
        val fingerprint = Net.fingerprint(Net.parseCertificate(authority))
        MaterialAlertDialogBuilder(this).setTitle(R.string.trust_title).setMessage(getString(R.string.trust_text, fingerprint))
            .setPositiveButton(R.string.trust_yes) { _, _ ->
                val store = Store(this)
                store.url = url
                store.certificate = authority
                startActivity(Intent(this, MainActivity::class.java))
                finish()
            }
            .setNegativeButton(R.string.cancel, null).show()
    }
}
