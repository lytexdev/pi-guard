package de.lytex

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.view.View
import android.webkit.CookieManager
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import kotlin.concurrent.thread

/** The dashboard, with the phone's own functions around it. */
class MainActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var web: WebView
    private lateinit var offline: LinearLayout
    private var pairing = false
    private var pairFailed = false
    private var chosen: ValueCallback<Array<Uri>>? = null
    private var asked: Pair<PermissionRequest, Array<String>>? = null

    private val pickFile = registerForActivityResult(ActivityResultContracts.GetMultipleContents()) {
        chosen?.onReceiveValue(it.toTypedArray())
        chosen = null
    }
    private val askCamera = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        asked?.let { (request, wanted) -> if (granted) request.grant(wanted) else request.deny() }
        asked = null
    }
    private val askPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {}

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        store = Store(this)
        if (store.url == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            return finish()
        }
        setContentView(R.layout.activity_main)
        findViewById<TextView>(R.id.app_title).setText(Disguise.current(this).name)
        if (!Disguise.isDefault(this)) findViewById<View>(R.id.app_logo).visibility = View.GONE
        web = findViewById(R.id.web)
        offline = findViewById(R.id.offline)
        findViewById<Button>(R.id.sos).setOnClickListener { startActivity(Intent(this, EmergencyActivity::class.java)) }
        findViewById<ImageButton>(R.id.settings).setOnClickListener { startActivity(Intent(this, AppSettingsActivity::class.java)) }
        findViewById<Button>(R.id.retry).setOnClickListener { open() }
        findViewById<Button>(R.id.vpn).setOnClickListener { openVpn() }
        findViewById<ImageButton>(R.id.vpn_open).setOnClickListener { openVpn() }
        prepareWeb()
        val wanted = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) wanted += Manifest.permission.POST_NOTIFICATIONS
        askPermissions.launch(wanted.toTypedArray())
        open()
        GuardService.start(this)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun prepareWeb() {
        web.setBackgroundColor(getColor(R.color.bg)) // no white flash before the dashboard paints
        web.overScrollMode = View.OVER_SCROLL_NEVER
        web.isHorizontalScrollBarEnabled = false
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.mediaPlaybackRequiresUserGesture = false
        CookieManager.getInstance().setAcceptCookie(true)
        web.webViewClient = object : WebViewClient() {
            @SuppressLint("WebViewClientOnReceivedSslError")
            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                // The pinned authority instead of the system's list; anything else stays refused.
                val leaf = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) error.certificate.x509Certificate else null
                val pem = store.certificate
                if (leaf != null && pem != null && Net.issuedBy(leaf, pem)) handler.proceed() else handler.cancel()
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) offline.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView, url: String) {
                if (store.token == null) pairIfLoggedIn()
            }

            override fun doUpdateVisitedHistory(view: WebView, url: String, isReload: Boolean) {
                if (store.token == null) pairIfLoggedIn()
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // Compared by part: "https://pi:8443@evil.example" starts with the same letters.
                val home = Uri.parse(store.url!!)
                if (request.url.scheme == home.scheme && request.url.authority == home.authority) return false
                startActivity(Intent(Intent.ACTION_VIEW, request.url))
                return true
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                val usable = setOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE, PermissionRequest.RESOURCE_VIDEO_CAPTURE)
                val wanted = request.resources.filter { it in usable }.toTypedArray()
                if (request.origin.toString().trimEnd('/') != store.url || wanted.isEmpty()) return request.deny()
                val needsCamera = PermissionRequest.RESOURCE_VIDEO_CAPTURE in wanted && checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED
                if (!needsCamera) return request.grant(wanted)
                asked = request to wanted // only when the dashboard wants a picture, not at the first start
                askCamera.launch(Manifest.permission.CAMERA)
            }

            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                chosen?.onReceiveValue(null)
                chosen = callback
                pickFile.launch("*/*")
                return true
            }
        }
    }

    /** With a stored login the dashboard opens directly; otherwise its own login page shows. */
    private fun open() {
        offline.visibility = View.GONE
        if (store.token == null) return web.loadUrl(store.url!!)
        thread {
            runCatching { Net.pick(store) }
            val cookie = runCatching {
                val request = Net.request(store, "/api/mobile/session").post(ByteArray(0).toRequestBody()).build()
                Net.client(store).newCall(request).execute().use {
                    if (it.code == 401) { // removed in the dashboard
                        runCatching { Vpn.down(this, store) }
                        store.forgetLogin()
                        runOnUiThread { CookieManager.getInstance().removeAllCookies(null) }
                    }
                    it.header("Set-Cookie").takeIf { _ -> it.isSuccessful }
                }
            }.getOrNull()
            runOnUiThread {
                if (cookie != null) CookieManager.getInstance().setCookie(store.url, cookie)
                web.loadUrl(store.url!!)
            }
            if (cookie != null) Vpn.sync(store) // the access is ready before the phone leaves home
        }
    }

    /** After the login in the dashboard: this phone gets its own token. */
    private fun pairIfLoggedIn() {
        if (pairing || pairFailed) return
        val cookies = CookieManager.getInstance().getCookie(store.url) ?: return
        pairing = true
        thread {
            val paired = runCatching {
                val client = Net.client(store)
                val session = client.newCall(Request.Builder().url("${store.url}/api/session").header("Cookie", cookies).build()).execute().use { JSONObject(it.body!!.string()) }
                if (!session.optBoolean("authenticated")) return@thread
                val body = JSONObject().put("name", "${Build.MANUFACTURER} ${Build.MODEL}").put("platform", "android")
                val request = Request.Builder().url("${store.url}/api/mobile/pair").header("Cookie", cookies)
                    .header("X-CSRF-Token", session.getString("csrf")).post(Net.jsonBody(body)).build()
                client.newCall(request).execute().use {
                    check(it.isSuccessful)
                    store.token = JSONObject(it.body!!.string()).getString("token")
                }
                Vpn.sync(store)
            }.isSuccess
            pairing = false
            runOnUiThread {
                pairFailed = !paired
                if (paired) GuardService.start(this) else Toast.makeText(this, R.string.pair_failed, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun openVpn() = startActivity(Intent(this, VpnActivity::class.java))

    override fun onResume() {
        super.onResume()
        findViewById<ImageButton>(R.id.vpn_open).imageTintList = ColorStateList.valueOf(getColor(if (Vpn.running) R.color.ok else R.color.text))
        if (Vpn.running && offline.visibility == View.VISIBLE) open()
        val app = application as PiGuardApp
        if (store.locked && SystemClock.elapsedRealtime() - app.leftAt > LOCK_AFTER) {
            startActivity(Intent(this, LockActivity::class.java))
        }
    }

    override fun onPause() {
        super.onPause()
        (application as PiGuardApp).leftAt = SystemClock.elapsedRealtime()
    }

    @Deprecated("Back goes through the dashboard's pages first")
    override fun onBackPressed() {
        if (::web.isInitialized && web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    private companion object {
        const val LOCK_AFTER = 30_000L
    }
}
