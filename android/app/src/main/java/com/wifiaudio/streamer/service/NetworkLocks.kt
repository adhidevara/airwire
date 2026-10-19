package com.wifiaudio.streamer.service

import android.annotation.SuppressLint
import android.content.Context
import android.net.wifi.WifiManager
import android.os.PowerManager

/** Keeps Wi-Fi out of power save and the CPU awake for as long as audio is flowing. */
class NetworkLocks(context: Context, private val tag: String) {
    private val app = context.applicationContext
    private val wifiLocks = mutableListOf<WifiManager.WifiLock>()
    private var wakeLock: PowerManager.WakeLock? = null

    @SuppressLint("WakelockTimeout") // held exactly as long as the user keeps audio running
    fun acquire() {
        val wifi = app.getSystemService(WifiManager::class.java)!!
        // LOW_LATENCY disables Wi-Fi power save while the screen is on; HIGH_PERF covers the
        // locked-screen case.
        @Suppress("DEPRECATION")
        listOf(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, WifiManager.WIFI_MODE_FULL_HIGH_PERF)
            .forEach { mode ->
                wifi.createWifiLock(mode, "$tag:$mode").apply {
                    setReferenceCounted(false)
                    acquire()
                    wifiLocks += this
                }
            }
        wakeLock = app.getSystemService(PowerManager::class.java)!!
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, tag)
            .apply {
                setReferenceCounted(false)
                acquire()
            }
    }

    fun release() {
        wifiLocks.forEach { if (it.isHeld) it.release() }
        wifiLocks.clear()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }
}
