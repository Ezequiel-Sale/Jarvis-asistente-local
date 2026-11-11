package com.jarvis.movil

import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.WindowManager
import android.widget.GridLayout
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Pantalla de juego: el celular en horizontal como segunda pantalla, con los FPS del
 * juego en grande (PresentMon en la PC) y temperaturas y cargas. Pide /api/status?fps=1
 * cada segundo; PresentMon solo corre mientras esta pantalla está abierta.
 */
class GameActivity : JarvisActivity() {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var fps: TextView
    private lateinit var frame: TextView
    private lateinit var tiles: Map<String, TextView>

    @Volatile
    private var generation = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(Color.BLACK)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            gravity = Gravity.CENTER_VERTICAL
            setOnClickListener { finish() }
        }
        val left = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER }
        fps = TextView(this).apply {
            text = "—"
            textSize = 96f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            setTextColor(getColor(R.color.cyan))
            gravity = Gravity.CENTER
        }
        left.addView(monoLabel("FPS", getColor(R.color.muted), 14f).apply { gravity = Gravity.CENTER })
        left.addView(fps)
        frame = monoLabel("Midiendo…", getColor(R.color.muted), 13f).apply { gravity = Gravity.CENTER }
        left.addView(frame)
        root.addView(left, LinearLayout.LayoutParams(0, -1, 1f))

        val grid = GridLayout(this).apply { columnCount = 2 }
        val names = listOf("gpu" to "GPU", "gpuHot" to "PUNTO CALIENTE", "gpuLoad" to "CARGA GPU",
            "cpuLoad" to "CARGA CPU", "cpu" to "CPU", "ram" to "RAM")
        val built = HashMap<String, TextView>()
        for ((key, name) in names) {
            val box = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = rounded(getColor(R.color.panel), getColor(R.color.border))
                setPadding(dp(12), dp(8), dp(12), dp(8))
            }
            box.addView(monoLabel(name, getColor(R.color.muted), 10f))
            val value = monoLabel("…", getColor(R.color.text), 26f)
            box.addView(value)
            built[key] = value
            grid.addView(box, GridLayout.LayoutParams().apply {
                width = dp(150)
                setMargins(dp(4), dp(4), dp(4), dp(4))
            })
        }
        tiles = built
        root.addView(grid)
        setContentView(root)
    }

    override fun onStart() {
        super.onStart()
        val client = jarvisApi() ?: return finish()
        val mine = ++generation
        thread(name = "juego") {
            while (mine == generation) {
                try {
                    val data = client.getJson("/api/status?fps=1")
                    ui.post { if (mine == generation) render(data) }
                } catch (e: Exception) {
                    ui.post { frame.text = "Sin conexión con la PC" }
                }
                Thread.sleep(1000)
            }
        }
    }

    override fun onStop() {
        super.onStop()
        generation++
    }

    private fun render(data: JSONObject) {
        val limits = data.optJSONObject("limits") ?: JSONObject()
        val measured = data.optJSONObject("fps")
        if (measured == null) {
            fps.text = "—"
            fps.setTextColor(getColor(R.color.muted))
            frame.text = "Esperando cuadros de la ventana activa…"
        } else {
            val value = measured.optInt("fps")
            fps.text = value.toString()
            fps.setTextColor(when {
                value >= 60 -> getColor(R.color.cyan)
                value >= 30 -> Color.rgb(0xFF, 0xC8, 0x57)
                else -> getColor(R.color.error)
            })
            frame.text = "${measured.optString("app")} · ${measured.optDouble("frameMs")} ms por cuadro"
        }
        for ((key, view) in tiles) {
            val number = if (key == "ram") data.optJSONObject("ram")?.optDouble("pct")
            else if (data.isNull(key)) null else data.optDouble(key)
            val unit = if (key in setOf("gpu", "gpuHot", "cpu")) "°" else "%"
            val limit = if (limits.has(key)) limits.optDouble(key) else null
            view.text = number?.let { "%.0f%s".format(it, unit) } ?: "—"
            view.setTextColor(getColor(if (number != null && limit != null && number >= limit) R.color.error else R.color.text))
        }
    }
}
