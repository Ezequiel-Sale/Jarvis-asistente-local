package com.jarvis.movil

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
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
    private lateinit var tabs: View

    private val ui = Handler(Looper.getMainLooper())
    private val questions = HashMap<String, QuestionCard>()
    private var recorder: Recorder? = null
    private var connected = false
    private var busy = false
    private var mode = "idle"
    private var liveBubble: LinearLayout? = null
    private var liveText: TextView? = null

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
        findViewById<View>(R.id.menu).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        }
        findViewById<View>(R.id.screen).setOnClickListener { openScreen() }
        status.setOnClickListener {
            if (!connected && api() != null) {
                AlertDialog.Builder(this)
                    .setMessage("No hay conexión con la PC. ¿Intento encenderla? (solo funciona en casa)")
                    .setPositiveButton("⚡ Encender") { _, _ ->
                        thread {
                            val message = Wake.send(this)
                            ui.post { toast(message) }
                        }
                    }
                    .setNegativeButton("Cancelar", null)
                    .show()
            }
        }
        findViewById<View>(R.id.files).setOnClickListener {
            startActivity(Intent(this, FilesActivity::class.java))
        }
        val root = findViewById<ViewGroup>(android.R.id.content).getChildAt(0) as LinearLayout
        tabs = Tabs.bar(this, "chat").apply { visibility = View.GONE }
        root.addView(tabs)
        address.setText(prefs.getString("base", ""))
        handleAnswerIntent(intent)
    }

    override fun onStart() {
        super.onStart()
        Lock.onStart(this, findViewById<ViewGroup>(android.R.id.content))
        val client = api()
        if (client != null) {
            showChat()
            startPolling()
            Updater(this, client).check(manual = false)
            askNotificationPermission()
            JarvisService.start(this)
        } else {
            showSetup("")
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleAnswerIntent(intent)
    }

    /** SÍ de riesgo alto pulsado en una notificación: huella y luego la respuesta. */
    private fun handleAnswerIntent(intent: Intent?) {
        val qid = intent?.getStringExtra(NotifyReceiver.EXTRA_QID) ?: return
        val approve = intent.getBooleanExtra(NotifyReceiver.EXTRA_APPROVE, false)
        val text = intent.getStringExtra(NotifyReceiver.EXTRA_TEXT) ?: ""
        intent.removeExtra(NotifyReceiver.EXTRA_QID)  // que no se repita al girar o volver
        getSystemService(android.app.NotificationManager::class.java).cancel(JarvisService.ID_QUESTION)
        Lock.authenticate(this, "Confirmar acción", text.take(120), onSuccess = {
            NotifyReceiver.send(this, qid, approve) { error ->
                ui.post { toast(error?.let { "No se envió: $it" } ?: "Respuesta enviada a Jarvis.") }
            }
        })
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 && !prefs.getBoolean("pidio_avisos", false) &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            prefs.edit().putBoolean("pidio_avisos", true).apply()
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
        }
    }

    @Deprecated("Solo para la pantalla de bloqueo de Android 8–9.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (!Lock.onActivityResult(requestCode, resultCode)) {
            @Suppress("DEPRECATION")
            super.onActivityResult(requestCode, resultCode, data)
        }
    }

    override fun onStop() {
        super.onStop()
        Lock.onStop()
        generation++
        recorder?.stop()
        recorder = null
        mic.text = "🎙"
    }

    override fun onDestroy() {
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
        findViewById<View>(R.id.screen).visibility = View.GONE
        findViewById<View>(R.id.files).visibility = View.GONE
        tabs.visibility = View.GONE
        status.text = "● SIN VINCULAR"
        status.setTextColor(getColor(R.color.muted))
    }

    private fun showChat() {
        setup.visibility = View.GONE
        scroll.visibility = View.VISIBLE
        composer.visibility = View.VISIBLE
        findViewById<View>(R.id.screen).visibility = View.VISIBLE
        findViewById<View>(R.id.files).visibility = View.VISIBLE
        tabs.visibility = View.VISIBLE
    }

    private fun openScreen() {
        if (api() != null) startActivity(Intent(this, ScreenActivity::class.java))
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
        JarvisService.stop(this)
        Speaker.stop()
        showSetup(message)
    }

    // --- Escucha de eventos -------------------------------------------------------

    private fun startPolling() {
        val client = api() ?: return
        val mine = ++generation
        messages.removeAllViews()
        liveBubble = null
        liveText = null
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
            val ts = event.optLong("ts", 0L)
            when (event.optString("type")) {
                "user" -> addBubble(text, true, ts)
                "live" -> showLive(text, ts)
                "assistant" -> {
                    showLive("")
                    addBubble(text, false, ts)
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

    /** Respuesta que Jarvis está escribiendo: una burbuja que se actualiza; vacía = quitarla. */
    private fun showLive(text: String, ts: Long = 0L) {
        if (text.isEmpty()) {
            liveBubble?.let { messages.removeView(it) }
            liveBubble = null
            liveText = null
            return
        }
        val view = liveText
        if (view != null) {
            view.text = text
            return
        }
        liveBubble = addBubble(text, false, ts)
        liveText = liveBubble?.getChildAt(1) as? TextView
    }

    /** «12/11 14:22» (con el año si no es el actual) a partir de los segundos Unix del evento. */
    private fun messageTime(ts: Long): String {
        val moment = if (ts > 0) java.util.Date(ts * 1000) else java.util.Date()
        val year = java.text.SimpleDateFormat("yyyy", java.util.Locale.ROOT)
        val pattern = if (year.format(moment) == year.format(java.util.Date())) "dd/MM HH:mm" else "dd/MM/yyyy HH:mm"
        return java.text.SimpleDateFormat(pattern, java.util.Locale.ROOT).format(moment)
    }

    private fun addBubble(text: String, user: Boolean, ts: Long = 0L): LinearLayout {
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(getColor(if (user) R.color.cyan_dark else R.color.panel_light))
            setPadding(dp(14), dp(9), dp(14), dp(10))
        }
        // Cabecera «TÚ   12/11 14:22»: queda como hijo 0 (showLive lee el texto en el hijo 1).
        val header = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        header.addView(label(if (user) "TÚ" else "JARVIS", getColor(if (user) R.color.cyan else R.color.violet)))
        header.addView(label(messageTime(ts), getColor(R.color.muted)).apply {
            typeface = Typeface.MONOSPACE
            setPadding(dp(10), 0, 0, 0)
        })
        bubble.addView(header)
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
        return bubble
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
            if (approve && event.optBoolean("high")) {  // riesgo alto: primero la huella
                Lock.authenticate(this, "Confirmar acción", event.optString("text").take(120),
                    onSuccess = { send(qid, true, buttons, result) })
                return
            }
            send(qid, approve, buttons, result)
        }

        val yes = Button(this).apply {
            text = (if (event.optBoolean("high")) "🔒 " else "") + event.optString("yes", "SÍ")
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
        finishQuestion(event, pending, qid, card, buttons, result, yes, no)
    }

    private fun send(qid: String, approve: Boolean, buttons: View, result: TextView) {
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

    private fun finishQuestion(
        event: JSONObject, pending: String?, qid: String, card: LinearLayout,
        buttons: LinearLayout, result: TextView, yes: Button, no: Button,
    ) {
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

    /** Con la voz de Piper de la PC (o la del teléfono si la PC no puede). */
    private fun speak(text: String) {
        if (prefs.getBoolean("leer", false)) api()?.let { Speaker.say(this, it, text) }
    }
}
