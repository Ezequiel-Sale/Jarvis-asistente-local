package com.jarvis.movil

import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Pestaña «Música»: lo que suena (carátula, play/pausa, anterior, siguiente), volumen
 * general, salida de audio y volumen o silencio de cada aplicación. Cada acción usa
 * las mismas herramientas de Jarvis que en la PC (POST /api/media).
 */
class MusicActivity : JarvisActivity() {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var cover: ImageView
    private lateinit var title: TextView
    private lateinit var artist: TextView
    private lateinit var source: TextView
    private lateinit var playPause: Button
    private lateinit var systemVolume: SeekBar
    private lateinit var systemLabel: TextView
    private lateinit var outputs: LinearLayout
    private lateinit var apps: LinearLayout
    private lateinit var status: TextView

    @Volatile
    private var generation = 0
    private var dragging = false
    private var coverKey = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(getColor(R.color.bg))
            fitsSystemWindows = true
        }
        root.addView(header())
        val list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(16))
        }
        root.addView(ScrollView(this).apply { addView(list) }, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(Tabs.bar(this, "music"))
        setContentView(root)

        // Lo que suena.
        val card = panel(list, "SONANDO AHORA")
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        cover = ImageView(this).apply {
            scaleType = ImageView.ScaleType.CENTER_CROP
            background = rounded(getColor(R.color.panel_light))
            clipToOutline = true
        }
        row.addView(cover, LinearLayout.LayoutParams(dp(84), dp(84)))
        val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), 0, 0, 0) }
        title = TextView(this).apply { setTextColor(getColor(R.color.text)); textSize = 16f; maxLines = 2 }
        artist = TextView(this).apply { setTextColor(getColor(R.color.muted)); textSize = 13f; maxLines = 1 }
        source = monoLabel("", getColor(R.color.violet))
        texts.addView(title)
        texts.addView(artist)
        texts.addView(source)
        row.addView(texts, LinearLayout.LayoutParams(0, -2, 1f))
        card.addView(row)
        val controls = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, dp(10), 0, 0)
        }
        controls.addView(control("⏮") { media("media_control", JSONObject().put("action", "previous")) })
        playPause = control("⏯") { media("media_control", JSONObject().put("action", "toggle")) }
        controls.addView(playPause)
        controls.addView(control("⏭") { media("media_control", JSONObject().put("action", "next")) })
        card.addView(controls)

        // Volumen general y salida.
        val volume = panel(list, "VOLUMEN GENERAL")
        systemLabel = monoLabel("…", getColor(R.color.text), 12f)
        volume.addView(systemLabel)
        systemVolume = slider { value -> media("set_system_volume", JSONObject().put("percentage", value)) }
        volume.addView(systemVolume)
        outputs = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(8), 0, 0) }
        volume.addView(outputs)

        apps = panel(list, "APLICACIONES CON SONIDO")
        status = TextView(this).apply { setTextColor(getColor(R.color.muted)); textSize = 12f }
        list.addView(status)
    }

    private fun header(): View = LinearLayout(this).apply {
        setBackgroundColor(getColor(R.color.panel))
        setPadding(dp(16), dp(12), dp(16), dp(12))
        addView(TextView(this@MusicActivity).apply {
            text = "🎵  Música"
            setTextColor(getColor(R.color.cyan))
            textSize = 19f
        })
    }

    private fun panel(parent: LinearLayout, name: String): LinearLayout {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(getColor(R.color.panel), getColor(R.color.border))
            setPadding(dp(14), dp(12), dp(14), dp(14))
        }
        box.addView(monoLabel(name, getColor(R.color.muted)).apply { setPadding(0, 0, 0, dp(8)) })
        parent.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
        return box
    }

    private fun control(text: String, action: () -> Unit): Button = Button(this).apply {
        this.text = text
        textSize = 22f
        setTextColor(getColor(R.color.bg))
        backgroundTintList = getColorStateList(R.color.cyan)
        setOnClickListener { action() }
        layoutParams = LinearLayout.LayoutParams(dp(72), dp(56)).apply { marginStart = dp(6); marginEnd = dp(6) }
    }

    private fun slider(onChange: (Int) -> Unit): SeekBar = SeekBar(this).apply {
        max = 100
        setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar, value: Int, fromUser: Boolean) {}
            override fun onStartTrackingTouch(bar: SeekBar) { dragging = true }
            override fun onStopTrackingTouch(bar: SeekBar) {
                dragging = false
                onChange(bar.progress)
            }
        })
    }

    // --- Datos -----------------------------------------------------------------------------

    override fun onStart() {
        super.onStart()
        val client = jarvisApi() ?: return finish()
        val mine = ++generation
        thread(name = "musica") {
            while (mine == generation) {
                try {
                    val data = client.getJson("/api/media")
                    ui.post { if (mine == generation) render(data, client) }
                } catch (e: Exception) {
                    ui.post { status.text = "Sin conexión con la PC; reintentando…" }
                }
                Thread.sleep(2000)
            }
        }
    }

    override fun onStop() {
        super.onStop()
        generation++
    }

    private fun render(data: JSONObject, client: Api) {
        status.text = ""
        val playing = data.optJSONObject("playing") ?: JSONObject()
        if (playing.length() == 0) {
            title.text = "No suena nada"
            artist.text = ""
            source.text = ""
            cover.setImageDrawable(null)
            coverKey = ""
        } else {
            title.text = playing.optString("title").ifEmpty { "Sin título" }
            artist.text = playing.optString("artist")
            val state = playing.optString("state")
            source.text = "${playing.optString("app").uppercase()} · ${state.uppercase()}"
            playPause.text = if (state == "reproduciendo") "⏸" else "▶"
            val key = playing.optString("app") + "|" + playing.optString("title")
            if (key != coverKey) {
                coverKey = key
                cover.setImageDrawable(null)
                if (playing.optBoolean("cover")) loadCover(client, key)
            }
        }
        if (!dragging) {
            val volume = data.optInt("volume", -1)
            if (volume >= 0) {
                systemVolume.progress = volume
                systemLabel.text = "$volume %"
            }
            renderApps(data)
        }
        outputs.removeAllViews()
        val current = data.optString("output")
        val list = data.optJSONArray("outputs")
        for (i in 0 until (list?.length() ?: 0)) {
            val item = list!!.getJSONObject(i)
            val device = item.optString("device")
            val selected = device == current
            outputs.addView(Button(this).apply {
                text = (if (device == "headphones") "🎧 " else "🔊 ") + item.optString("name")
                setTextColor(getColor(if (selected) R.color.bg else R.color.text))
                backgroundTintList = getColorStateList(if (selected) R.color.cyan else R.color.panel_light)
                setOnClickListener { if (!selected) media("set_output_device", JSONObject().put("device", device)) }
            }, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(6) })
        }
    }

    private fun renderApps(data: JSONObject) {
        while (apps.childCount > 1) apps.removeViewAt(1)  // el 0 es el título
        val list = data.optJSONArray("apps")
        if (list == null || list.length() == 0) {
            apps.addView(TextView(this).apply {
                text = "Ninguna aplicación está usando el sonido."
                setTextColor(getColor(R.color.muted))
            })
            return
        }
        for (i in 0 until list.length()) {
            val app = list.getJSONObject(i)
            val key = app.optString("app")
            val muted = app.optBoolean("muted")
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(4), 0, dp(4))
            }
            row.addView(TextView(this).apply {
                text = app.optString("name")
                setTextColor(getColor(R.color.text))
                textSize = 14f
            }, LinearLayout.LayoutParams(dp(96), -2))
            row.addView(slider { value -> media("set_app_volume", JSONObject().put("app", key).put("percentage", value)) }
                .apply { progress = app.optInt("volume") }, LinearLayout.LayoutParams(0, -2, 1f))
            row.addView(TextView(this).apply {
                text = if (muted) "🔇" else "🔊"
                textSize = 20f
                setPadding(dp(10), dp(4), dp(4), dp(4))
                setOnClickListener { media("set_app_volume", JSONObject().put("app", key).put("mute", !muted)) }
            })
            apps.addView(row)
        }
    }

    private fun loadCover(client: Api, key: String) {
        thread {
            try {
                val bytes = client.getBytes("/api/media/cover")
                val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                ui.post { if (key == coverKey && bitmap != null) cover.setImageBitmap(bitmap) }
            } catch (e: Exception) {
                // sin carátula
            }
        }
    }

    /** Ejecuta una herramienta de música en la PC y avisa si falló. */
    private fun media(tool: String, args: JSONObject) {
        val client = jarvisApi() ?: return
        thread {
            try {
                client.postJson("/api/media", JSONObject().put("tool", tool).put("args", args))
            } catch (e: Exception) {
                ui.post { Toast.makeText(this, e.message ?: "No pude hablar con Jarvis.", Toast.LENGTH_LONG).show() }
            }
        }
    }
}
