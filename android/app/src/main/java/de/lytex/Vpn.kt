package de.lytex

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.util.Log
import com.wireguard.android.backend.BackendException
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import org.json.JSONObject
import java.io.IOException

/** The phone's own WireGuard connection home: this app is its VPN client. */
object Vpn {
    enum class Problem { NOT_PAIRED, AWAY, NOT_READY, REFUSED, NO_INTERNET, FAILED }
    class Failed(val problem: Problem) : Exception(problem.name)
    class Status(val received: Long, val sent: Long, val handshake: Long)
    class Access(val address: String, val server: String)

    var upSince = 0L
        private set
    private var backend: GoBackend? = null
    private val tunnel = object : Tunnel {
        override fun getName() = "piguard"
        override fun onStateChange(newState: Tunnel.State) = Unit
    }

    private fun backend(context: Context) = backend ?: GoBackend(context.applicationContext).also { backend = it }

    val running: Boolean get() = backend?.getState(tunnel) == Tunnel.State.UP

    /** Blocks; call off the main thread. Uses the stored access, so it also works away from home. */
    @Synchronized
    fun up(context: Context, store: Store) {
        val text = store.vpnConfig ?: fetch(store)
        try {
            backend(context).setState(tunnel, Tunnel.State.UP, Config.parse(text.byteInputStream()))
        } catch (e: BackendException) {
            Log.w(TAG, "tunnel refused: ${e.reason}")
            throw Failed(when (e.reason) {
                BackendException.Reason.VPN_NOT_AUTHORIZED -> Problem.REFUSED
                BackendException.Reason.DNS_RESOLUTION_FAILURE -> Problem.NO_INTERNET
                else -> Problem.FAILED
            })
        } catch (e: Exception) {
            Log.w(TAG, "tunnel failed: ${e.javaClass.simpleName}")
            throw Failed(Problem.FAILED)
        }
        upSince = System.currentTimeMillis()
        store.vpnWanted = true
    }

    @Synchronized
    fun down(context: Context, store: Store) {
        store.vpnWanted = false
        if (running) backend(context).setState(tunnel, Tunnel.State.DOWN, null)
    }

    /** After a restart of the phone or the app. Never asks anything, Android's consent must still stand. */
    fun resume(context: Context, store: Store) {
        if (store.vpnWanted && !running && VpnService.prepare(context) == null) runCatching { up(context, store) }
    }

    /** Whenever pi-guard is in reach: creates the access, or picks up one renewed in the dashboard. */
    fun sync(store: Store) {
        runCatching { fetch(store) }.onFailure { Log.i(TAG, "no access yet: ${it.message}") }
    }

    /** Asked again, pi-guard answers with the same access. */
    @Synchronized
    private fun fetch(store: Store): String {
        if (store.token == null) throw Failed(Problem.NOT_PAIRED) // would count as a failed login
        val answer = try {
            Net.post(store, "/api/mobile/vpn", JSONObject().put("full_tunnel", true)).use {
                if (it.code == 401) throw Failed(Problem.NOT_PAIRED)
                if (!it.isSuccessful) throw Failed(Problem.NOT_READY)
                JSONObject(it.body!!.string())
            }
        } catch (_: IOException) {
            throw Failed(Problem.AWAY)
        }
        store.vpnName = answer.optString("name").ifEmpty { null }
        return answer.getString("config").also { store.vpnConfig = it }
    }

    fun status(): Status? {
        val stats = backend?.takeIf { running }?.getStatistics(tunnel) ?: return null
        val peer = stats.peers().firstOrNull()?.let(stats::peer)
        return Status(stats.totalRx(), stats.totalTx(), peer?.latestHandshakeEpochMillis() ?: 0)
    }

    fun access(store: Store): Access? = runCatching {
        val config = Config.parse(store.vpnConfig!!.byteInputStream())
        Access(config.`interface`.addresses.first().address.hostAddress!!, config.peers.first().endpoint.get().host)
    }.getOrNull()

    /** Android runs one VPN at a time, so a second one decides what connecting here does. */
    fun otherActive(context: Context): Boolean {
        val manager = context.getSystemService(ConnectivityManager::class.java)
        return !running && manager.getNetworkCapabilities(manager.activeNetwork)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
    }

    private const val TAG = "PiGuardVpn"
}
