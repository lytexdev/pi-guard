package de.lytex

import android.app.Application
import org.amnezia.awg.backend.GoBackend
import kotlin.concurrent.thread

class PiGuardApp : Application() {
    /** When the app last went to the background; it locks again after a while. */
    var leftAt = 0L

    override fun onCreate() {
        super.onCreate()
        Crashes.install(this)
        GuardService.channels(this)
        // Android's "Always-on VPN" starts the tunnel service by itself, without the app being opened.
        GoBackend.setAlwaysOnCallback { thread { runCatching { Vpn.up(this, Store(this)) } } }
    }
}
