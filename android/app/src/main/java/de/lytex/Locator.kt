package de.lytex

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.CancellationSignal
import org.json.JSONObject
import java.time.Instant
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** What this phone knows about itself, for finding it and for emergencies. */
object Locator {
    private fun allowed(context: Context) =
        context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    private fun locate(context: Context): Location? {
        if (!allowed(context)) return null
        val manager = context.getSystemService(LocationManager::class.java)
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).filter(manager::isProviderEnabled)
        var fix: Location? = null
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && providers.isNotEmpty()) {
            val done = CountDownLatch(1)
            val pool = Executors.newSingleThreadExecutor()
            manager.getCurrentLocation(providers.first(), CancellationSignal(), pool) { fix = it; done.countDown() }
            done.await(20, TimeUnit.SECONDS)
            pool.shutdown()
        }
        return fix ?: providers.mapNotNull(manager::getLastKnownLocation).maxByOrNull { it.time }
    }

    @Suppress("DEPRECATION")
    fun report(context: Context, withLocation: Boolean): JSONObject {
        val report = JSONObject()
        report.put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
        report.put("os", "Android ${Build.VERSION.RELEASE}")
        report.put("app", context.packageManager.getPackageInfo(context.packageName, 0).versionName)
        context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))?.let {
            val level = it.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val scale = it.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
            if (level >= 0) report.put("battery", level * 100 / scale)
            report.put("charging", it.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0)
        }
        val connectivity = context.getSystemService(ConnectivityManager::class.java)
        connectivity.getNetworkCapabilities(connectivity.activeNetwork)?.let {
            report.put("vpn", it.hasTransport(NetworkCapabilities.TRANSPORT_VPN))
            report.put("network", when {
                it.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "WLAN"
                it.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "Mobile"
                else -> "Other"
            })
        }
        if (allowed(context)) {
            val ssid = context.applicationContext.getSystemService(WifiManager::class.java).connectionInfo?.ssid
            if (ssid != null && ssid != "<unknown ssid>") report.put("ssid", ssid.trim('"'))
        }
        if (withLocation) locate(context)?.let {
            report.put("latitude", it.latitude).put("longitude", it.longitude).put("accuracy", it.accuracy.toDouble())
            if (it.hasAltitude()) report.put("altitude", it.altitude)
            if (it.hasSpeed()) report.put("speed", it.speed.toDouble())
            report.put("located_at", Instant.ofEpochMilli(it.time).toString())
        }
        return report
    }
}
