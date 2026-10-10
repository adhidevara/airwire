package com.wifiaudio.streamer.files

import org.json.JSONObject
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.MessageDigest

class HttpStatusException(val code: Int, message: String) : IOException(message)

data class PairResult(val token: String, val pcName: String)

data class OutboxItem(val id: String, val name: String, val size: Long, val sha256: String)

data class Outbox(val version: Long, val files: List<OutboxItem>)

/**
 * Blocking client for the receiver's file API (PROTOCOL.md, "File transfer"). Call it from
 * a background dispatcher.
 */
class FileTransferClient(
    private val host: String,
    private val port: Int,
    private val token: String?,
) {
    fun pair(code: String, device: String): PairResult {
        val body = JSONObject().put("code", code).put("device", device).toString().toByteArray()
        val conn = open("/pair", "POST")
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.setFixedLengthStreamingMode(body.size)
        conn.outputStream.use { it.write(body) }
        val json = readJson(conn)
        return PairResult(json.getString("token"), json.optString("name").ifBlank { host })
    }

    /** Streams [input] to the PC; returns the name the PC saved it under. */
    fun upload(name: String, size: Long, input: InputStream, onProgress: (Long) -> Unit): String {
        val conn = open("/files/" + encode(name), "PUT", readTimeoutMs = 120_000)
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/octet-stream")
        conn.setFixedLengthStreamingMode(size)
        val digest = MessageDigest.getInstance("SHA-256")
        conn.outputStream.use { out -> copy(input, out, digest, onProgress) }
        val json = readJson(conn)
        val remote = json.optString("sha256")
        if (remote.isNotEmpty() && remote != digest.hex()) throw IOException("Checksum mismatch")
        return json.optString("name").ifBlank { name }
    }

    /** Long-polls the PC's outbox; returns as soon as it differs from [since]. */
    fun outbox(since: Long, waitSeconds: Int): Outbox {
        val conn = open("/outbox?since=$since&wait=$waitSeconds", "GET",
            readTimeoutMs = (waitSeconds + 15) * 1000)
        val json = readJson(conn)
        val files = json.getJSONArray("files")
        return Outbox(
            version = json.getLong("version"),
            files = List(files.length()) { i ->
                val f = files.getJSONObject(i)
                OutboxItem(f.getString("id"), f.getString("name"), f.getLong("size"), f.optString("sha256"))
            },
        )
    }

    fun download(id: String, output: OutputStream, onProgress: (Long) -> Unit) {
        val conn = open("/outbox/$id", "GET", readTimeoutMs = 120_000)
        if (conn.responseCode != HttpURLConnection.HTTP_OK) readJson(conn) // throws with the reason
        val expected = conn.getHeaderField("X-Airwire-Sha256").orEmpty()
        val digest = MessageDigest.getInstance("SHA-256")
        conn.inputStream.use { input -> copy(input, output, digest, onProgress) }
        if (expected.isNotEmpty() && expected != digest.hex()) throw IOException("Checksum mismatch")
    }

    /** Tells the PC the file is saved, so it leaves the outbox. */
    fun acknowledge(id: String) {
        val conn = open("/outbox/$id", "DELETE")
        val code = conn.responseCode
        conn.disconnect()
        if (code != HttpURLConnection.HTTP_NO_CONTENT && code != HttpURLConnection.HTTP_NOT_FOUND) {
            throw HttpStatusException(code, "Acknowledge failed ($code)")
        }
    }

    private fun open(path: String, method: String, readTimeoutMs: Int = 30_000): HttpURLConnection =
        (URL("http", host, port, "/api/v1$path").openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 5_000
            readTimeout = readTimeoutMs
            useCaches = false
            token?.let { setRequestProperty("Authorization", "Bearer $it") }
        }

    private fun readJson(conn: HttpURLConnection): JSONObject {
        try {
            val code = conn.responseCode
            if (code in 200..299) {
                val text = conn.inputStream.use { it.readBytes().decodeToString() }
                return if (text.isBlank()) JSONObject() else JSONObject(text)
            }
            val error = conn.errorStream?.use { it.readBytes().decodeToString() }.orEmpty()
            val message = runCatching { JSONObject(error).optString("error") }.getOrNull()
            throw HttpStatusException(code, message?.ifBlank { null } ?: "HTTP $code")
        } finally {
            conn.disconnect()
        }
    }

    private fun copy(input: InputStream, output: OutputStream, digest: MessageDigest, onProgress: (Long) -> Unit) {
        val buf = ByteArray(64 * 1024)
        var total = 0L
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            output.write(buf, 0, n)
            digest.update(buf, 0, n)
            total += n
            onProgress(total)
        }
    }

    private fun MessageDigest.hex() = digest().joinToString("") { "%02x".format(it) }

    private fun encode(name: String) = URLEncoder.encode(name, "UTF-8").replace("+", "%20")
}
