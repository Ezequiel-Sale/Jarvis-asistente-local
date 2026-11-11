package com.jarvis.movil

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.view.Surface
import java.io.BufferedInputStream
import java.io.DataInputStream
import java.nio.ByteBuffer

/**
 * Reproduce el vídeo H.264 de /api/video en una superficie con el decodificador por
 * hardware del teléfono. Formato del flujo: 4 bytes de largo, 1 byte de banderas
 * (1 = cuadro clave) y el paquete en Annex B. El decodificador se configura con el
 * SPS/PPS del primer cuadro clave.
 */
class VideoPlayer(
    private val api: Api,
    private val surface: Surface,
    private val running: () -> Boolean,
    private val onSize: (Int, Int) -> Unit,
    private val onFrame: (Int) -> Unit,
) {

    /** Bloquea hasta que ``running`` sea falso o se corte la conexión (lanza la excepción). */
    fun play(path: String, widthHint: Int, heightHint: Int) {
        val connection = api.openStream(path)
        var codec: MediaCodec? = null
        try {
            val input = DataInputStream(BufferedInputStream(connection.inputStream, 1 shl 16))
            val info = MediaCodec.BufferInfo()
            var pts = 0L
            while (running()) {
                val length = input.readInt()
                val flags = input.readUnsignedByte()
                if (length <= 0 || length > 8 * 1024 * 1024) throw IllegalStateException("Paquete de vídeo no válido.")
                val data = ByteArray(length)
                input.readFully(data)
                if (codec == null) {
                    if (flags and 1 == 0) continue  // hasta el primer cuadro clave
                    val sets = parameterSets(data) ?: continue
                    val format = MediaFormat.createVideoFormat("video/avc", widthHint, heightHint)
                    format.setByteBuffer("csd-0", ByteBuffer.wrap(sets.first))
                    format.setByteBuffer("csd-1", ByteBuffer.wrap(sets.second))
                    if (Build.VERSION.SDK_INT >= 30) format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
                    format.setInteger(MediaFormat.KEY_PRIORITY, 0)  // tiempo real
                    codec = MediaCodec.createDecoderByType("video/avc")
                    codec.configure(format, surface, null, 0)
                    codec.start()
                }
                val decoder = codec ?: continue
                var index = -1
                while (index < 0 && running()) index = decoder.dequeueInputBuffer(10_000)
                if (index < 0) break
                val buffer = decoder.getInputBuffer(index) ?: continue
                buffer.clear()
                buffer.put(data)
                decoder.queueInputBuffer(index, 0, length, pts, 0)
                pts += 16_666
                drain(decoder, info, length)
            }
        } finally {
            try {
                codec?.stop()
            } catch (e: Exception) {
            }
            codec?.release()
            connection.disconnect()
        }
    }

    private fun drain(decoder: MediaCodec, info: MediaCodec.BufferInfo, bytes: Int) {
        var counted = false
        while (true) {
            val out = decoder.dequeueOutputBuffer(info, 0)
            when {
                out >= 0 -> {
                    decoder.releaseOutputBuffer(out, true)
                    onFrame(if (counted) 0 else bytes)
                    counted = true
                }
                out == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    val format = decoder.outputFormat
                    val width = if (format.containsKey("crop-right")) {
                        format.getInteger("crop-right") - format.getInteger("crop-left") + 1
                    } else format.getInteger(MediaFormat.KEY_WIDTH)
                    val height = if (format.containsKey("crop-bottom")) {
                        format.getInteger("crop-bottom") - format.getInteger("crop-top") + 1
                    } else format.getInteger(MediaFormat.KEY_HEIGHT)
                    onSize(width, height)
                }
                else -> return
            }
        }
    }

    companion object {
        /** SPS y PPS (con su código de inicio) de un cuadro clave en Annex B, o null. */
        fun parameterSets(data: ByteArray): Pair<ByteArray, ByteArray>? {
            var sps: ByteArray? = null
            var pps: ByteArray? = null
            val starts = ArrayList<Int>()
            var i = 0
            while (i + 3 <= data.size) {
                if (data[i].toInt() == 0 && data[i + 1].toInt() == 0 && data[i + 2].toInt() == 1) {
                    starts.add(i + 3)
                    i += 3
                } else i++
            }
            for ((n, start) in starts.withIndex()) {
                if (start >= data.size) continue
                var end = if (n + 1 < starts.size) starts[n + 1] - 3 else data.size
                while (end > start && data[end - 1].toInt() == 0) end--  // ceros del código de 4 bytes
                val nal = byteArrayOf(0, 0, 0, 1) + data.copyOfRange(start, end)
                when (data[start].toInt() and 0x1f) {
                    7 -> sps = nal
                    8 -> pps = nal
                }
            }
            val s = sps ?: return null
            val p = pps ?: return null
            return s to p
        }
    }
}
