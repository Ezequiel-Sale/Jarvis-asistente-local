package com.jarvis.movil

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * Capa sobre la imagen o el vídeo de la pantalla: dibuja el puntero de la PC y el
 * recuadro de «¿Qué es esto?». En modo touchpad recibe los gestos; si no, deja pasar
 * los toques al zoom que está debajo.
 */
class OverlayView(context: Context) : View(context) {

    enum class Mode { NONE, SELECT, TOUCHPAD }

    var mode = Mode.NONE
        set(value) {
            field = value
            selection = null
            invalidate()
        }

    /** El zoom de lo que se ve ahora (imagen o vídeo): da el tamaño y la matriz. */
    var zoom: Zoomer? = null

    /** Puntero en fracción (0–1) de lo que se ve, o null si no se ve. */
    var cursor: Pair<Float, Float>? = null
        set(value) {
            field = value
            invalidate()
        }

    var onSelected: ((Float, Float, Float, Float) -> Unit)? = null
    var touchpad: TouchpadSender? = null

    private var selection: RectF? = null
    private val selectionPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = context.dp(2).toFloat()
        color = context.getColor(R.color.cyan)
    }
    private val selectionFill = Paint().apply { color = 0x3342E8D8 }
    private val arrowFill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
    private val arrowLine = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = context.dp(1).toFloat()
        color = Color.BLACK
    }

    private fun contentToView(): Matrix? {
        val z = zoom ?: return null
        if (z.contentWidth == 0) return null
        return z.matrixCopy()
    }

    override fun onDraw(canvas: Canvas) {
        selection?.let {
            canvas.drawRect(it, selectionFill)
            canvas.drawRect(it, selectionPaint)
        }
        val (fx, fy) = cursor ?: return
        val z = zoom ?: return
        val matrix = contentToView() ?: return
        val point = floatArrayOf(fx * z.contentWidth, fy * z.contentHeight)
        matrix.mapPoints(point)
        val s = context.dp(1).toFloat()
        val path = Path().apply {  // flecha clásica del puntero
            moveTo(point[0], point[1])
            lineTo(point[0], point[1] + 17 * s)
            lineTo(point[0] + 4.5f * s, point[1] + 13 * s)
            lineTo(point[0] + 8 * s, point[1] + 20 * s)
            lineTo(point[0] + 10.5f * s, point[1] + 19 * s)
            lineTo(point[0] + 7 * s, point[1] + 12 * s)
            lineTo(point[0] + 12 * s, point[1] + 12 * s)
            close()
        }
        canvas.drawPath(path, arrowFill)
        canvas.drawPath(path, arrowLine)
    }

    // --- Toques ------------------------------------------------------------------------

    private var startX = 0f
    private var startY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var downAt = 0L
    private var moved = false
    private var twoFingers = false
    private var twoMoved = false
    private var scrollRest = 0f
    private var dragging = false
    private val ui = Handler(Looper.getMainLooper())
    private val longPress = Runnable {
        if (mode == Mode.TOUCHPAD && !moved && !twoFingers) {
            dragging = true
            performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
            touchpad?.button("down")
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean = when (mode) {
        Mode.NONE -> false
        Mode.SELECT -> select(event)
        Mode.TOUCHPAD -> pad(event)
    }

    private fun select(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                startX = event.x
                startY = event.y
                selection = RectF(startX, startY, startX, startY)
            }
            MotionEvent.ACTION_MOVE -> {
                selection = RectF(min(startX, event.x), min(startY, event.y), max(startX, event.x), max(startY, event.y))
                invalidate()
            }
            MotionEvent.ACTION_UP -> {
                val rect = selection ?: return true
                val z = zoom
                val matrix = contentToView()
                if (z != null && matrix != null && rect.width() > context.dp(12) && rect.height() > context.dp(12)) {
                    val inverse = Matrix()
                    matrix.invert(inverse)
                    val content = RectF(rect)
                    inverse.mapRect(content)
                    val x = (content.left / z.contentWidth).coerceIn(0f, 1f)
                    val y = (content.top / z.contentHeight).coerceIn(0f, 1f)
                    val w = (content.right / z.contentWidth).coerceIn(0f, 1f) - x
                    val h = (content.bottom / z.contentHeight).coerceIn(0f, 1f) - y
                    if (w > 0.005f && h > 0.005f) onSelected?.invoke(x, y, w, h)
                }
            }
        }
        return true
    }

    private fun pad(event: MotionEvent): Boolean {
        val sender = touchpad ?: return true
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                startX = event.x
                startY = event.y
                lastX = event.x
                lastY = event.y
                downAt = SystemClock.uptimeMillis()
                moved = false
                twoFingers = false
                twoMoved = false
                dragging = false
                scrollRest = 0f
                ui.postDelayed(longPress, 450)
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                twoFingers = true
                ui.removeCallbacks(longPress)
                lastY = (event.getY(0) + event.getY(1)) / 2
            }
            MotionEvent.ACTION_MOVE -> {
                if (twoFingers && event.pointerCount >= 2) {
                    val y = (event.getY(0) + event.getY(1)) / 2
                    scrollRest += y - lastY
                    lastY = y
                    val step = context.dp(28).toFloat()
                    val notches = (scrollRest / step).toInt()
                    if (notches != 0) {
                        twoMoved = true
                        scrollRest -= notches * step
                        sender.scroll(notches)  // desplazamiento natural: el contenido sigue al dedo
                    }
                } else if (!twoFingers) {
                    val dx = event.x - lastX
                    val dy = event.y - lastY
                    lastX = event.x
                    lastY = event.y
                    if (abs(event.x - startX) + abs(event.y - startY) > context.dp(8)) {
                        moved = true
                        if (!dragging) ui.removeCallbacks(longPress)
                    }
                    sender.move(dx * SENSITIVITY, dy * SENSITIVITY)
                }
            }
            MotionEvent.ACTION_POINTER_UP -> {
                if (twoFingers && !twoMoved && SystemClock.uptimeMillis() - downAt < 350) {
                    sender.click("right")
                    twoMoved = true  // que el último dedo al soltar no haga otro clic
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                ui.removeCallbacks(longPress)
                if (dragging) {
                    sender.button("up")
                } else if (event.actionMasked == MotionEvent.ACTION_UP && !moved && !twoFingers &&
                    SystemClock.uptimeMillis() - downAt < 300) {
                    sender.click("left")
                }
                dragging = false
            }
        }
        return true
    }

    companion object {
        const val SENSITIVITY = 1.6f
    }
}

