package com.jarvis.movil

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import kotlin.concurrent.thread

/**
 * Chat con el Jarvis de la PC: órdenes por texto o voz, respuestas y confirmaciones.
 * Todo llega por la cola de eventos del servidor (espera larga), así el chat muestra
 * lo mismo que la ventana de Jarvis aunque la orden se haya dado en la PC.
 */
class MainActivity : Activity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var status: TextView
    private lateinit var setup: View
    private lateinit var scroll: ScrollView
    private lateinit var messages: LinearLayout
    private lateinit var composer: View
    private lateinit var input: EditText
    private lateinit var mic: Button
    private lateinit var address: EditText
    private lateinit var code: EditText
    private lateinit var pair: Button
    private lateinit var setupError: TextView

    private val ui = Handler(Looper.getMainLooper())
    private val questions = HashMap<String, QuestionCard>()
    private var recorder: Recorder? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var connected = false
    private var busy = false
    private var mode = "idle"

    /** Cambia cada vez que se reinicia la escucha: los hilos viejos se detienen solos. */
    @Volatile
    private var generation = 0

    private class QuestionCard(val buttons: View, val result: TextView)

    // --- Ciclo de vida ---------------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = getSharedPreferences("jarvis", MODE_PRIVATE)
        status = findViewById(R.id.status)
        setup = findViewById(R.id.setup)
        scroll = findViewById(R.id.scroll)
        messages = findViewById(R.id.messages)
        composer = findViewById(R.id.composer)
        input = findViewById(R.id.input)
        mic = findViewById(R.id.mic)
        address = findViewById(R.id.address)
        code = findViewById(R.id.code)
        pair = findViewById(R.id.pair)
        setupError = findViewById(R.id.setup_error)

        findViewById<Button>(R.id.send).setOnClickListener { sendText() }
        mic.setOnClickListener { toggleMic() }
        pair.setOnClickListener { doPair() }
        findViewById<View>(R.id.menu).setOnClickListener { showMenu(it) }
        address.setText(prefs.getString("base", ""))
        if (prefs.getBoolean("leer", false)) startTts()
    }

    override fun onStart() {
        super.onStart()
        if (api() != null) {
            showChat()
            startPolling()
        } else {
            showSetup("")
        }
    }

    override fun onStop() {
        super.onStop()
        generation++
        recorder?.stop()
        recorder = null
        mic.text = "🎙"
    }

    override fun onDestroy() {
        tts?.shutdown()
        super.onDestroy()
    }

    // --- Vinculación -------------------------------------------------------------

    private fun api(): Api? {
        val base = prefs.getString("base", null)
        val token = prefs.getString("token", null)
        return if (base.isNullOrEmpty() || token.isNullOrEmpty()) null else Api(base, token)
    }

    private fun showSetup(message: String) {
        generation++
        setup.visibility = View.VISIBLE
        scroll.visibility = View.GONE
        composer.visibility = View.GONE
        setupError.text = message
        pair.isEnabled = true
        status.text = "● SIN VINCULAR"
        status.setTextColor(getColor(R.color.muted))
    }

    private fun showChat() {
        setup.visibility = View.GONE
        scroll.visibility = View.VISIBLE
        composer.visibility = View.VISIBLE
    }

    private fun doPair() {
        var base = address.text.toString().trim()
            .removePrefix("http://").removePrefix("https://").trimEnd('/')
        val digits = code.text.toString().filter { it.isDigit() }
        if (base.isEmpty() || digits.length != 6) {
            setupError.text = "Escribe la dirección y el código de 6 cifras que muestra Jarvis en la PC."
            return
        }
        if (!base.contains(":")) base += ":8765"
        pair.isEnabled = false
        setupError.text = ""
        val target = base
        thread {
            try {
                val body = JSONObject().put("code", digits).put("device", Build.MODEL ?: "Android")
                val token = Api(target, null).postJson("/api/pair", body).getString("token")
                prefs.edit().putString("base", target).putString("token", token).apply()
                ui.post {
                    code.setText("")
                    showChat()
                    startPolling()
                }
            } catch (e: ApiException) {
                ui.post {
                    setupError.text = e.message
                    pair.isEnabled = true
                }
            } catch (e: Exception) {
                ui.post {
                    setupError.text = "No pude conectar con $target.\n• ¿Tailscale está activado en este " +
                        "celular?\n• ¿La «App del celular» está activada en los Ajustes de Jarvis?\n" +
                        "• Si Windows preguntó por el firewall, hay que permitir el acceso."
                    pair.isEnabled = true
                }
            }
        }
    }

    private fun unpair(message: String) {
        prefs.edit().remove("token").apply()
        showSetup(message)
    }

    // --- Escucha de eventos -------------------------------------------------------

    private fun startPolling() {
        val client = api() ?: return
        val mine = ++generation
        messages.removeAllViews()
        questions.clear()
        connected = false
        refreshStatus()
        thread(name = "eventos") {
            var last = -1
            while (mine == generation) {
                try {
                    val initial = last < 0
                    val wait = if (initial) 0 else 25
                    val reply = client.getJson("/api/events?since=$last&wait=$wait", 40000)
                    val events = reply.getJSONArray("events")
                    val pending = if (reply.isNull("pending")) null else reply.optString("pending")
                    val nowBusy = reply.optBoolean("busy")
                    last = reply.getInt("last")
                    ui.post {
                        if (mine == generation) {
                            connected = true
                            render(events, pending, initial)
                            busy = nowBusy
                            if (initial) mode = if (nowBusy) "processing" else "idle"
                            if (!nowBusy && mode == "processing") mode = "idle"
                            refreshStatus()
                        }
                    }
                } catch (e: ApiException) {
                    if (e.code == 401) {
                        ui.post { if (mine == generation) unpair("Este celular ya no está vinculado. Vincúlalo de nuevo.") }
                        return@thread
                    }
                    offline(mine)
                } catch (e: Exception) {
                    offline(mine)
                }
            }
        }
    }

    private fun offline(mine: Int) {
        ui.post {
            if (mine == generation) {
                connected = false
                refreshStatus()
            }
        }
        Thread.sleep(3000)
    }

    private fun render(events: JSONArray, pending: String?, initial: Boolean) {
        if (initial && events.length() == 0) {
            addNotice("Conectado con Jarvis. Escribe una orden o pulsa 🎙 para hablar.")
        }
        for (i in 0 until events.length()) {
            val event = events.getJSONObject(i)
            val text = event.optString("text")
            when (event.optString("type")) {
                "user" -> addBubble(text, true)
                "assistant" -> {
                    addBubble(text, false)
                    if (!initial) speak(text)
                }
                "notice" -> addNotice(text)
                "state" -> mode = event.optString("mode", "idle")
                "question" -> addQuestion(event, pending)
                "resolved" -> questions[event.optString("qid")]?.let {
                    it.buttons.visibility = View.GONE
                    it.result.text = text
                    it.result.visibility = View.VISIBLE
                }
            }
        }
        if (events.length() > 0) scroll.post { scroll.smoothScrollTo(0, messages.height) }
    }

    private fun refreshStatus() {
        val (text, color) = when {
            !connected -> "● SIN CONEXIÓN" to R.color.error
            recorder != null -> "● GRABANDO… TOCA ■ PARA ENVIAR" to R.color.error
            mode == "listening" -> "● LA PC TE ESCUCHA…" to R.color.cyan
            busy || mode == "processing" -> "● PROCESANDO…" to R.color.violet
            else -> "● LISTO" to R.color.cyan
        }
        status.text = text
        status.setTextColor(getColor(color))
    }

    // --- Dibujo del chat -----------------------------------------------------------

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun rounded(color: Int, stroke: Int? = null): GradientDrawable = GradientDrawable().apply {
        setColor(color)
        cornerRadius = dp(14).toFloat()
        if (stroke != null) setStroke(dp(1), stroke)
    }

    private fun label(text: String, color: Int): TextView = TextView(this).apply {
        this.text = text
        setTextColor(color)
        textSize = 10f
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
    }

    private fun addBubble(text: String, user: Boolean) {
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(getColor(if (user) R.color.cyan_dark else R.color.panel_light))
            setPadding(dp(14), dp(9), dp(14), dp(10))
        }
        bubble.addView(label(if (user) "TÚ" else "JARVIS", getColor(if (user) R.color.cyan else R.color.violet)))
        bubble.addView(TextView(this).apply {
            this.text = text
            setTextColor(getColor(R.color.text))
            textSize = 16f
            setLineSpacing(dp(2).toFloat(), 1f)
            setTextIsSelectable(true)
        })
        val params = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply {
            gravity = if (user) Gravity.END else Gravity.START
            topMargin = dp(5)
            bottomMargin = dp(5)
            if (user) marginStart = dp(48) else marginEnd = dp(48)
        }
        messages.addView(bubble, params)
    }

    private fun addNotice(text: String) {
        val notice = TextView(this).apply {
            this.text = text
            setTextColor(getColor(R.color.muted))
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(dp(12), dp(10), dp(12), dp(10))
        }
        messages.addView(notice)
    }

    private fun addQuestion(event: JSONObject, pending: String?) {
        val qid = event.optString("qid")
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(getColor(R.color.panel), getColor(R.color.cyan))
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        card.addView(label("JARVIS PREGUNTA", getColor(R.color.cyan)))
        card.addView(TextView(this).apply {
            text = event.optString("text")
            setTextColor(getColor(R.color.text))
            textSize = 16f
            setPadding(0, dp(4), 0, dp(8))
        })
        if (event.optBoolean("image") && qid == pending) {
            val image = ImageView(this).apply {
                adjustViewBounds = true
                setPadding(0, 0, 0, dp(8))
            }
            card.addView(image)
            val client = api()
            thread {
                try {
                    val bytes = client?.getBytes("/api/question/$qid/image") ?: return@thread
                    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return@thread
                    ui.post {
                        image.setImageBitmap(bitmap)
                        scroll.post { scroll.smoothScrollTo(0, messages.height) }
                    }
                } catch (e: Exception) {
                    // sin imagen: la pregunta sigue pudiéndose responder
                }
            }
        }
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val result = TextView(this).apply {
            setTextColor(getColor(R.color.muted))
            textSize = 13f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            visibility = View.GONE
        }

        fun answer(approve: Boolean) {
            buttons.visibility = View.GONE
            result.text = "Enviando…"
            result.visibility = View.VISIBLE
            val client = api()
            thread {
                try {
                    client?.postJson("/api/answer", JSONObject().put("qid", qid).put("approve", approve))
                } catch (e: Exception) {
                    ui.post { result.text = e.message ?: "No pude enviar la respuesta." }
                }
            }
        }

        val yes = Button(this).apply {
            text = event.optString("yes", "SÍ")
            setTextColor(getColor(R.color.bg))
            backgroundTintList = getColorStateList(R.color.cyan)
            setOnClickListener { answer(true) }
        }
        val no = Button(this).apply {
            text = event.optString("no", "NO")
            setTextColor(getColor(R.color.text))
            backgroundTintList = getColorStateList(R.color.panel_light)
            setOnClickListener { answer(false) }
        }
        val half = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        buttons.addView(yes, half)
        buttons.addView(no, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginStart = dp(8)
        })
        card.addView(buttons)
        card.addView(result)
        if (qid != pending) {  // del historial: ya se respondió o venció
            buttons.visibility = View.GONE
            result.text = "Sin respuesta pendiente"
            result.visibility = View.VISIBLE
        }
        questions[qid] = QuestionCard(buttons, result)
        val params = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply {
            topMargin = dp(6)
            bottomMargin = dp(6)
        }
        messages.addView(card, params)
    }

    // --- Enviar órdenes --------------------------------------------------------------

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    private fun sendText() {
        val text = input.text.toString().trim()
        val client = api()
        if (text.isEmpty() || client == null) return
        input.setText("")
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
            refreshStatus()
            val client = api()
            if (wav == null || client == null) {
                toast("No se grabó nada.")
                return
            }
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
        refreshStatus()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1 && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            toggleMic()
        } else if (requestCode == 1) {
            toast("Sin permiso de micrófono solo puedes escribir.")
        }
    }

    // --- Menú y lectura en voz alta ------------------------------------------------------

    private fun showMenu(anchor: View) {
        val reading = prefs.getBoolean("leer", false)
        val menu = PopupMenu(this, anchor)
        menu.menu.add(0, 1, 0, if (reading) "No leer las respuestas en voz alta" else "Leer las respuestas en voz alta")
        menu.menu.add(0, 2, 1, "Cancelar lo que está haciendo")
        menu.menu.add(0, 3, 2, "Desvincular este celular")
        menu.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                1 -> {
                    prefs.edit().putBoolean("leer", !reading).apply()
                    if (!reading) startTts() else tts?.stop()
                }
                2 -> {
                    val client = api()
                    thread {
                        try {
                            client?.postJson("/api/cancel")
                        } catch (e: Exception) {
                            ui.post { toast("No pude conectar con Jarvis.") }
                        }
                    }
                }
                3 -> AlertDialog.Builder(this)
                    .setMessage("¿Desvincular este celular? Para volver a usarlo tendrás que vincularlo con un código nuevo.")
                    .setPositiveButton("Desvincular") { _, _ -> unpair("") }
                    .setNegativeButton("Cancelar", null)
                    .show()
            }
            true
        }
        menu.show()
    }

    private fun startTts() {
        if (tts != null) return
        tts = TextToSpeech(this) { result ->
            ttsReady = result == TextToSpeech.SUCCESS
            if (ttsReady) tts?.language = Locale("es")
        }
    }

    private fun speak(text: String) {
        if (ttsReady && prefs.getBoolean("leer", false)) {
            tts?.speak(text, TextToSpeech.QUEUE_ADD, null, "jarvis")
        }
    }
}
