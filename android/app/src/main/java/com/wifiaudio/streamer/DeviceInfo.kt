package com.wifiaudio.streamer

import android.content.Context
import android.os.Build
import android.provider.Settings

/** The name the user gave this phone, e.g. "Redmi Note 11", shown on the PC. */
fun Context.deviceName(): String =
    Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME)
        ?.takeIf { it.isNotBlank() } ?: Build.MODEL
