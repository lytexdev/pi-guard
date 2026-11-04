package de.lytex

import android.os.Bundle
import android.os.SystemClock
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat

/** PIN and biometrics in front of the app. */
class LockActivity : AppCompatActivity() {
    private lateinit var store: Store
    private var wrong = 0

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContentView(R.layout.activity_lock)
        store = Store(this)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = moveTaskToBack(true).let { }
        })
        val pin = findViewById<EditText>(R.id.pin)
        val open = findViewById<Button>(R.id.open)
        open.setOnClickListener {
            if (store.checkPin(pin.text.toString())) {
                unlocked()
            } else {
                pin.text.clear()
                Toast.makeText(this, R.string.lock_wrong, Toast.LENGTH_SHORT).show()
                if (++wrong >= TRIES) { // guessing gets slower
                    open.isEnabled = false
                    open.postDelayed({ open.isEnabled = true }, wrong * WAIT)
                }
            }
        }
        val biometric = findViewById<Button>(R.id.biometric)
        val strong = BiometricManager.from(this).canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS
        if (store.useBiometric && strong) {
            biometric.setOnClickListener { prompt() }
            prompt()
        } else {
            biometric.visibility = android.view.View.GONE
        }
        if (!store.hasPin) {
            pin.visibility = android.view.View.GONE
            open.visibility = android.view.View.GONE
            if (!strong) unlocked() // biometrics were removed from the phone: nothing to ask for
        }
    }

    private fun unlocked() {
        (application as PiGuardApp).leftAt = SystemClock.elapsedRealtime()
        finish()
    }

    private fun prompt() {
        val info = BiometricPrompt.PromptInfo.Builder().setTitle(getString(R.string.lock_prompt))
            .setNegativeButtonText(getString(R.string.cancel)).build()
        BiometricPrompt(this, ContextCompat.getMainExecutor(this), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = unlocked()
        }).authenticate(info)
    }

    private companion object {
        const val TRIES = 3
        const val WAIT = 5_000L
    }
}
