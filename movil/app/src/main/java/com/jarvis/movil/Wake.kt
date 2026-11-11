package com.jarvis.movil

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * Encender la PC (Wake-on-LAN) solo en casa: el «paquete mágico» (6 bytes 0xFF y la
 * MAC 16 veces) por la red local. Los datos (MAC, IP, difusión) los da la PC con
 * /api/wol mientras está encendida y quedan guardados para cuando esté apagada.
 */
object Wake {
    private const val KEY = "wol"

    fun saved(context: Context): JSONObject? =
        context.getSharedPreferences("jarvis", Context.MODE_PRIVATE).getString(KEY, null)
            ?.let { runCatching { JSONObject(it) }.getOrNull() }

    /** Pide los datos a la PC (si está encendida) y los guarda. */
    fun refresh(context: Context, api: Api): JSONObject? = try {
        val data = api.getJson("/api/wol", 30000)
        context.getSharedPreferences("jarvis", Context.MODE_PRIVATE).edit().putString(KEY, data.toString()).apply()
        data
    } catch (e: Exception) {
        null
    }

    fun magicPacket(mac: String): ByteArray {
        val bytes = mac.split(":", "-").filter { it.isNotEmpty() }.map { it.toInt(16).toByte() }
        require(bytes.size == 6) { "MAC no válida: $mac" }
        return ByteArray(6) { 0xFF.toByte() } + ByteArray(16 * 6) { bytes[it % 6] }
    }

    @Suppress("DEPRECATION")
    fun onWifi(context: Context): Boolean {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return false
        return manager.allNetworks.any { network ->
            val caps = manager.getNetworkCapabilities(network) ?: return@any false
            !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                    caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
        }
    }

    /** Manda los paquetes (bloquea unos 300 ms; llamar fuera del hilo de la interfaz). Devuelve el aviso para el usuario. */
    fun send(context: Context): String {
        val data = saved(context)
            ?: return "Todavía no tengo los datos de tu PC: abre la app una vez con la PC encendida."
        val macs = data.optJSONArray("macs") ?: return "La PC no informó ninguna placa de red."
        if (!onWifi(context)) {
            return "Solo funciona en casa: conecta el celular al Wi-Fi de tu casa (el mismo que la PC)."
        }
        val targets = listOfNotNull(data.optString("broadcast").ifEmpty { null }, "255.255.255.255",
            data.optString("ip").ifEmpty { null })
        var sent = 0
        DatagramSocket().use { socket ->
            socket.broadcast = true
            repeat(3) {
                for (i in 0 until macs.length()) {
                    val packet = magicPacket(macs.getString(i))
                    for (target in targets) {
                        for (port in intArrayOf(9, 7)) {
                            try {
                                socket.send(DatagramPacket(packet, packet.size, InetAddress.getByName(target), port))
                                sent++
                            } catch (e: Exception) {
                                // una dirección que no se alcanza no impide las demás
                            }
                        }
                    }
                }
                Thread.sleep(100)
            }
        }
        if (sent == 0) return "No pude mandar la señal de encendido por la red."
        return if (data.optBoolean("wifi")) {
            "Señal enviada. Por Wi-Fi la PC solo despierta si estaba suspendida; en unos segundos debería conectarse."
        } else "Señal enviada. La PC debería encenderse en unos segundos."
    }
}
