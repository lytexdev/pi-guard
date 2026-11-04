package de.lytex

import android.app.Application

class PiGuardApp : Application() {
    /** When the app last went to the background; it locks again after a while. */
    var leftAt = 0L

    override fun onCreate() {
        super.onCreate()
        GuardService.channels(this)
    }
}
