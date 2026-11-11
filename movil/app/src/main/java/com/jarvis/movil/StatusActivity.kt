package com.jarvis.movil

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import kotlin.concurrent.thread

/** Pestaña «Equipo»: temperaturas, cargas, RAM y discos, con gráficos de ~10 minutos. */
class StatusActivity : JarvisActivity() {

    private class Metric(val key: String, val name: String, val unit: String, val max: Float, val limitKey: String?) {
        lateinit var value: TextView
        lateinit var chart: ChartView
    }

    private val metrics = listOf(
        Metric("gpu", "GPU", "°C", 110f, "gpu"),
        Metric("gpuHot", "GPU punto caliente", "°C", 110f, "gpuHot"),
        Metric("gpuLoad", "Carga GPU", "%", 100f, null),
        Metric("cpu", "CPU", "°C", 110f, "cpu"),
        Metric("cpuLoad", "Carga CPU", "%", 100f, null),
        Metric("ram", "Memoria RAM", "%", 100f, null),
    )
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var title: TextView
    private lateinit var disks: LinearLayout
    private lateinit var note: TextView

    @Volatile
    private var generation = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(getColor(R.color.bg))
            fitsSystemWindows = true
        }
        root.addView(LinearLayout(this).apply {
            setBackgroundColor(getColor(R.color.panel))
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(8), dp(8), dp(8))
            title = TextView(this@StatusActivity).apply {
                text = "📊  Equipo"
                setTextColor(getColor(R.color.cyan))
                textSize = 19f
            }
            addView(title, LinearLayout.LayoutParams(0, -2, 1f))
            addView(Button(this@StatusActivity).apply {
                text = "🎮 JUEGO"
                setTextColor(getColor(R.color.bg))
                backgroundTintList = getColorStateList(R.color.violet)
                setOnClickListener { startActivity(Intent(this@StatusActivity, GameActivity::class.java)) }
            })
        })
        val list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(16))
        }
        root.addView(ScrollView(this).apply { addView(list) }, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(Tabs.bar(this, "status"))
        setContentView(root)

        for (metric in metrics) {
            val box = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = rounded(getColor(R.color.panel), getColor(R.color.border))
                setPadding(dp(14), dp(10), dp(14), dp(10))
            }
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(TextView(this).apply {
                text = metric.name
                setTextColor(getColor(R.color.text))
                textSize = 14f
            }, LinearLayout.LayoutParams(0, -2, 1f))
            metric.value = monoLabel("…", getColor(R.color.cyan), 15f)
            row.addView(metric.value)
            box.addView(row)
            metric.chart = ChartView(this)
            box.addView(metric.chart, LinearLayout.LayoutParams(-1, dp(56)).apply { topMargin = dp(6) })
            list.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(10) })
        }
        list.addView(monoLabel("DISCOS", getColor(R.color.muted)).apply { setPadding(0, dp(6), 0, dp(6)) })
        disks = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        list.addView(disks)
        note = TextView(this).apply { setTextColor(getColor(R.color.muted)); textSize = 12f; setPadding(0, dp(8), 0, 0) }
        list.addView(note)
    }

    override fun onStart() {
        super.onStart()
        val client = jarvisApi() ?: return finish()
        val mine = ++generation
        thread(name = "equipo") {
            while (mine == generation) {
                try {
                    val data = client.getJson("/api/status")
                    ui.post { if (mine == generation) render(data) }
                } catch (e: Exception) {
                    ui.post { note.text = "Sin conexión con la PC; reintentando…" }
                }
                Thread.sleep(3000)
            }
        }
    }

    override fun onStop() {
        super.onStop()
        generation++
    }

    private fun number(obj: JSONObject, key: String): Float? =
        if (obj.isNull(key) || !obj.has(key)) null else obj.optDouble(key).toFloat()

    private fun render(data: JSONObject) {
        val limits = data.optJSONObject("limits") ?: JSONObject()
        val history = data.optJSONArray("history") ?: JSONArray()
        val ram = data.optJSONObject("ram")
        for (metric in metrics) {
            val current = if (metric.key == "ram") ram?.optDouble("pct")?.toFloat() else number(data, metric.key)
            val limit = metric.limitKey?.let { number(limits, it) }
            val hot = current != null && limit != null && current >= limit
            metric.value.text = when {
                current == null -> "sin dato"
                metric.key == "ram" && ram != null ->
                    "%.0f %%  ·  %.1f / %.0f GB".format(current, ram.optLong("used") / GB, ram.optLong("total") / GB)
                else -> "%.0f %s".format(current, metric.unit)
            }
            metric.value.setTextColor(getColor(if (hot) R.color.error else R.color.cyan))
            val values = (0 until history.length()).map { number(history.getJSONObject(it), metric.key) }
            metric.chart.setData(values, metric.max, limit, getColor(if (hot) R.color.error else R.color.cyan))
        }
        disks.removeAllViews()
        val list = data.optJSONArray("disks") ?: JSONArray()
        for (i in 0 until list.length()) {
            val disk = list.getJSONObject(i)
            val pct = disk.optDouble("pct").toFloat()
            val name = disk.optString("unit") + disk.optString("name").let { if (it.isEmpty()) "" else " ($it)" }
            val box = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = rounded(getColor(R.color.panel), getColor(R.color.border))
                setPadding(dp(14), dp(10), dp(14), dp(12))
            }
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(TextView(this).apply { text = "Disco $name"; setTextColor(getColor(R.color.text)) },
                LinearLayout.LayoutParams(0, -2, 1f))
            row.addView(monoLabel("%.0f GB libres de %.0f".format(disk.optLong("free") / GB, disk.optLong("total") / GB),
                getColor(if (pct >= 90) R.color.error else R.color.cyan), 12f))
            box.addView(row)
            box.addView(ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
                max = 100
                progress = pct.toInt()
                progressTintList = getColorStateList(if (pct >= 90) R.color.error else R.color.cyan)
            }, LinearLayout.LayoutParams(-1, dp(10)).apply { topMargin = dp(6) })
            disks.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
        }
        val gpu = data.optString("gpuName")
        note.text = buildString {
            if (gpu.isNotEmpty()) append("GPU: $gpu. ")
            if (number(data, "cpu") == null) append("La temperatura de la CPU necesita el driver PawnIO en la PC.")
        }
    }

    companion object {
        const val GB = 1024.0 * 1024.0 * 1024.0
    }
}
