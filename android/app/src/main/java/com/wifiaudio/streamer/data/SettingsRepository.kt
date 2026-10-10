package com.wifiaudio.streamer.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.wifiaudio.streamer.AudioSourceType
import com.wifiaudio.streamer.net.Protocol
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map
import java.io.IOException

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "stream_settings")

/** Token from pairing with the PC's file service (see files.FileTransfers). */
data class FilePairing(val token: String, val pcName: String)

data class StreamSettings(
    val host: String = "",
    val port: Int = Protocol.DEFAULT_PORT,
    val source: AudioSourceType = AudioSourceType.INTERNAL,
    val bitrateKbps: Int = 128,
)

class SettingsRepository(context: Context) {
    private val store = context.applicationContext.dataStore

    val settings: Flow<StreamSettings> = store.data
        .catch { e -> if (e is IOException) emit(emptyPreferences()) else throw e }
        .map { p ->
            StreamSettings(
                host = p[HOST].orEmpty(),
                port = p[PORT] ?: Protocol.DEFAULT_PORT,
                source = p[SOURCE]?.let { runCatching { AudioSourceType.valueOf(it) }.getOrNull() }
                    ?: AudioSourceType.INTERNAL,
                bitrateKbps = p[BITRATE] ?: 128,
            )
        }

    val pairing: Flow<FilePairing?> = store.data
        .catch { e -> if (e is IOException) emit(emptyPreferences()) else throw e }
        .map { p ->
            p[FILES_TOKEN]?.takeIf { it.isNotBlank() }?.let { FilePairing(it, p[FILES_PC_NAME].orEmpty()) }
        }

    suspend fun savePairing(pairing: FilePairing) {
        store.edit { p ->
            p[FILES_TOKEN] = pairing.token
            p[FILES_PC_NAME] = pairing.pcName
        }
    }

    suspend fun clearPairing() {
        store.edit { p ->
            p.remove(FILES_TOKEN)
            p.remove(FILES_PC_NAME)
        }
    }

    suspend fun save(s: StreamSettings) {
        store.edit { p ->
            p[HOST] = s.host
            p[PORT] = s.port
            p[SOURCE] = s.source.name
            p[BITRATE] = s.bitrateKbps
        }
    }

    private companion object {
        val HOST = stringPreferencesKey("host")
        val PORT = intPreferencesKey("port")
        val SOURCE = stringPreferencesKey("source")
        val BITRATE = intPreferencesKey("bitrate_kbps")
        val FILES_TOKEN = stringPreferencesKey("files_token")
        val FILES_PC_NAME = stringPreferencesKey("files_pc_name")
    }
}
