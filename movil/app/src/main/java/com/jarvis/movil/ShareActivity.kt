package com.jarvis.movil

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * «Compartir → Jarvis» desde cualquier app: un link se abre en la PC, un texto va al
 * portapapeles de la PC y las fotos o archivos se guardan en Descargas de la PC.
 */
class ShareActivity : JarvisActivity() {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var bar: ProgressBar
    private var started = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(getColor(R.color.bg))
            setPadding(dp(32), dp(32), dp(32), dp(32))
        }
        box.addView(TextView(this).apply {
            text = "J.A.R.V.I.S."
            setTextColor(getColor(R.color.cyan))
            textSize = 20f
            gravity = Gravity.CENTER
        })
        status = TextView(this).apply {
            setTextColor(getColor(R.color.text))
            textSize = 15f
            gravity = Gravity.CENTER
            setPadding(0, dp(16), 0, dp(12))
        }
        box.addView(status)
        bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 1000
            progressTintList = getColorStateList(R.color.cyan)
        }
        box.addView(bar, LinearLayout.LayoutParams(-1, dp(8)))
        setContentView(box)
    }

    override fun onStart() {
        super.onStart()
        if (started) return
        started = true
        val client = jarvisApi()
        if (client == null) {
            finishWith("Primero vincula este celular con Jarvis.")
            return
        }
        val uris = streams(intent)
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)
        if (uris.isEmpty() && text.isNullOrBlank()) {
            finishWith("No llegó nada para compartir.")
            return
        }
        status.text = if (uris.isEmpty()) "Enviando a la PC…" else "Enviando ${uris.size} archivo(s) a la PC…"
        thread(name = "compartir") {
            val result = try {
                if (uris.isEmpty()) {
                    client.postJson("/api/share", JSONObject().put("text", text)).optString("text", "Listo.")
                } else {
                    val names = uris.mapIndexed { index, uri ->
                        Transfers.upload(this, client, uri) { sent, size ->
                            val done = (index + if (size > 0) sent.toDouble() / size else 0.0) / uris.size
                            ui.post {
                                bar.progress = (done * 1000).toInt()
                                status.text = "Enviando ${index + 1} de ${uris.size}… ${Transfers.readable(sent)}"
                            }
                        }
                    }
                    if (names.size == 1) "Listo: «${names[0]}» quedó en Descargas de la PC."
                    else "Listo: ${names.size} archivos quedaron en Descargas de la PC."
                }
            } catch (e: Exception) {
                "No se pudo enviar: ${e.message ?: "sin conexión con la PC"}"
            }
            ui.post { finishWith(result) }
        }
    }

    private fun streams(intent: Intent): List<Uri> {
        val list = ArrayList<Uri>()
        if (intent.action == Intent.ACTION_SEND_MULTIPLE) {
            val extra: List<Uri>? = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM)
            }
            extra?.let { list.addAll(it) }
        } else {
            val one: Uri? = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(Intent.EXTRA_STREAM)
            }
            one?.let { list.add(it) }
        }
        return list
    }

    private fun finishWith(message: String) {
        status.text = message
        bar.progress = 1000
        ui.postDelayed({ finish() }, 2200)
    }
}
