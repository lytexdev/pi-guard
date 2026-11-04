package de.lytex

import android.content.Context
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import org.json.JSONObject

/** The phone's own WireGuard connection home: this app is its VPN client. */
object Vpn {
    private var backend: GoBackend? = null
    private val tunnel = object : Tunnel {
        override fun getName() = "piguard"
        override fun onStateChange(newState: Tunnel.State) = Unit
    }

    private fun backend(context: Context) = backend ?: GoBackend(context.applicationContext).also { backend = it }

    val running: Boolean get() = backend?.getState(tunnel) == Tunnel.State.UP

    /** Blocks; call off the main thread. Asks pi-guard for an access the first time. */
    fun up(context: Context, store: Store) {
        val config = store.vpnConfig ?: fetch(store).also { store.vpnConfig = it }
        backend(context).setState(tunnel, Tunnel.State.UP, Config.parse(config.byteInputStream()))
    }

    fun down(context: Context) {
        backend(context).setState(tunnel, Tunnel.State.DOWN, null)
    }

    private fun fetch(store: Store): String =
        Net.post(store, "/api/mobile/vpn", JSONObject().put("full_tunnel", true)).use {
            check(it.isSuccessful) { "vpn ${it.code}" }
            JSONObject(it.body!!.string()).getString("config")
        }
}
