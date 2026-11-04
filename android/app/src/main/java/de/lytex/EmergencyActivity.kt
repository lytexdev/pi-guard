package de.lytex

import android.os.Bundle
import android.os.CountDownTimer
import android.os.VibrationEffect
import android.os.Vibrator
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import kotlin.concurrent.thread

/** One tap on SOS starts this; a second tap within ten seconds cancels it. */
class EmergencyActivity : AppCompatActivity() {
    private lateinit var text: TextView
    private lateinit var button: Button
    private var timer: CountDownTimer? = null

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContentView(R.layout.activity_emergency)
        text = findViewById(R.id.count)
        button = findViewById(R.id.cancel)
        button.setOnClickListener { timer?.cancel(); finish() }
        timer = object : CountDownTimer(SECONDS * 1000, 1000) {
            override fun onTick(left: Long) {
                text.text = getString(R.string.emergency_count, ((left + 999) / 1000).toInt())
                getSystemService(Vibrator::class.java).vibrate(VibrationEffect.createOneShot(120, VibrationEffect.DEFAULT_AMPLITUDE))
            }

            override fun onFinish() = send()
        }.start()
    }

    private fun send() {
        text.setText(R.string.emergency_sending)
        button.isEnabled = false
        thread {
            val reached = runCatching {
                val store = Store(this)
                val report: JSONObject = Locator.report(this, true)
                Net.post(store, "/api/mobile/emergency", report).use {
                    check(it.isSuccessful)
                    JSONObject(it.body!!.string()).getInt("contacts")
                }
            }
            runOnUiThread {
                text.text = reached.fold({ if (it > 0) getString(R.string.emergency_sent, it) else getString(R.string.emergency_nobody) }, { getString(R.string.emergency_failed) })
                button.isEnabled = true
                button.setOnClickListener { finish() }
                button.setText(android.R.string.ok)
            }
        }
    }

    override fun onDestroy() {
        timer?.cancel()
        super.onDestroy()
    }

    private companion object {
        const val SECONDS = 10L
    }
}
