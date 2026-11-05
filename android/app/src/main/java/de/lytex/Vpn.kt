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
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.Inet6Address
import java.net.InetAddress
import kotlin.concurrent.thread

/** The phone's own WireGuard connection home: this app is its VPN client. */
object Vpn {
    enum class Problem { NOT_PAIRED, AWAY, NOT_READY, REFUSED, NO_INTERNET, FAILED }
    class Failed(val problem: Problem) : Exception(problem.name)
    class Status(val received: Long, val sent: Long, val handshake: Long)
    class Access(val address: String, val server: String)
    class Way(val kind: String, val endpoint: String, val mtu: Int)

    var upSince = 0L
        private set
    var way: Way? = null
        private set
    private var hunt = 0
    private var excluded: Set<String> = emptySet()
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
        val all = ways(store)
        val first = all.firstOrNull { it.endpoint == store.vpnEndpoint } ?: all.firstOrNull()
        excluded = store.vpnExcluded
        start(context, text, first)
        store.vpnWanted = true
        if (all.size > 1) watch(context.applicationContext, store, text, listOfNotNull(first) + all.filter { it !== first })
    }

    private fun start(context: Context, text: String, chosen: Way?) {
        try {
            val config = Config.parse(prepared(text, chosen).byteInputStream())
            backend(context).setState(tunnel, Tunnel.State.UP, config)
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
        way = chosen
        upSince = System.currentTimeMillis()
    }

    /** The access with one way filled in. WireGuard prefers IPv4, so IPv6 is named by its address. */
    private fun prepared(text: String, chosen: Way?): String = withExcluded(withWay(text, chosen))

    private fun withExcluded(text: String): String {
        if (excluded.isEmpty()) return text
        val lines = text.lines()
        val at = lines.indexOfFirst { it.startsWith("PrivateKey") } + 1
        return (lines.take(at) + "ExcludedApplications = ${excluded.joinToString(", ")}" + lines.drop(at)).joinToString("\n")
    }

    private fun withWay(text: String, chosen: Way?): String {
        chosen ?: return text
        var target = chosen.endpoint
        if (chosen.kind == "ipv6" && !target.startsWith("[")) {
            val host = target.substringBeforeLast(':')
            val address = InetAddress.getAllByName(host).firstOrNull { it is Inet6Address }?.hostAddress?.substringBefore('%')
                ?: throw BackendException(BackendException.Reason.DNS_RESOLUTION_FAILURE)
            target = "[$address]:${target.substringAfterLast(':')}"
        }
        val lines = text.lines().filterNot { it.startsWith("MTU") }.map { if (it.startsWith("Endpoint")) "Endpoint = $target" else it }
        if (chosen.mtu == 0) return lines.joinToString("\n")
        val at = lines.indexOfFirst { it.startsWith("PrivateKey") } + 1
        return (lines.take(at) + "MTU = ${chosen.mtu}" + lines.drop(at)).joinToString("\n")
    }

    /** Like a VPN app with several servers: moves on while a way does not answer, also later. */
    private fun watch(context: Context, store: Store, text: String, order: List<Way>) {
        val run = ++hunt
        thread(isDaemon = true) {
            var turn = 0
            while (run == hunt) {
                if (answered(run)) {
                    store.vpnEndpoint = way?.endpoint
                    while (run == hunt && fresh()) Thread.sleep(CHECK_EVERY)
                }
                if (run != hunt || !running) return@thread
                turn++
                synchronized(this) {
                    if (run == hunt) next(context, text, order[turn % order.size])
                }
            }
        }
    }

    /** A new tunnel, because the name of the next way can only be looked up outside the old one. */
    private fun next(context: Context, text: String, chosen: Way) {
        runCatching { backend(context).setState(tunnel, Tunnel.State.DOWN, null) }
        runCatching { start(context, text, chosen) }.onFailure { Log.i(TAG, "${chosen.kind} not usable: ${it.message}") }
    }

    private fun answered(run: Int): Boolean {
        val until = System.currentTimeMillis() + WAY_WAIT
        while (run == hunt && System.currentTimeMillis() < until) {
            if ((status()?.handshake ?: 0) > 0) return true
            Thread.sleep(POLL)
        }
        return false
    }

    private fun fresh(): Boolean {
        val handshake = status()?.handshake ?: return false
        return System.currentTimeMillis() - handshake < STALE
    }

    fun ways(store: Store): List<Way> = runCatching {
        val list = JSONArray(store.vpnWays ?: "[]")
        (0 until list.length()).map { list.getJSONObject(it) }.map { Way(it.getString("kind"), it.getString("endpoint"), it.optInt("mtu")) }
    }.getOrDefault(emptyList())

    @Synchronized
    fun down(context: Context, store: Store) {
        hunt++
        store.vpnWanted = false
        if (running) backend(context).setState(tunnel, Tunnel.State.DOWN, null)
    }

    /** After a restart of the phone or the app. Never asks anything, Android's consent must still stand. */
    fun resume(context: Context, store: Store) {
        if (store.vpnWanted && !running && VpnService.prepare(context) == null) runCatching { up(context, store) }
    }

    /** A new choice of apps takes effect by connecting again. */
    fun reconnect(context: Context, store: Store) {
        if (running) {
            down(context, store)
            up(context, store)
        }
    }

    /** When the network changes. Away from home means pi-guard cannot be reached directly. */
    fun autoConnect(context: Context, store: Store) {
        if (!store.vpnAuto || running || store.vpnConfig == null || VpnService.prepare(context) != null) return
        val home = store.url ?: return
        if (!Net.isPiGuard(home)) runCatching { up(context, store) }
    }

    /** Whenever pi-guard is in reach: creates the access, or picks up one renewed in the dashboard. */
    fun sync(store: Store) {
        runCatching { fetch(store) }.onFailure { Log.i(TAG, "no access yet: ${it.message}") }
        runCatching { learnAddresses(store) }
    }

    private fun learnAddresses(store: Store) {
        val known = Net.client(store).newCall(Net.request(store, "/api/mobile/reach").build()).execute().use {
            if (!it.isSuccessful) return
            JSONObject(it.body!!.string()).getJSONArray("addresses")
        }
        val named = (0 until known.length()).map(known::getString)
        store.addresses = (listOfNotNull(store.url) + store.addresses + named).distinct()
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
        store.vpnWays = answer.optJSONArray("ways")?.toString()
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
    private const val WAY_WAIT = 12_000L
    private const val POLL = 500L
    private const val CHECK_EVERY = 20_000L
    private const val STALE = 180_000L
}
