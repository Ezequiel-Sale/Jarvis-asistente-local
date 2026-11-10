package com.jarvis.movil

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread

/**
 * Graba la voz a 16 kHz mono (lo que espera Whisper en la PC) y la entrega como WAV.
 * El audio solo vive en memoria: se envía a la PC y se descarta.
 */
class Recorder {
    private val rate = 16000
    private val maxBytes = rate * 2 * 60  // 60 s
    private val pcm = ByteArrayOutputStream()
    private var record: AudioRecord? = null
    private var worker: Thread? = null

    @Volatile
    private var running = false

    /** Requiere el permiso RECORD_AUDIO ya concedido. */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        val minBuffer = AudioRecord.getMinBufferSize(
            rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuffer <= 0) return false
        val recorder = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, rate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuffer * 2
            )
        } catch (e: Exception) {
            return false
        }
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            return false
        }
        record = recorder
        running = true
        recorder.startRecording()
        worker = thread(name = "grabacion") {
            val buffer = ByteArray(minBuffer)
            while (running && pcm.size() < maxBytes) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) synchronized(pcm) { pcm.write(buffer, 0, read) }
            }
        }
        return true
    }

    /** Detiene la grabación y devuelve el WAV (o null si duró menos de 0,4 s). */
    fun stop(): ByteArray? {
        running = false
        worker?.join(1500)
        record?.let {
            try {
                it.stop()
            } catch (e: IllegalStateException) {
                // ya estaba detenido
            }
            it.release()
        }
        record = null
        val data = synchronized(pcm) { pcm.toByteArray() }
        if (data.size < rate * 2 * 4 / 10) return null
        return wav(data)
    }

    private fun wav(data: ByteArray): ByteArray {
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray(Charsets.US_ASCII))
        header.putInt(36 + data.size)
        header.put("WAVE".toByteArray(Charsets.US_ASCII))
        header.put("fmt ".toByteArray(Charsets.US_ASCII))
        header.putInt(16)              // tamaño del bloque fmt
        header.putShort(1)             // PCM
        header.putShort(1)             // mono
        header.putInt(rate)
        header.putInt(rate * 2)        // bytes por segundo
        header.putShort(2)             // bytes por muestra
        header.putShort(16)            // bits por muestra
        header.put("data".toByteArray(Charsets.US_ASCII))
        header.putInt(data.size)
        return header.array() + data
    }
}
