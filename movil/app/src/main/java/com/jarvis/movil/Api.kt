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

    /** Cuadro JPEG de /api/screen y la cabecera X-Cursor ("x,y" o "none"). */
    fun getFrame(path: String): Pair<ByteArray, String?> {
        val connection = open(path, "GET", 20000)
        val bytes = read(connection)
        return bytes to connection.getHeaderField("X-Cursor")
    }

    /** Sube un archivo por partes (sin cargarlo entero en memoria); ``progress`` recibe los bytes enviados. */
    fun postStream(path: String, input: java.io.InputStream, size: Long, progress: (Long) -> Unit): JSONObject {
        val connection = open(path, "POST", 120000)
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/octet-stream")
        connection.setFixedLengthStreamingMode(size)
        connection.outputStream.use { out ->
            val buffer = ByteArray(256 * 1024)
            var sent = 0L
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                out.write(buffer, 0, read)
                sent += read
                progress(sent)
            }
        }
        return JSONObject(String(read(connection), Charsets.UTF_8))
    }

    /** Descarga a ``output`` por partes; ``progress`` recibe (bytes, total o -1). */
    fun download(path: String, output: java.io.OutputStream, progress: (Long, Long) -> Unit) {
        val connection = open(path, "GET", 120000)
        val code = connection.responseCode
        if (code !in 200..299) read(connection)  // lanza ApiException con el mensaje de Jarvis
        val total = connection.getHeaderField("Content-Length")?.toLongOrNull() ?: -1L
        try {
            connection.inputStream.use { input ->
                val buffer = ByteArray(256 * 1024)
                var received = 0L
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    output.write(buffer, 0, read)
                    received += read
                    progress(received, total)
                }
            }
        } finally {
            connection.disconnect()
        }
    }

    /** Flujo continuo (vídeo): conexión abierta y ya comprobada; quien la usa la cierra. */
    fun openStream(path: String): HttpURLConnection {
        val connection = open(path, "GET", 5000)
        val code = connection.responseCode
        if (code !in 200..299) read(connection)  // lanza ApiException con el mensaje de Jarvis
        return connection
    }
}
