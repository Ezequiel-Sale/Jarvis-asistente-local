package com.jarvis.movil

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Pantallas de la PC en vivo (solo ver) y una caja para darle órdenes a Jarvis sobre
 * lo que se ve. Pide capturas JPEG seguidas a /api/screen; no es vídeo, son unos
 * pocos cuadros por segundo. La respuesta de Jarvis aparece debajo de la imagen.
 */
class ScreenActivity : Activity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var image: ZoomImageView
    private lateinit var topBar: LinearLayout
    private lateinit var monitors: LinearLayout
    private lateinit var quality: TextView
    private lateinit var info: TextView
    private lateinit var bottom: LinearLayout
    private lateinit var reply: TextView
    private lateinit var input: EditText
    private lateinit var mic: Button

    private val ui = Handler(Looper.getMainLooper())
    private var recorder: Recorder? = null
    private var question: AlertDialog? = null
    private var questionId: String? = null

    @Volatile
    private var generation = 0

    @Volatile
    private var monitor = 0

    @Volatile
    private var saver = false

    private fun api(): Api? {
        val base = prefs.getString("base", null)
        val token = prefs.getString("token", null)
        return if (base.isNullOrEmpty() || token.isNullOrEmpty()) null else Api(base, token)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    // --- Interfaz -----------------------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("jarvis", MODE_PRIVATE)
        monitor = prefs.getInt("pantalla", -1)
        saver = prefs.getBoolean("pantalla_ahorro", false)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        image = ZoomImageView(this).apply { onTap = { toggleBars() } }
        root.addView(image, FrameLayout.LayoutParams(-1, -1))

        topBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(0xCC0D1B2A.toInt())
            setPadding(dp(6), dp(4), dp(6), dp(4))
        }
        topBar.addView(chip("✕", R.color.text) { finish() })
        monitors = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        topBar.addView(HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            addView(monitors)
        }, LinearLayout.LayoutParams(0, -2, 1f))
        quality = chip("", R.color.violet) { toggleQuality() }
        topBar.addView(quality)
        root.addView(topBar, FrameLayout.LayoutParams(-1, -2, Gravity.TOP))

        info = TextView(this).apply {
            setTextColor(getColor(R.color.muted))
            textSize = 11f
            typeface = Typeface.MONOSPACE
            setPadding(dp(10), dp(4), dp(10), dp(4))
            setBackgroundColor(0x99000000.toInt())
        }
        root.addView(info, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.END).apply { topMargin = dp(52) })

        bottom = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(0xDD0D1B2A.toInt())
            setPadding(dp(8), dp(6), dp(8), dp(8))
        }
        reply = TextView(this).apply {
            setTextColor(getColor(R.color.text))
            textSize = 14f
            maxLines = 4
            visibility = View.GONE
            setPadding(dp(4), 0, dp(4), dp(6))
            setOnClickListener { maxLines = if (maxLines == 4) 30 else 4 }
        }
        bottom.addView(reply)
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        input = EditText(this).apply {
            hint = "Dile a Jarvis qué hacer con lo que ves…"
            setHintTextColor(getColor(R.color.muted))
            setTextColor(getColor(R.color.text))
            textSize = 15f
            maxLines = 3
            imeOptions = EditorInfo.IME_ACTION_SEND
            setBackgroundResource(R.drawable.bg_input)
            setPadding(dp(12), dp(8), dp(12), dp(8))
            setOnEditorActionListener { _, action, _ ->
                if (action == EditorInfo.IME_ACTION_SEND) { send(); true } else false
            }
        }
        row.addView(input, LinearLayout.LayoutParams(0, -2, 1f))
        mic = button("🎙") { toggleMic() }
        row.addView(mic, LinearLayout.LayoutParams(dp(52), dp(48)).apply { marginStart = dp(6) })
        row.addView(button("➤") { send() }, LinearLayout.LayoutParams(dp(52), dp(48)).apply { marginStart = dp(4) })
        bottom.addView(row)
        root.addView(bottom, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))

        setContentView(root)
        refreshQuality()
    }

    private fun chip(text: String, color: Int, action: () -> Unit): TextView = TextView(this).apply {
        this.text = text
        setTextColor(getColor(color))
        textSize = 13f
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        setPadding(dp(12), dp(10), dp(12), dp(10))
        setOnClickListener { action() }
    }

    private fun button(text: String, action: () -> Unit): Button = Button(this).apply {
        this.text = text
        textSize = 18f
        setTextColor(getColor(R.color.bg))
        backgroundTintList = getColorStateList(R.color.cyan)
        setPadding(0, 0, 0, 0)
        setOnClickListener { action() }
    }

    private fun toggleBars() {
        val show = topBar.visibility != View.VISIBLE
        val state = if (show) View.VISIBLE else View.GONE
        topBar.visibility = state
        bottom.visibility = state
        info.visibility = state
    }

    private fun toggleQuality() {
        saver = !saver
        prefs.edit().putBoolean("pantalla_ahorro", saver).apply()
        refreshQuality()
    }

    private fun refreshQuality() {
        quality.text = if (saver) "AHORRO" else "HD"
    }

    private fun showMonitors(list: List<Pair<Int, String>>) {
        monitors.removeAllViews()
        for ((number, name) in list) {
            val selected = number == monitor
            monitors.addView(chip(name, if (selected) R.color.cyan else R.color.muted) {
                monitor = number
                prefs.edit().putInt("pantalla", number).apply()
                showMonitors(list)
            }.apply { if (selected) setBackgroundColor(getColor(R.color.cyan_dark)) })
        }
    }

    // --- Ciclo de vida ------------------------------------------------------------------

    override fun onStart() {
        super.onStart()
        val client = api() ?: return finish()
        val mine = ++generation
        info.text = "Conectando…"
        thread(name = "pantallas") { loadMonitors(client, mine) }
        thread(name = "eventos-pantalla") { pollEvents(client, mine) }
    }

    override fun onStop() {
        super.onStop()
        generation++
        recorder?.stop()
        recorder = null
        mic.text = "🎙"
    }

    private fun loadMonitors(client: Api, mine: Int) {
        try {
            val reply = client.getJson("/api/screens")
            if (!reply.optBoolean("allowed")) {
                ui.post { info.text = "Ver la pantalla está desactivado en los Ajustes de Jarvis (App del celular)." }
                return
            }
            val screens = reply.getJSONArray("screens")
            val list = ArrayList<Pair<Int, String>>()
            var primary = 0
            for (i in 0 until screens.length()) {
                val screen = screens.getJSONObject(i)
                val number = screen.getInt("number")
                if (screen.optBoolean("primary")) primary = number
                list.add(number to "MONITOR $number")
            }
            if (list.size > 1) list.add(0 to "TODOS")
            if (list.none { it.first == monitor }) monitor = if (primary != 0) primary else list.firstOrNull()?.first ?: 0
            ui.post { if (mine == generation) showMonitors(list) }
            frames(client, mine)
        } catch (e: ApiException) {
            ui.post { if (e.code == 401) finish() else info.text = e.message }
        } catch (e: Exception) {
            ui.post { info.text = "No pude conectar con la PC." }
        }
    }

    /** Pide cuadros seguidos: HD ~4 por segundo, ahorro ~2 y más livianos. */
    private fun frames(client: Api, mine: Int) {
        var shown = 0
        var since = SystemClock.elapsedRealtime()
        var kb = 0
        while (mine == generation) {
            val start = SystemClock.elapsedRealtime()
            val economy = saver
            val wide = if (monitor == 0) 1.6 else 1.0
            val width = ((if (economy) 960 else 1600) * wide).toInt()
            val q = if (economy) 40 else 60
            try {
                val bytes = client.getBytes("/api/screen?n=$monitor&w=$width&q=$q")
                val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                shown++
                kb += bytes.size / 1024
                val now = SystemClock.elapsedRealtime()
                val text = if (now - since >= 2000) {
                    val seconds = (now - since) / 1000.0
                    val line = String.format("%.1f cuadros/s · %d KB/s", shown / seconds, (kb / seconds).toInt())
                    shown = 0
                    kb = 0
                    since = now
                    line
                } else null
                ui.post {
                    if (mine == generation && bitmap != null) {
                        image.setFrame(bitmap)
                        if (text != null) info.text = text
                    }
                }
            } catch (e: ApiException) {
                if (e.code == 401) {
                    ui.post { finish() }
                    return
                }
                ui.post { if (mine == generation) info.text = e.message }
                Thread.sleep(2000)
            } catch (e: Exception) {
                ui.post { if (mine == generation) info.text = "Sin conexión con la PC; reintentando…" }
                Thread.sleep(2000)
            }
            val pause = (if (economy) 500L else 250L) - (SystemClock.elapsedRealtime() - start)
            if (pause > 0) Thread.sleep(pause)
        }
    }

    // --- Respuestas de Jarvis -------------------------------------------------------------

    private fun pollEvents(client: Api, mine: Int) {
        var last = -1
        while (mine == generation) {
            try {
                val initial = last < 0
                val reply = client.getJson("/api/events?since=$last&wait=${if (initial) 0 else 25}", 40000)
                last = reply.getInt("last")
                if (initial) continue  // lo anterior ya se vio en el chat
                val events = reply.getJSONArray("events")
                val pending = if (reply.isNull("pending")) null else reply.optString("pending")
                ui.post {
                    if (mine != generation) return@post
                    for (i in 0 until events.length()) {
                        val event = events.getJSONObject(i)
                        val text = event.optString("text")
                        when (event.optString("type")) {
                            "live", "assistant", "notice" -> if (text.isNotEmpty()) showReply(text)
                            "question" -> if (event.optString("qid") == pending) ask(event)
                            "resolved" -> if (event.optString("qid") == questionId) {
                                question?.dismiss()
                                question = null
                            }
                        }
                    }
                }
            } catch (e: ApiException) {
                if (e.code == 401) return
                Thread.sleep(3000)
            } catch (e: Exception) {
                Thread.sleep(3000)
            }
        }
    }

    private fun showReply(text: String) {
        reply.text = text
        reply.visibility = View.VISIBLE
    }

    private fun ask(event: JSONObject) {
        val qid = event.optString("qid")
        question?.dismiss()
        questionId = qid
        fun answer(approve: Boolean) {
            val client = api()
            thread {
                try {
                    client?.postJson("/api/answer", JSONObject().put("qid", qid).put("approve", approve))
                } catch (e: Exception) {
                    ui.post { toast(e.message ?: "No pude enviar la respuesta.") }
                }
            }
        }
        question = AlertDialog.Builder(this)
            .setTitle("Jarvis pregunta")
            .setMessage(event.optString("text"))
            .setPositiveButton(event.optString("yes", "SÍ")) { _, _ -> answer(true) }
            .setNegativeButton(event.optString("no", "NO")) { _, _ -> answer(false) }
            .setCancelable(false)
            .show()
    }

    // --- Enviar órdenes -------------------------------------------------------------------

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    private fun send() {
        val text = input.text.toString().trim()
        val client = api()
        if (text.isEmpty() || client == null) return
        input.setText("")
        showReply("Enviado: $text")
        thread {
            try {
                client.postJson("/api/message", JSONObject().put("text", text))
            } catch (e: Exception) {
                val reason = if (e is ApiException) e.message else "No pude conectar con Jarvis."
                ui.post {
                    toast(reason ?: "No pude enviar el mensaje.")
                    if (input.text.isEmpty()) input.setText(text)
                }
            }
        }
    }

    private fun toggleMic() {
        val current = recorder
        if (current != null) {
            recorder = null
            mic.text = "🎙"
            val wav = current.stop()
            val client = api()
            if (wav == null || client == null) {
                toast("No se grabó nada.")
                return
            }
            showReply("Enviando audio…")
            thread {
                try {
                    client.postWav("/api/audio", wav)
                } catch (e: Exception) {
                    val reason = if (e is ApiException) e.message else "No pude enviar el audio."
                    ui.post { toast(reason ?: "No pude enviar el audio.") }
                }
            }
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            return
        }
        val fresh = Recorder()
        if (!fresh.start()) {
            toast("No pude usar el micrófono.")
            return
        }
        recorder = fresh
        mic.text = "■"
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1 && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) toggleMic()
    }
}