/** Junta los gestos del touchpad y los manda a /api/input en lotes (cada ~25 ms). */
class TouchpadSender(private val api: Api, private val onError: (String) -> Unit) {
    private val queue = LinkedBlockingQueue<JSONObject>()
    private var restX = 0f
    private var restY = 0f

    @Volatile
    private var running = true

    init {
        thread(name = "touchpad", isDaemon = true) { loop() }
    }

    fun stop() {
        running = false
    }

    @Synchronized
    fun move(dx: Float, dy: Float) {
        restX += dx
        restY += dy
        val x = restX.toInt()
        val y = restY.toInt()
        if (x == 0 && y == 0) return
        restX -= x
        restY -= y
        queue.put(JSONObject().put("t", "move").put("dx", x).put("dy", y))
    }

    fun click(button: String) = queue.put(JSONObject().put("t", "click").put("b", button))
    fun button(action: String) = queue.put(JSONObject().put("t", action).put("b", "left"))
    fun scroll(notches: Int) = queue.put(JSONObject().put("t", "scroll").put("dy", notches))
    fun key(name: String) = queue.put(JSONObject().put("t", "key").put("k", name))
    fun text(value: String) = queue.put(JSONObject().put("t", "text").put("s", value))

    private fun loop() {
        while (running) {
            val first = queue.poll(500, TimeUnit.MILLISECONDS) ?: continue
            Thread.sleep(25)
            var dx = 0
            var dy = 0
            // Los movimientos seguidos se suman en uno; el resto va en orden.
            val pending = arrayListOf(first)
            queue.drainTo(pending, 150)
            val ordered = JSONArray()
            for (event in pending) {
                if (event.optString("t") == "move") {
                    dx += event.optInt("dx")
                    dy += event.optInt("dy")
                } else {
                    if (dx != 0 || dy != 0) {
                        ordered.put(JSONObject().put("t", "move").put("dx", dx).put("dy", dy))
                        dx = 0
                        dy = 0
                    }
                    ordered.put(event)
                }
            }
            if (dx != 0 || dy != 0) ordered.put(JSONObject().put("t", "move").put("dx", dx).put("dy", dy))
            try {
                api.postJson("/api/input", JSONObject().put("events", ordered))
            } catch (e: Exception) {
                onError(e.message ?: "No pude enviar el movimiento a la PC.")
                Thread.sleep(500)
            }
        }
    }
}
