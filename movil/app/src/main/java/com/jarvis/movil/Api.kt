package com.jarvis.movil

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Error que devolvió Jarvis (401 = celular no vinculado, 409 = ocupado…). */
class ApiException(val code: Int, message: String) : Exception(message)

/**
 * Cliente del servidor de Jarvis en la PC (jarvis_mobile.py).
 * La conexión es HTTP dentro de Tailscale, que ya cifra todo el tráfico.
 */
class Api(private val base: String, private val token: String?) {

    private fun open(path: String, method: String, readTimeoutMs: Int): HttpURLConnection {
        val connection = URL("http://$base$path").openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 8000
        connection.readTimeout = readTimeoutMs
        connection.useCaches = false
        if (token != null) connection.setRequestProperty("Authorization", "Bearer $token")
        return connection
    }

    private fun read(connection: HttpURLConnection): ByteArray {
        try {
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
            if (code !in 200..299) {
                val message = try {
                    JSONObject(String(bytes, Charsets.UTF_8)).optString("error", "Error $code")
                } catch (e: Exception) {
                    "Error $code"
                }
                throw ApiException(code, message)
            }
            return bytes
        } finally {
            connection.disconnect()
        }
    }

    private fun post(path: String, body: ByteArray, contentType: String, timeoutMs: Int): JSONObject {
        val connection = open(path, "POST", timeoutMs)
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", contentType)
        connection.setFixedLengthStreamingMode(body.size)
        connection.outputStream.use { it.write(body) }
        return JSONObject(String(read(connection), Charsets.UTF_8))
    }

    fun postJson(path: String, body: JSONObject = JSONObject()): JSONObject =
        post(path, body.toString().toByteArray(Charsets.UTF_8), "application/json", 15000)

    fun postWav(path: String, wav: ByteArray): JSONObject = post(path, wav, "audio/wav", 30000)

    fun getJson(path: String, readTimeoutMs: Int = 15000): JSONObject =
        JSONObject(String(read(open(path, "GET", readTimeoutMs)), Charsets.UTF_8))

    fun getBytes(path: String): ByteArray = read(open(path, "GET", 20000))
}
