package de.lytex

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec

/** Settings on the phone. Secrets are encrypted with a key that never leaves the Android Keystore. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("piguard", Context.MODE_PRIVATE)

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as SecretKey?)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return generator.generateKey()
    }

    private fun putSecret(name: String, value: String?) {
        if (value == null) {
            prefs.edit().remove(name).apply()
            return
        }
        val cipher = Cipher.getInstance(CIPHER).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val data = cipher.iv + cipher.doFinal(value.toByteArray())
        prefs.edit().putString(name, Base64.encodeToString(data, Base64.NO_WRAP)).apply()
    }

    private fun secret(name: String): String? {
        val data = Base64.decode(prefs.getString(name, null) ?: return null, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(CIPHER)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, data, 0, IV_SIZE))
        return String(cipher.doFinal(data, IV_SIZE, data.size - IV_SIZE))
    }

    var url: String?
        get() = prefs.getString("url", null)
        set(value) = prefs.edit().putString("url", value).apply()
    var certificate: String?
        get() = prefs.getString("certificate", null)
        set(value) = prefs.edit().putString("certificate", value).apply()
    var token: String?
        get() = secret("token")
        set(value) = putSecret("token", value)
    var vpnConfig: String?
        get() = secret("vpn")
        set(value) = putSecret("vpn", value)
    var vpnName: String?
        get() = prefs.getString("vpn_name", null)
        set(value) = prefs.edit().putString("vpn_name", value).apply()
    /** On when the person connected, so a restart of the phone or the app comes back connected. */
    var vpnWanted: Boolean
        get() = prefs.getBoolean("vpn_wanted", false)
        set(value) = prefs.edit().putBoolean("vpn_wanted", value).apply()
    var useBiometric: Boolean
        get() = prefs.getBoolean("biometric", false)
        set(value) = prefs.edit().putBoolean("biometric", value).apply()
    var useCalendar: Boolean
        get() = prefs.getBoolean("calendar", false)
        set(value) = prefs.edit().putBoolean("calendar", value).apply()
    var shareLocation: Boolean
        get() = prefs.getBoolean("location", false)
        set(value) = prefs.edit().putBoolean("location", value).apply()

    val hasPin: Boolean get() = prefs.contains("pin")
    val locked: Boolean get() = hasPin || useBiometric

    fun setPin(pin: String?) {
        if (pin == null) {
            prefs.edit().remove("pin").remove("pin_salt").apply()
            return
        }
        val salt = ByteArray(16).also(SecureRandom()::nextBytes)
        prefs.edit()
            .putString("pin_salt", Base64.encodeToString(salt, Base64.NO_WRAP))
            .putString("pin", hash(pin, salt)).apply()
    }

    fun checkPin(pin: String): Boolean {
        val salt = Base64.decode(prefs.getString("pin_salt", null) ?: return false, Base64.NO_WRAP)
        return hash(pin, salt) == prefs.getString("pin", null)
    }

    private fun hash(pin: String, salt: ByteArray): String {
        val spec = PBEKeySpec(pin.toCharArray(), salt, 150_000, 256)
        val bytes = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
        return Base64.encodeToString(bytes, Base64.NO_WRAP)
    }

    /** Removed in the dashboard: nothing of the old login may stay on the phone. */
    fun forgetLogin() {
        token = null
        vpnConfig = null
        vpnWanted = false
    }

    fun clear() = prefs.edit().clear().apply()

    private companion object {
        const val ALIAS = "piguard"
        const val CIPHER = "AES/GCM/NoPadding"
        const val IV_SIZE = 12
    }
}
