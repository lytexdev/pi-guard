package de.lytex

import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import androidx.appcompat.app.AppCompatActivity

/**
 * Started by a shortcut on the app icon. Without a window of its own: it asks for the app lock
 * if one is set (SOS does not wait for it), runs the action and closes.
 */
class QuickActionActivity : AppCompatActivity() {
    private var waitingForLock = false
    private var started = false

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val store = Store(this)
        val id = intent.getStringExtra(QuickActions.EXTRA)
        if (store.token == null || id == null || QuickActions.all.none { it.id == id }) {
            startActivity(Intent(this, MainActivity::class.java))
            return finish()
        }
        val app = application as PiGuardApp
        val unlockedRecently = SystemClock.elapsedRealtime() - app.leftAt < MainActivity.LOCK_AFTER
        if (store.locked && id != "sos" && !unlockedRecently) {
            waitingForLock = true
            startActivity(Intent(this, LockActivity::class.java))
        }
    }

    override fun onResume() {
        super.onResume()
        if (started) return
        if (waitingForLock) {
            // Back from the lock screen: only an unlock refreshes this time.
            val app = application as PiGuardApp
            if (SystemClock.elapsedRealtime() - app.leftAt > UNLOCK_WINDOW) return finish()
        }
        started = true
        QuickActions.start(this, intent.getStringExtra(QuickActions.EXTRA)!!) { finish() }
    }

    private companion object {
        const val UNLOCK_WINDOW = 5_000L
    }
}
