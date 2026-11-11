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

    /**
     * Lee la respuesta entera y cierra el flujo sin disconnect(): así la conexión
     * vuelve al grupo y la siguiente petición (p. ej. el próximo cuadro de la
     * pantalla) no tiene que abrir otra.
     */
    private fun read(connection: HttpURLConnection): ByteArray {
        val code = try {
            connection.responseCode
        } catch (e: Exception) {
            connection.disconnect()
            throw e
        }
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

    /** POST con JSON que devuelve bytes (p. ej. el WAV de /api/tts). */
    fun postForBytes(path: String, body: JSONObject, timeoutMs: Int = 60000): ByteArray {
        val connection = open(path, "POST", timeoutMs)
        val data = body.toString().toByteArray(Charsets.UTF_8)
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setFixedLengthStreamingMode(data.size)
        connection.outputStream.use { it.write(data) }
        return read(connection)
    }

    fun getJson(path: String, readTimeoutMs: Int = 15000): JSONObject =
        JSONObject(String(read(open(path, "GET", readTimeoutMs)), Charsets.UTF_8))

    fun getBytes(path: String): ByteArray = read(open(path, "GET", 20000))

    /** Flujo continuo (vídeo): conexión abierta y ya comprobada; quien la usa la cierra. */
    fun openStream(path: String): HttpURLConnection {
        val connection = open(path, "GET", 5000)
        val code = connection.responseCode
        if (code !in 200..299) read(connection)  // lanza ApiException con el mensaje de Jarvis
        return connection
    }
}
