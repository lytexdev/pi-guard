package de.lytex

import android.content.Context
import android.net.ConnectivityManager
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.Socket
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/** All connections to pi-guard. Its own certificate authority is pinned at the first start. */
object Net {
    private val json = "application/json".toMediaType()
    const val PORT = 8443

    fun parseCertificate(pem: String): X509Certificate =
        CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(pem.toByteArray())) as X509Certificate

    fun fingerprint(certificate: X509Certificate): String =
        MessageDigest.getInstance("SHA-256").digest(certificate.encoded).joinToString(":") { "%02X".format(it) }

    /** True if this certificate was issued by the pinned authority. */
    fun issuedBy(certificate: X509Certificate, pem: String): Boolean = try {
        certificate.verify(parseCertificate(pem).publicKey)
        certificate.checkValidity()
        true
    } catch (_: Exception) {
        false
    }

    private fun systemTrust(): X509TrustManager {
        val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        factory.init(null as KeyStore?)
        return factory.trustManagers.filterIsInstance<X509TrustManager>().first()
    }

    /** Trusts public certificates and the pinned authority. */
    private fun trustFor(pem: String?): X509TrustManager = object : X509TrustManager {
        private val system = systemTrust()
        override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
        override fun checkClientTrusted(chain: Array<X509Certificate>, type: String) = system.checkClientTrusted(chain, type)
        override fun checkServerTrusted(chain: Array<X509Certificate>, type: String) {
            if (pem != null && issuedBy(chain[0], pem)) return
            system.checkServerTrusted(chain, type)
        }
    }

    private fun builder(trust: X509TrustManager): OkHttpClient.Builder {
        val context = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
        return OkHttpClient.Builder().sslSocketFactory(context.socketFactory, trust)
            .connectTimeout(10, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
    }

    /** The pinned authority names the Pi, not each address it answers on. */
    fun client(store: Store): OkHttpClient {
        val pem = store.certificate
        val builder = builder(trustFor(pem))
        val standard = HttpsURLConnection.getDefaultHostnameVerifier()
        if (pem != null) builder.hostnameVerifier(HostnameVerifier { host, session ->
            val leaf = session.peerCertificates.firstOrNull() as? X509Certificate
            (leaf != null && issuedBy(leaf, pem)) || standard.verify(host, session)
        })
        return builder.pingInterval(30, TimeUnit.SECONDS).build()
    }

    /**
     * Only for reading the public certificate at the first start. Nothing secret goes over this
     * connection, and the person confirms the fingerprint before it is trusted.
     */
    private fun firstContact(): OkHttpClient {
        val anything = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<X509Certificate>, type: String) = Unit
            override fun checkServerTrusted(chain: Array<X509Certificate>, type: String) = Unit
        }
        return builder(anything).hostnameVerifier(HostnameVerifier { _, _ -> true })
            .connectTimeout(3, TimeUnit.SECONDS).readTimeout(5, TimeUnit.SECONDS).build()
    }

    fun isPiGuard(url: String): Boolean = try {
        firstContact().newCall(Request.Builder().url("$url/api/hello").build()).execute().use {
            it.isSuccessful && JSONObject(it.body!!.string()).optString("app") == "pi-guard"
        }
    } catch (_: Exception) {
        false
    }

    fun fetchAuthority(url: String): String? = try {
        firstContact().newCall(Request.Builder().url("$url/ca.crt").build()).execute().use {
            if (it.isSuccessful) it.body!!.string() else null
        }
    } catch (_: Exception) {
        null
    }

    /** Away from home the dashboard may answer elsewhere, for example over Tailscale. Blocks. */
    fun pick(store: Store) {
        val now = store.url ?: return
        if (isPiGuard(now)) return
        store.addresses.firstOrNull { it != now && isPiGuard(it) }?.let { store.url = it }
    }

    /** Looks for pi-guard in this phone's /24 network. */
    fun discover(context: Context): String? {
        val manager = context.getSystemService(ConnectivityManager::class.java)
        val links = manager.getLinkProperties(manager.activeNetwork) ?: return null
        val own = links.linkAddresses.map { it.address }.filterIsInstance<Inet4Address>().firstOrNull() ?: return null
        val prefix = own.hostAddress!!.substringBeforeLast('.')
        val pool = Executors.newFixedThreadPool(32)
        val answers = (1..254).map { host ->
            pool.submit<String?> {
                val address = "$prefix.$host"
                val open = try {
                    Socket().use { it.connect(InetSocketAddress(address, PORT), 400); true }
                } catch (_: Exception) {
                    false
                }
                if (open && isPiGuard("https://$address:$PORT")) "https://$address:$PORT" else null
            }
        }
        val found = answers.firstNotNullOfOrNull { it.get() }
        pool.shutdownNow()
        return found
    }

    fun request(store: Store, path: String): Request.Builder =
        Request.Builder().url("${store.url}$path").header("Authorization", "Bearer ${store.token}")

    fun post(store: Store, path: String, body: JSONObject): Response =
        client(store).newCall(request(store, path).post(body.toString().toRequestBody(json)).build()).execute()

    fun jsonBody(body: JSONObject) = body.toString().toRequestBody(json)
}
