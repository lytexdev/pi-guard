package de.lytex

import android.content.Intent
import android.os.Bundle
import android.os.CountDownTimer
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.View
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
        val store = Store(this)
        thread {
            val reached = runCatching {
                val report: JSONObject = Locator.report(this, true)
                Net.post(store, "/api/mobile/emergency", report).use {
                    check(it.isSuccessful)
                    JSONObject(it.body!!.string()).getInt("contacts")
                }
            }
            val result = reached.fold({ if (it > 0) getString(R.string.emergency_sent, it) else getString(R.string.emergency_nobody) }, { getString(R.string.emergency_failed) })
            if (reached.isSuccess) {
                // The place follows every minute for a while, from the service.
                startService(Intent(this, GuardService::class.java).setAction(GuardService.ACTION_TRACK))
                if (store.sosMedia) capture(store, result)
            }
            runOnUiThread {
                text.text = if (reached.isSuccess) "$result\n\n${getString(R.string.sos_tracking)}" else result
                button.isEnabled = true
                button.setOnClickListener { finish() }
                button.setText(android.R.string.ok)
                if (reached.isSuccess) findViewById<Button>(R.id.safe).apply {
                    visibility = View.VISIBLE
                    setOnClickListener { startService(Intent(this@EmergencyActivity, GuardService::class.java).setAction(GuardService.ACTION_SAFE)); finish() }
                }
            }
        }
    }

    /** Runs on the sending thread while this screen stays open: Android allows camera and
     *  microphone only for an app in front. */
    private fun capture(store: Store, result: String) {
        runOnUiThread { text.text = "$result\n\n${getString(R.string.sos_taking, SosCapture.CLIP_SECONDS)}" }
        runCatching { SosCapture.photos(this, store) }
        val left = SosCapture.CLIP_SECONDS
        val ticker = object : Runnable {
            var seconds = left
            override fun run() {
                if (seconds <= 0 || isFinishing) return
                text.text = "$result\n\n${getString(R.string.sos_taking, seconds--)}"
                text.postDelayed(this, 1000)
            }
        }
        runOnUiThread { ticker.run() }
        runCatching { SosCapture.clip(this, store) }
    }

    override fun onDestroy() {
        timer?.cancel()
        super.onDestroy()
    }

    private companion object {
        const val SECONDS = 10L
    }
}
