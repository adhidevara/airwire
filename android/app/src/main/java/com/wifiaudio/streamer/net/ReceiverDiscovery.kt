package com.wifiaudio.streamer.net

import android.content.Context
import android.net.wifi.WifiManager
import android.os.SystemClock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import kotlin.random.Random

data class DiscoveredReceiver(val name: String, val host: String, val port: Int)

/** Broadcasts DISCOVER and collects OFFER replies (see PROTOCOL.md). */
object ReceiverDiscovery {

    suspend fun scan(
        context: Context,
        port: Int = Protocol.DEFAULT_PORT,
        timeoutMs: Long = 1500,
    ): List<DiscoveredReceiver> = withContext(Dispatchers.IO) {
        val wifi = context.applicationContext.getSystemService(WifiManager::class.java)
        // Some devices filter broadcast traffic unless a multicast lock is held.
        val lock = wifi?.createMulticastLock("wifiaudio:discovery")?.apply {
            setReferenceCounted(false)
            acquire()
        }
        try {
            DatagramSocket().use { socket ->
                socket.broadcast = true
                val nonce = Random.nextInt()
                val message = Protocol.discover(nonce)
                for (address in broadcastAddresses()) {
                    runCatching { socket.send(DatagramPacket(message, message.size, address, port)) }
                }

                val found = linkedMapOf<String, DiscoveredReceiver>()
                val buf = ByteArray(1500)
                val deadline = SystemClock.elapsedRealtime() + timeoutMs
                while (true) {
                    val left = deadline - SystemClock.elapsedRealtime()
                    if (left <= 0) break
                    socket.soTimeout = left.toInt()
                    val packet = DatagramPacket(buf, buf.size)
                    try {
                        socket.receive(packet)
                    } catch (e: SocketTimeoutException) {
                        break
                    }
                    val offer = Protocol.parse(buf, packet.length) as? Protocol.Offer ?: continue
                    if (offer.nonce != nonce) continue
                    val host = packet.address.hostAddress ?: continue
                    found[host] = DiscoveredReceiver(offer.name.ifBlank { host }, host, offer.audioPort)
                }
                found.values.toList()
            }
        } finally {
            lock?.release()
        }
    }

    /** Subnet broadcast of every IPv4 interface (Wi-Fi client and hotspot), plus the global one. */
    private fun broadcastAddresses(): List<InetAddress> {
        val result = mutableListOf<InetAddress>()
        NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
            .filter { runCatching { it.isUp && !it.isLoopback }.getOrDefault(false) }
            .flatMap { it.interfaceAddresses }
            .filter { it.address is Inet4Address }
            .mapNotNullTo(result) { it.broadcast }
        result += InetAddress.getByName("255.255.255.255")
        return result.distinct()
    }
}
