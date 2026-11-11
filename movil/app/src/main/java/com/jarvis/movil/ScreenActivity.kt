package com.jarvis.movil

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.graphics.SurfaceTexture
import android.view.Surface
import android.view.TextureView
import android.view.Gravity
import android.view.View
import android.content.Intent
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
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread

/**
 * Pantallas de la PC en vivo (solo ver) y una caja para darle órdenes a Jarvis sobre
 * lo que se ve. Pide capturas JPEG seguidas a /api/screen; no es vídeo, son unos
 * cuadros por segundo. La respuesta de Jarvis aparece debajo de la imagen.
 *
 * Calidad: AUTO (60 FPS con Wi-Fi, AHORRO con datos móviles), 60 FPS y 30 FPS
 * (vídeo H.264 de /api/video, decodificado por hardware), FLUIDO (imágenes, el
 * siguiente cuadro apenas llega el anterior), HD (~4 por segundo) o AHORRO (~2).
 * «TODOS» y los celulares o PCs donde el vídeo falla usan FLUIDO.
 *
 * Arriba: 🔍 «¿Qué es esto?» (marcar una zona y preguntar), 🖱 touchpad (pide el
 * control a la PC: riesgo alto, se aprueba con huella) y 🔊 audio de la PC (solo con
 * vídeo). El puntero de la PC se dibuja encima (OverlayView).
 */
