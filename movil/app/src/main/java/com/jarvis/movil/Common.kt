package com.jarvis.movil

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView

fun Context.dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

fun Context.jarvisApi(): Api? {
    val prefs = getSharedPreferences("jarvis", Context.MODE_PRIVATE)
    val base = prefs.getString("base", null)
    val token = prefs.getString("token", null)
    return if (base.isNullOrEmpty() || token.isNullOrEmpty()) null else Api(base, token)
}

fun Context.rounded(color: Int, stroke: Int? = null): GradientDrawable = GradientDrawable().apply {
    setColor(color)
    cornerRadius = dp(14).toFloat()
    if (stroke != null) setStroke(dp(1), stroke)
}

fun Context.monoLabel(text: String, color: Int, size: Float = 10f): TextView = TextView(this).apply {
    this.text = text
    setTextColor(color)
    textSize = size
    typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
}

/** Pantallas nuevas de la app: huella al volver (Lock) y barra de pestañas. */
abstract class JarvisActivity : Activity() {

    protected val content: ViewGroup get() = findViewById(android.R.id.content)

    override fun onStart() {
        super.onStart()
        Lock.onStart(this, content)
    }

    override fun onStop() {
        super.onStop()
        Lock.onStop()
    }

    @Deprecated("Solo para la pantalla de bloqueo de Android 8–9.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (!Lock.onActivityResult(requestCode, resultCode)) {
            @Suppress("DEPRECATION")
            super.onActivityResult(requestCode, resultCode, data)
        }
    }
}

/** Pestañas de abajo: Chat · Pantalla · Música · Equipo · Ajustes. */
object Tabs {
    private val items = listOf(
        Triple("chat", "💬\nChat", MainActivity::class.java),
        Triple("screen", "🖥\nPantalla", ScreenActivity::class.java),
        Triple("music", "🎵\nMúsica", MusicActivity::class.java),
        Triple("status", "📊\nEquipo", StatusActivity::class.java),
        Triple("settings", "⚙\nAjustes", SettingsActivity::class.java),
    )

    fun bar(activity: Activity, current: String): LinearLayout {
        val bar = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(activity.getColor(R.color.panel))
            setPadding(0, activity.dp(4), 0, activity.dp(4))
        }
        for ((key, label, target) in items) {
            val selected = key == current
            bar.addView(TextView(activity).apply {
                text = label
                gravity = Gravity.CENTER
                textSize = 11f
                setTextColor(activity.getColor(if (selected) R.color.cyan else R.color.muted))
                typeface = if (selected) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
                setPadding(0, activity.dp(4), 0, activity.dp(4))
                if (!selected) setOnClickListener {
                    activity.startActivity(Intent(activity, target)
                        .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_NO_ANIMATION))
                    @Suppress("DEPRECATION")
                    activity.overridePendingTransition(0, 0)
                }
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        }
        return bar
    }
}

/**
 * Gráfico de línea de los últimos minutos (sin bibliotecas). ``limit`` dibuja una
 * línea roja punteada; los huecos (sin dato) cortan la línea.
 */
class ChartView(context: Context) : View(context) {
    private var values: List<Float?> = emptyList()
    private var maximum = 100f
    private var limit: Float? = null
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = context.dp(2).toFloat()
        color = context.getColor(R.color.cyan)
    }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = context.getColor(R.color.cyan_dark)
    }
    private val danger = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = context.dp(1).toFloat()
        color = context.getColor(R.color.error)
        pathEffect = DashPathEffect(floatArrayOf(10f, 8f), 0f)
    }
    private val grid = Paint().apply { color = context.getColor(R.color.border) }

    fun setData(values: List<Float?>, maximum: Float, limit: Float? = null, color: Int? = null) {
        this.values = values
        this.maximum = maxOf(maximum, values.filterNotNull().maxOrNull() ?: 0f, 1f)
        this.limit = limit
        if (color != null) line.color = color
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        canvas.drawLine(0f, h - 1, w, h - 1, grid)
        limit?.let {
            val y = h - h * (it / maximum)
            canvas.drawLine(0f, y, w, y, danger)
        }
        if (values.size < 2) return
        val step = w / (values.size - 1)
        val path = Path()
        val area = Path()
        var open = false
        var startX = 0f
        var lastX = 0f
        for ((i, value) in values.withIndex()) {
            val x = i * step
            if (value == null) {
                if (open) {
                    area.lineTo(lastX, h)
                    area.lineTo(startX, h)
                    area.close()
                }
                open = false
                continue
            }
            val y = h - h * (value / maximum).coerceIn(0f, 1f)
            if (!open) {
                path.moveTo(x, y)
                area.moveTo(x, h)
                area.lineTo(x, y)
                startX = x
                open = true
            } else {
                path.lineTo(x, y)
                area.lineTo(x, y)
            }
            lastX = x
        }
        if (open) {
            area.lineTo(lastX, h)
            area.lineTo(startX, h)
            area.close()
        }
        canvas.drawPath(area, fill)
        canvas.drawPath(path, line)
    }
}
