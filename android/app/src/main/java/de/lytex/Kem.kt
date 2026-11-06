package de.lytex

/** The post-quantum key exchange: the same Rust code as on the Pi and on computers. */
object Kem {
    private val available = runCatching { System.loadLibrary("pi_guard_kem") }.isSuccess

    private external fun exchange(start: ByteArray): ByteArray?

    /** Blocks for some seconds at most; call it inside the running tunnel. Null if there is no new key. */
    fun newKey(start: ByteArray): ByteArray? = if (available) runCatching { exchange(start) }.getOrNull() else null
}