class ScreenActivity : Activity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var screenRoot: FrameLayout
    private lateinit var image: ZoomImageView
    private lateinit var video: ZoomTextureView

    @Volatile
    private var videoSurface: Surface? = null

    @Volatile
    private var videoFailed = false
    private lateinit var topBar: LinearLayout
    private lateinit var monitors: LinearLayout
    private lateinit var quality: TextView
    private lateinit var info: TextView
    private lateinit var bottom: LinearLayout
    private lateinit var reply: TextView
    private lateinit var input: EditText
    private lateinit var mic: Button
    private lateinit var overlay: OverlayView
    private lateinit var selectChip: TextView
    private lateinit var padChip: TextView
    private lateinit var audioChip: TextView
    private lateinit var keys: LinearLayout
    private var sender: TouchpadSender? = null
    private var touchpadOn = false

    @Volatile
    private var audioOn = false

    private val ui = Handler(Looper.getMainLooper())
    private var recorder: Recorder? = null
    private var question: AlertDialog? = null
    private var questionId: String? = null

    @Volatile
    private var generation = 0

    @Volatile
    private var monitor = 0

    /** fps > 0: vídeo; si no, imágenes JPEG con esa calidad y pausa. */
    private class Quality(val name: String, val width: Int, val jpeg: Int, val intervalMs: Long, val fps: Int = 0)

    private val video60 = Quality("60 FPS", 1920, 0, 0, fps = 60)
    private val video30 = Quality("30 FPS", 1920, 0, 0, fps = 30)
    private val fluid = Quality("FLUIDO", 1600, 55, 0)
    private val hd = Quality("HD", 1600, 60, 250)
    private val economy = Quality("AHORRO", 960, 40, 500)
    private val modes = listOf("AUTO", "60 FPS", "30 FPS", "FLUIDO", "HD", "AHORRO")

    @Volatile
    private var mode = "AUTO"

    @Volatile
    private var onWifi = false

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
        mode = prefs.getString("pantalla_calidad", "AUTO")?.takeIf { it in modes } ?: "AUTO"
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        // El vídeo queda siempre debajo (una TextureView oculta no crea su superficie);
        // en modo vídeo se quita la imagen de encima.
        video = ZoomTextureView(this)
        video.zoom.onTap = { toggleBars() }
        video.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
                videoSurface = Surface(texture)
            }

            override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) {}

            override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
                videoSurface = null
                return true
            }

            override fun onSurfaceTextureUpdated(texture: SurfaceTexture) {}
        }
        root.addView(video, FrameLayout.LayoutParams(-1, -1))
        image = ZoomImageView(this)
        image.zoom.onTap = { toggleBars() }
        root.addView(image, FrameLayout.LayoutParams(-1, -1))
        overlay = OverlayView(this).apply { zoom = image.zoom }
        image.zoom.onChange = { overlay.invalidate() }
        video.zoom.onChange = { overlay.invalidate() }
        overlay.onSelected = { x, y, w, h -> askAboutZone(x, y, w, h) }
        root.addView(overlay, FrameLayout.LayoutParams(-1, -1))

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
        selectChip = chip("🔍", R.color.text) { toggleSelect() }
        topBar.addView(selectChip)
        padChip = chip("🖱", R.color.text) { toggleTouchpad() }
        topBar.addView(padChip)
        audioOn = prefs.getBoolean("pantalla_audio", false)
        audioChip = chip("", R.color.text) { toggleAudio() }
        topBar.addView(audioChip)
        refreshAudioChip()
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
        keys = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            visibility = View.GONE
            setPadding(0, 0, 0, dp(6))
        }
        for ((label, action) in listOf("⌨" to "type", "⏎" to "enter", "⌫" to "backspace", "Esc" to "esc",
            "Tab" to "tab", "Alt+Tab" to "alttab", "⊞" to "win")) {
            keys.addView(chip(label, R.color.cyan) {
                if (action == "type") typeOnPc() else sender?.key(action)
            }.apply { setBackgroundColor(getColor(R.color.panel_light)) },
                LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(3) })
        }
        bottom.addView(keys)
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
        screenRoot = root
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

    // --- Puntero, «¿Qué es esto?», touchpad y audio -------------------------------------

    private fun parseCursor(header: String?): Pair<Float, Float>? {
        val parts = header?.split(",") ?: return null
        if (parts.size != 2) return null
        val x = parts[0].toFloatOrNull() ?: return null
        val y = parts[1].toFloatOrNull() ?: return null
        return x to y
    }

    private fun toggleSelect() {
        if (overlay.mode == OverlayView.Mode.SELECT) {
            overlay.mode = if (touchpadOn) OverlayView.Mode.TOUCHPAD else OverlayView.Mode.NONE
            selectChip.setBackgroundColor(Color.TRANSPARENT)
            return
        }
        overlay.mode = OverlayView.Mode.SELECT
        selectChip.setBackgroundColor(getColor(R.color.cyan_dark))
        showReply("Marca con el dedo la zona sobre la que quieres preguntar.")
    }

    private fun askAboutZone(x: Float, y: Float, w: Float, h: Float) {
        val field = EditText(this).apply {
            setText("¿Qué es esto?")
            selectAll()
        }
        AlertDialog.Builder(this)
            .setTitle("Preguntar sobre la zona")
            .setView(field)
            .setPositiveButton("Preguntar") { _, _ ->
                val client = api() ?: return@setPositiveButton
                val body = JSONObject().put("n", monitor).put("x", x).put("y", y).put("w", w).put("h", h)
                    .put("question", field.text.toString())
                showReply("Analizando la zona… (puede tardar un poco)")
                thread {
                    try {
                        client.postJson("/api/vision", body)
                    } catch (e: Exception) {
                        ui.post { toast(e.message ?: "No pude enviar la zona.") }
                    }
                }
            }
            .setNegativeButton("Cancelar", null)
            .setOnDismissListener { if (overlay.mode == OverlayView.Mode.SELECT) toggleSelect() }
            .show()
    }

    /** Pide el control a la PC; se activa cuando llega el evento «control» (tras aprobarlo). */
    private fun toggleTouchpad() {
        val client = api() ?: return
        val on = !touchpadOn
        if (!on) setTouchpad(false)
        thread {
            try {
                val reply = client.postJson("/api/control", JSONObject().put("on", on))
                ui.post {
                    when {
                        reply.optBoolean("active") -> setTouchpad(true)
                        reply.optBoolean("pending") ->
                            showReply("Esperando que apruebes el control (en la PC o aquí con tu huella)…")
                    }
                }
            } catch (e: Exception) {
                ui.post { toast(e.message ?: "No pude pedir el control.") }
            }
        }
    }

    private fun setTouchpad(on: Boolean) {
        if (on == touchpadOn) return
        touchpadOn = on
        if (on) {
            val client = api() ?: return
            sender = TouchpadSender(client) { message ->
                ui.post { if (touchpadOn) info.text = message }
            }
            overlay.touchpad = sender  // sin esto los gestos no llegaban a ningún lado
            overlay.mode = OverlayView.Mode.TOUCHPAD
            showReply("Touchpad activo: un dedo mueve, toque = clic, dos dedos = clic derecho o desplazar, " +
                "mantener = arrastrar.")
        } else {
            sender?.stop()
            sender = null
            overlay.touchpad = null
            if (overlay.mode == OverlayView.Mode.TOUCHPAD) overlay.mode = OverlayView.Mode.NONE
        }
        padChip.setBackgroundColor(if (on) getColor(R.color.error) else Color.TRANSPARENT)
        keys.visibility = if (on) View.VISIBLE else View.GONE
    }

    private fun typeOnPc() {
        val field = EditText(this).apply { hint = "Texto para escribir en la PC" }
        AlertDialog.Builder(this)
            .setTitle("Escribir en la PC")
            .setView(field)
            .setPositiveButton("Escribir") { _, _ -> sender?.text(field.text.toString()) }
            .setNeutralButton("Escribir y ⏎") { _, _ ->
                sender?.text(field.text.toString())
                sender?.key("enter")
            }
            .setNegativeButton("Cancelar", null)
            .show()
    }

    private fun toggleAudio() {
        audioOn = !audioOn
        prefs.edit().putBoolean("pantalla_audio", audioOn).apply()
        refreshAudioChip()
        if (audioOn && effective().fps == 0) toast("El audio de la PC llega solo en los modos de vídeo (60 o 30 FPS).")
    }

    private fun refreshAudioChip() {
        audioChip.text = if (audioOn) "🔊" else "🔇"
    }

    private fun toggleQuality() {
        mode = modes[(modes.indexOf(mode) + 1) % modes.size]
        prefs.edit().putString("pantalla_calidad", mode).apply()
        refreshQuality()
    }

    private fun current(): Quality = when (mode) {
        "60 FPS" -> video60
        "30 FPS" -> video30
        "FLUIDO" -> fluid
        "HD" -> hd
        "AHORRO" -> economy
        else -> if (onWifi) video60 else economy
    }

    /** Lo que de verdad se usa: el vídeo es de un monitor a la vez y puede fallar. */
    private fun effective(): Quality {
        val wanted = current()
        return if (wanted.fps > 0 && (monitor == 0 || videoFailed)) fluid else wanted
    }

    private fun refreshQuality() {
        val used = effective().name
        quality.text = when {
            mode == "AUTO" -> "AUTO · $used"
            used != mode -> "$mode → $used"
            else -> mode
        }
    }

    /**
     * Wi-Fi o cable: alguna red (que no sea la VPN de Tailscale) con internet.
     * Con Tailscale activo la red «activa» es la VPN, por eso se miran todas.
     */
    @Suppress("DEPRECATION")
    private fun detectWifi(): Boolean {
        val manager = getSystemService(ConnectivityManager::class.java) ?: return false
        return manager.allNetworks.any { network ->
            val caps = manager.getNetworkCapabilities(network) ?: return@any false
            !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                    caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
        }
    }

    private fun showMonitors(list: List<Pair<Int, String>>) {
        monitors.removeAllViews()
        for ((number, name) in list) {
            val selected = number == monitor
            monitors.addView(chip(name, if (selected) R.color.cyan else R.color.muted) {
                monitor = number
                prefs.edit().putInt("pantalla", number).apply()
                showMonitors(list)
                refreshQuality()
            }.apply { if (selected) setBackgroundColor(getColor(R.color.cyan_dark)) })
        }
    }

    // --- Ciclo de vida ------------------------------------------------------------------

    override fun onStart() {
        super.onStart()
        Lock.onStart(this, screenRoot)
        val client = api() ?: return finish()
        val mine = ++generation
        info.text = "Conectando…"
        thread(name = "pantallas") { loadMonitors(client, mine) }
        thread(name = "eventos-pantalla") { pollEvents(client, mine) }
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
        if (touchpadOn) {  // al salir de la vista, la PC deja de estar controlada
            val client = api()
            setTouchpad(false)
            thread {
                try {
                    client?.postJson("/api/control", JSONObject().put("on", false))
                } catch (e: Exception) {
                }
            }
        }
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
            // En FLUIDO un segundo pedido viaja por la red mientras la PC captura el siguiente.
            thread(name = "pantallas-2") { frames(client, mine, helper = true) }
            frames(client, mine, helper = false)
        } catch (e: ApiException) {
            ui.post { if (e.code == 401) finish() else info.text = e.message }
        } catch (e: Exception) {
            ui.post { info.text = "No pude conectar con la PC." }
        }
    }

    private val requested = AtomicLong()
    private var lastShown = 0L  // solo en el hilo de la interfaz
    private val stats = Any()
    private var statFrames = 0
    private var statKb = 0
    private var statSince = SystemClock.elapsedRealtime()

    /** Cuadros/s y KB/s cada 2 s (o null si aún no toca mostrarlo). */
    private fun countFrame(bytes: Int): String? = synchronized(stats) {
        statFrames++
        statKb += bytes / 1024
        val now = SystemClock.elapsedRealtime()
        if (now - statSince < 2000) return null
        val seconds = (now - statSince) / 1000.0
        val line = String.format("%.1f cuadros/s · %d KB/s", statFrames / seconds, (statKb / seconds).toInt())
        statFrames = 0
        statKb = 0
        statSince = now
        line
    }

    /**
     * Pide cuadros seguidos, al ritmo de la calidad elegida (y de la red, en AUTO).
     * El ayudante solo trabaja en FLUIDO; si un cuadro llega después de uno más
     * nuevo, se descarta.
     */
    private fun frames(client: Api, mine: Int, helper: Boolean) {
        var checked = 0L
        while (mine == generation) {
            val start = SystemClock.elapsedRealtime()
            if (!helper && start - checked > 5000) {  // la red puede cambiar mientras se mira
                checked = start
                val wifi = detectWifi()
                if (wifi != onWifi) {
                    onWifi = wifi
                    ui.post { refreshQuality() }
                }
            }
            val quality = effective()
            if (helper && (quality.intervalMs > 0 || quality.fps > 0)) {
                Thread.sleep(300)
                continue
            }
            if (quality.fps > 0) {
                playVideo(client, mine, quality)
                continue
            }
            if (image.visibility != View.VISIBLE) ui.post {
                image.visibility = View.VISIBLE
                overlay.zoom = image.zoom
            }
            val wide = if (monitor == 0) 1.6 else 1.0
            val width = (quality.width * wide).toInt()
            val seq = requested.incrementAndGet()
            try {
                val (bytes, cursorHeader) = client.getFrame("/api/screen?n=$monitor&w=$width&q=${quality.jpeg}")
                val pointer = parseCursor(cursorHeader)
                val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                val text = countFrame(bytes.size)
                ui.post {
                    if (mine == generation && bitmap != null && seq > lastShown) {
                        lastShown = seq
                        image.setFrame(bitmap)
                        overlay.cursor = pointer
                    }
                    if (mine == generation && text != null) info.text = text
                }
            } catch (e: ApiException) {
                if (e.code == 401) {
                    ui.post { finish() }
                    return
                }
                if (!helper) ui.post { if (mine == generation) info.text = e.message }
                Thread.sleep(2000)
            } catch (e: Exception) {
                if (!helper) ui.post { if (mine == generation) info.text = "Sin conexión con la PC; reintentando…" }
                Thread.sleep(2000)
            }
            val pause = quality.intervalMs - (SystemClock.elapsedRealtime() - start)
            if (pause > 0) Thread.sleep(pause)
        }
    }

    /** Vídeo hasta que cambie el monitor o la calidad, se cierre la vista o falle. */
    private fun playVideo(client: Api, mine: Int, quality: Quality) {
        val surface = videoSurface
        if (surface == null) {
            Thread.sleep(100)
            return
        }
        val wantedMonitor = monitor
        val wantedAudio = audioOn
        ui.post {
            if (mine == generation) {
                image.visibility = View.GONE
                overlay.zoom = video.zoom
            }
        }
        val player = VideoPlayer(
            client, surface,
            running = { mine == generation && monitor == wantedMonitor && effective() === quality &&
                videoSurface === surface && audioOn == wantedAudio },
            onSize = { w, h -> ui.post { video.zoom.setContentSize(w, h) } },
            onFrame = { bytes ->
                val text = countFrame(bytes)
                if (text != null) ui.post { if (mine == generation) info.text = text }
            },
            onCursor = { pointer -> ui.post { overlay.cursor = pointer } },
        )
        val kbps = if (quality.fps >= 60) 10000 else 8000
        try {
            val sound = if (wantedAudio) "&audio=1" else ""
            player.play("/api/video?n=$wantedMonitor&fps=${quality.fps}&w=${quality.width}&kbps=$kbps$sound",
                quality.width, quality.width * 9 / 16)
        } catch (e: ApiException) {
            when (e.code) {
                401 -> ui.post { finish() }
                400, 404, 501 -> {
                    videoFailed = true
                    ui.post { info.text = "Vídeo no disponible (${e.message}); uso imágenes."; refreshQuality() }
                }
                else -> {
                    ui.post { if (mine == generation) info.text = e.message }
                    Thread.sleep(2000)
                }
            }
        } catch (e: java.io.IOException) {
            if (mine == generation) {
                ui.post { info.text = "Se cortó el vídeo; reconectando…" }
                Thread.sleep(1000)
            }
        } catch (e: Exception) {  // el decodificador del teléfono no pudo
            if (mine == generation && videoSurface === surface) {
                videoFailed = true
                ui.post { info.text = "Este celular no pudo reproducir el vídeo; uso imágenes."; refreshQuality() }
            }
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
                            "control" -> setTouchpad(event.optBoolean("active"))
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
            .setPositiveButton(event.optString("yes", "SÍ")) { _, _ ->
                if (event.optBoolean("high")) {
                    Lock.authenticate(this, "Confirmar acción", event.optString("text").take(120),
                        onSuccess = { answer(true) })
                } else answer(true)
            }
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
