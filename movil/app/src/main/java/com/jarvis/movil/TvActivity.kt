package com.jarvis.movil

import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Pestaña «Tele»: control remoto del televisor (Google TV) a través de Jarvis. Cada botón
 * es POST /api/tv {action}; la PC lo ejecuta con las herramientas tv_control / tv_open_app.
 * El estado (encendida, app, volumen) se pide cada 3 s mientras la pestaña está a la vista.
 */
class TvActivity : JarvisActivity() {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var name: TextView
    private lateinit var pill: TextView
    private lateinit var detail: TextView
    private lateinit var apps: LinearLayout

    @Volatile
    private var generation = 0
    private var appsKey = ""

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
        root.addView(Tabs.bar(this, "tv"))
        setContentView(root)

        list.addView(statusCard(), LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })

        val remote = panel(list, "CONTROL")
        row(remote, dp(52),
            key("Prender", "power_on", R.color.cyan_dark, R.color.cyan, 15f),
            key("Apagar", "power_off", R.color.error_dark, R.color.error, 15f))
        remote.addView(dpad(), LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(14); bottomMargin = dp(14) })
        row(remote, dp(50),
            key("↩  Atrás", "back"), key("⌂  Inicio", "home"), key("⚙  Panel", "settings"))
        labeledRow(remote, "VOLUMEN",
            key("−", "volume_down", size = 22f), key("🔇", "mute", size = 18f), key("+", "volume_up", size = 22f))
        labeledRow(remote, "MEDIOS",
            key("◀◀", "rewind"), key("▶ ❚❚", "play_pause"), key("▶▶", "forward"))

        val inputs = panel(list, "ENTRADAS")
        row(inputs, dp(46), *(1..4).map { key("HDMI $it", "hdmi$it", fg = R.color.cyan, size = 14f) }.toTypedArray())

        apps = panel(list, "APPS")
    }

    // --- Vista ---------------------------------------------------------------------------

    private fun header(): View = LinearLayout(this).apply {
        setBackgroundColor(getColor(R.color.panel))
        setPadding(dp(16), dp(12), dp(16), dp(12))
        addView(TextView(this@TvActivity).apply {
            text = "📺  Tele"
            setTextColor(getColor(R.color.cyan))
            textSize = 19f
        })
    }

    /** Tarjeta de estado: nombre de la tele, píldora (encendida/apagada) y qué está mostrando. */
    private fun statusCard(): View = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = rounded(getColor(R.color.panel), getColor(R.color.border))
        setPadding(dp(16), dp(14), dp(16), dp(14))
        val top = LinearLayout(this@TvActivity).apply { gravity = Gravity.CENTER_VERTICAL }
        name = TextView(this@TvActivity).apply {
            text = "La tele"
            setTextColor(getColor(R.color.text))
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
        }
        pill = TextView(this@TvActivity).apply {
            textSize = 11f
            typeface = Typeface.DEFAULT_BOLD
            setPadding(dp(10), dp(3), dp(10), dp(3))
        }
        top.addView(name, LinearLayout.LayoutParams(0, -2, 1f))
        top.addView(pill)
        addView(top)
        detail = TextView(this@TvActivity).apply {
            setTextColor(getColor(R.color.muted))
            textSize = 14f
            setPadding(0, dp(4), 0, 0)
            text = "Conectando con la tele…"
        }
        addView(detail)
        paintPill("…", R.color.panel_light, R.color.muted)
    }

    private fun paintPill(text: String, bg: Int, fg: Int) {
        pill.text = text
        pill.setTextColor(getColor(fg))
        pill.background = GradientDrawable().apply {
            setColor(getColor(bg))
            cornerRadius = dp(20).toFloat()
        }
    }

    private fun panel(parent: LinearLayout, title: String): LinearLayout {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(getColor(R.color.panel), getColor(R.color.border))
            setPadding(dp(14), dp(12), dp(14), dp(14))
        }
        box.addView(monoLabel(title, getColor(R.color.muted)).apply { setPadding(dp(2), 0, 0, dp(10)) })
        parent.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
        return box
    }

    /** Un botón del control: fondo redondeado con onda al tocar; ``oval`` para el OK. */
    private fun key(label: String, action: String, bg: Int = R.color.panel_light, fg: Int = R.color.text,
                    size: Float = 15f, oval: Boolean = false): TextView = TextView(this).apply {
        text = label
        gravity = Gravity.CENTER
        textSize = size
        setTextColor(getColor(fg))
        if (oval) typeface = Typeface.DEFAULT_BOLD
        val fill = GradientDrawable().apply {
            setColor(getColor(bg))
            if (oval) setShape(GradientDrawable.OVAL) else cornerRadius = dp(12).toFloat()
        }
        background = RippleDrawable(ColorStateList.valueOf(getColor(R.color.hover)), fill, null)
        isClickable = true
        isFocusable = true
        setOnClickListener {
            it.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
            send(action)
        }
    }

    /** Fila de botones del mismo ancho. */
    private fun row(parent: LinearLayout, height: Int, vararg keys: View) {
        val line = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for (k in keys) line.addView(k, LinearLayout.LayoutParams(0, height, 1f).apply {
            marginStart = dp(4); marginEnd = dp(4)
        })
        parent.addView(line, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
    }

    /** Título corto a la izquierda y tres botones (volumen, reproducción). */
    private fun labeledRow(parent: LinearLayout, title: String, vararg keys: View) {
        val line = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        line.addView(monoLabel(title, getColor(R.color.muted)).apply { setPadding(dp(4), 0, 0, 0) },
            LinearLayout.LayoutParams(dp(78), -2))
        for (k in keys) line.addView(k, LinearLayout.LayoutParams(0, dp(48), 1f).apply {
            marginStart = dp(4); marginEnd = dp(4)
        })
        parent.addView(line, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
    }

    /** Cruz de dirección con el OK redondo en el centro. */
    private fun dpad(): View {
        val size = dp(68)
        val grid = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL }
        fun line(vararg cells: View?) {
            val l = LinearLayout(this).apply { gravity = Gravity.CENTER }
            for (c in cells) l.addView(c ?: View(this), LinearLayout.LayoutParams(size, size).apply {
                setMargins(dp(4), dp(4), dp(4), dp(4))
            })
            grid.addView(l)
        }
        line(null, key("▲", "up", size = 20f), null)
        line(key("◀", "left", size = 20f), key("OK", "ok", R.color.cyan, R.color.bg, 18f, oval = true),
            key("▶", "right", size = 20f))
        line(null, key("▼", "down", size = 20f), null)
        return grid
    }

    // --- Datos -----------------------------------------------------------------------------

    override fun onStart() {
        super.onStart()
        val client = jarvisApi() ?: return finish()
        val mine = ++generation
        thread(name = "tele") {
            while (mine == generation) {
                try {
                    val data = client.getJson("/api/tv")
                    ui.post { if (mine == generation) render(data) }
                } catch (e: ApiException) {
                    ui.post { showProblem("Sin tele", e.message ?: "La tele no está disponible.") }
                } catch (e: Exception) {
                    ui.post { showProblem("Sin PC", "Sin conexión con la PC; reintentando…") }
                }
                Thread.sleep(3000)
            }
        }
    }

    override fun onStop() {
        super.onStop()
        generation++
    }

    private fun showProblem(state: String, text: String) {
        paintPill(state.uppercase(), R.color.panel_light, R.color.error)
        detail.text = text
    }

    private fun render(data: JSONObject) {
        name.text = data.optString("name").ifEmpty { "La tele" }
        when {
            !data.optBoolean("paired") ->
                showProblem("Sin vincular", "Vincúlala en Jarvis: ⚙ Ajustes → Televisor.")
            !data.optBoolean("connected") ->
                showProblem("Sin conexión", "Puede estar apagada del todo o sin Wi-Fi.")
            data.has("on") && !data.isNull("on") && !data.optBoolean("on") -> {
                paintPill("APAGADA", R.color.panel_light, R.color.muted)
                detail.text = "En reposo: «Prender» la enciende."
            }
            else -> {
                paintPill("ENCENDIDA", R.color.cyan_dark, R.color.cyan)
                val parts = mutableListOf<String>()
                data.optString("appName").takeIf { it.isNotEmpty() }?.let { parts.add(it) }
                if (data.has("volume") && !data.isNull("volume")) {
                    parts.add("Volumen ${data.optInt("volume")}" + if (data.optBoolean("muted")) " · silenciada" else "")
                }
                detail.text = parts.joinToString(" · ").ifEmpty { " " }
            }
        }
        val list = data.optJSONArray("apps") ?: return
        if (list.toString() == appsKey) return
        appsKey = list.toString()
        while (apps.childCount > 1) apps.removeViewAt(1)  // el 0 es el título
        var line: LinearLayout? = null
        for (i in 0 until list.length()) {
            if (i % 3 == 0) {
                line = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
                apps.addView(line, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
            }
            val app = list.getJSONObject(i)
            line!!.addView(key(app.optString("name"), "app:" + app.optString("id"), size = 14f),
                LinearLayout.LayoutParams(0, dp(52), 1f).apply { marginStart = dp(4); marginEnd = dp(4) })
        }
        val missing = 3 - list.length() % 3
        if (missing in 1..2) repeat(missing) {
            line?.addView(View(this), LinearLayout.LayoutParams(0, dp(52), 1f).apply { marginStart = dp(4); marginEnd = dp(4) })
        }
    }

    /** Un botón del control: la PC lo hace y devuelve el estado nuevo. */
    private fun send(action: String) {
        val client = jarvisApi() ?: return
        thread {
            try {
                val answer = client.postJson("/api/tv", JSONObject().put("action", action))
                answer.optJSONObject("tv")?.let { data -> ui.post { render(data) } }
            } catch (e: Exception) {
                ui.post { Toast.makeText(this, e.message ?: "No pude hablar con Jarvis.", Toast.LENGTH_LONG).show() }
            }
        }
    }
}
