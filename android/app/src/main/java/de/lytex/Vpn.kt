package de.lytex

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.util.Base64
import android.util.Log
import org.amnezia.awg.backend.BackendException
import org.amnezia.awg.backend.GoBackend
import org.amnezia.awg.backend.Tunnel
import org.amnezia.awg.config.Config
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
    /** One way home. [disguise] makes it AmneziaWG: the same tunnel, its packets looking different. */
    class Way(val kind: String, val endpoint: String, val mtu: Int, val disguise: Map<String, Long> = emptyMap()) {
        val hidden: Boolean get() = disguise.isNotEmpty()
    }

    var upSince = 0L
        private set
    var way: Way? = null
        private set
    private var hunt = 0
    /** From the last key exchange. In memory only: without it, the starting key still opens the tunnel. */
    private var pqKey: String? = null
    private var pqSeen = 0L
    private var excluded: Set<String> = emptySet()
    private var backend: GoBackend? = null
    private val tunnel = object : Tunnel {
        override fun getName() = "piguard"
        override fun onStateChange(newState: Tunnel.State) = Unit
    }

    private fun backend(context: Context) = backend ?: GoBackend(context.applicationContext).also { backend = it }

    val running: Boolean get() = backend?.getState(tunnel) == Tunnel.State.UP
    val quantum: Boolean get() = running && pqKey != null

    /** Blocks; call off the main thread. Uses the stored access, so it also works away from home. */
    @Synchronized
    fun up(context: Context, store: Store) {
        val text = store.vpnConfig ?: fetch(store)
        val all = ways(store)
        val first = all.firstOrNull { it.endpoint == store.vpnEndpoint } ?: all.firstOrNull()
        excluded = store.vpnExcluded
        if (System.currentTimeMillis() - pqSeen > PQ_KEY_VALID) pqKey = null
        start(context, text, first)
        store.vpnWanted = true
        watch(context.applicationContext, store, text, listOfNotNull(first) + all.filter { it !== first })
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
    private fun prepared(text: String, chosen: Way?): String = withDisguise(withExcluded(withWay(withKey(text), chosen)), chosen)

    /** Jc, S1, H1 and the rest go into [Interface]; without them it is plain WireGuard. */
    private fun withDisguise(text: String, chosen: Way?): String {
        if (chosen == null || !chosen.hidden) return text
        val lines = text.lines()
        val at = lines.indexOfFirst { it.startsWith("PrivateKey") } + 1
        val extra = chosen.disguise.filterKeys { it in DISGUISE }.map { (key, value) -> "$key = $value" }
        return (lines.take(at) + extra + lines.drop(at)).joinToString("\n")
    }

    private fun withKey(text: String): String {
        val key = pqKey ?: return text
        return text.lines().joinToString("\n") { if (it.startsWith("PresharedKey")) "PresharedKey = $key" else it }
    }

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
        val ring: List<Way?> = order.ifEmpty { listOf(null) }
        thread(isDaemon = true) {
            var turn = 0
            while (run == hunt) {
                if (answered(run)) {
                    store.vpnEndpoint = way?.endpoint
                    if (pqKey == null && quantum(context, text, run)) continue // a new tunnel: wait for its answer
                    while (run == hunt && fresh()) {
                        pqSeen = System.currentTimeMillis()
                        Thread.sleep(CHECK_EVERY)
                    }
                }
                if (run != hunt || !running) return@thread
                turn++
                // The Pi returns to the starting key when a device goes silent, for example asleep.
                val fallBack = pqKey != null
                pqKey = null
                if (ring.size > 1 || fallBack) synchronized(this) {
                    if (run == hunt) next(context, text, ring[turn % ring.size])
                }
            }
        }
    }

    /** Fetches a post-quantum key through the tunnel, then connects with it. Android cannot swap it in a running tunnel. */
    private fun quantum(context: Context, text: String, run: Int): Boolean {
        val start = text.lines().firstOrNull { it.startsWith("PresharedKey") }?.substringAfter('=')?.trim() ?: return false
        val raw = runCatching { Base64.decode(start, Base64.NO_WRAP) }.getOrNull() ?: return false
        val key = Kem.newKey(raw) ?: return false
        synchronized(this) {
            if (run != hunt || !running) return false
            pqKey = Base64.encodeToString(key, Base64.NO_WRAP)
            pqSeen = System.currentTimeMillis()
            next(context, text, way)
        }
        return true
    }

    /** A new tunnel, because the name of the next way can only be looked up outside the old one. */
    private fun next(context: Context, text: String, chosen: Way?) {
        runCatching { backend(context).setState(tunnel, Tunnel.State.DOWN, null) }
        runCatching { start(context, text, chosen) }.onFailure { Log.i(TAG, "${chosen?.kind} not usable: ${it.message}") }
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

    /** The normal ways first; the disguised ones only after them, as they cost a little more. */
    fun ways(store: Store): List<Way> = parse(store.vpnWays) + parse(store.vpnHidden)

    private fun parse(json: String?): List<Way> = runCatching {
        val list = JSONArray(json ?: "[]")
        (0 until list.length()).map { list.getJSONObject(it) }.map { item ->
            val disguise = item.optJSONObject("awg")?.let { awg -> awg.keys().asSequence().associateWith { awg.getLong(it) } }.orEmpty()
            Way(item.getString("kind"), item.getString("endpoint"), item.optInt("mtu"), disguise)
        }
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
        store.vpnHidden = answer.optJSONArray("obfuscated")?.toString() // AmneziaWG, when the Pi has it
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
    private val DISGUISE = setOf("Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4")
    private const val WAY_WAIT = 12_000L
    private const val POLL = 500L
    private const val CHECK_EVERY = 20_000L
    private const val STALE = 180_000L
    private const val PQ_KEY_VALID = 190_000L // the Pi returns to the starting key a little later
}
