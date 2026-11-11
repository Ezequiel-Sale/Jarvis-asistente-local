package com.jarvis.movil

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import org.json.JSONObject
import java.io.File
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * Lee en voz alta las respuestas con la voz de Piper de la PC (/api/tts), una tras
 * otra. Si la PC no puede, usa la voz del teléfono. Lo comparten el chat y el
 * servicio en segundo plano.
 */
object Speaker {
    private class Item(val context: Context, val api: Api, val text: String)

    private val queue = LinkedBlockingQueue<Item>()
    private var worker: Thread? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    @Volatile
    private var player: MediaPlayer? = null

    @Volatile
    private var generation = 0

    /** Avisa al empezar y al terminar de hablar (el servicio lo usa para los auriculares). */
    @Volatile
    var onPlaying: ((Boolean) -> Unit)? = null

    @Synchronized
    fun say(context: Context, api: Api, text: String) {
        if (text.isBlank()) return
        queue.put(Item(context.applicationContext, api, text))
        if (worker?.isAlive != true) worker = thread(name = "voz", isDaemon = true) { loop() }
    }

    /** Calla y descarta lo pendiente. */
    fun stop() {
        generation++
        queue.clear()
        try {
            player?.stop()
        } catch (e: Exception) {
        }
        tts?.stop()
    }

    private fun loop() {
        while (true) {
            val item = queue.poll(60, TimeUnit.SECONDS) ?: return
            val mine = generation
            onPlaying?.invoke(true)
            try {
                if (!playPiper(item, mine)) playPhone(item)
            } finally {
                if (queue.isEmpty()) onPlaying?.invoke(false)
            }
        }
    }

    private fun playPiper(item: Item, mine: Int): Boolean {
        val wav = try {
            item.api.postForBytes("/api/tts", JSONObject().put("text", item.text))
        } catch (e: Exception) {
            return false
        }
        if (mine != generation) return true
        val file = File(item.context.cacheDir, "voz.wav")
        file.writeBytes(wav)
        val done = CountDownLatch(1)
        val media = MediaPlayer()
        try {
            media.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            media.setDataSource(file.path)
            media.setOnCompletionListener { done.countDown() }
            media.setOnErrorListener { _, _, _ -> done.countDown(); true }
            media.prepare()
            player = media
            media.start()
            done.await(5, TimeUnit.MINUTES)
        } catch (e: Exception) {
            return false
        } finally {
            player = null
            media.release()
            file.delete()
        }
        return true
    }

    private fun playPhone(item: Item) {
        val ready = CountDownLatch(1)
        if (tts == null) {
            tts = TextToSpeech(item.context) { status ->
                ttsReady = status == TextToSpeech.SUCCESS
                if (ttsReady) tts?.language = Locale("es")
                ready.countDown()
            }
            ready.await(5, TimeUnit.SECONDS)
        }
        val engine = tts ?: return
        if (!ttsReady) return
        val done = CountDownLatch(1)
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) = done.countDown()

            @Deprecated("Requerido por la clase base.")
            override fun onError(id: String?) = done.countDown()
        })
        engine.speak(item.text, TextToSpeech.QUEUE_FLUSH, null, "jarvis")
        done.await(5, TimeUnit.MINUTES)
    }
}
