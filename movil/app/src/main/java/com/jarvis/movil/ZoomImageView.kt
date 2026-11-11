package com.jarvis.movil

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.graphics.RectF
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.widget.ImageView
import kotlin.math.max
import kotlin.math.min

/**
 * Imagen con zoom de dos dedos, arrastre y doble toque (acercar / volver a encajar).
 * Al cambiar de cuadro se conserva el zoom si el tamaño de la imagen no cambió.
 */
class ZoomImageView(context: Context) : ImageView(context) {

    var onTap: (() -> Unit)? = null

    private val transform = Matrix()
    private val values = FloatArray(9)
    private var imageWidth = 0
    private var imageHeight = 0

    init {
        scaleType = ScaleType.MATRIX
    }

    fun setFrame(bitmap: Bitmap) {
        val reset = bitmap.width != imageWidth || bitmap.height != imageHeight
        imageWidth = bitmap.width
        imageHeight = bitmap.height
        setImageBitmap(bitmap)
        if (reset) fit() else imageMatrix = transform
    }

    private fun fitScale(): Float =
        if (imageWidth == 0 || width == 0) 1f else min(width.toFloat() / imageWidth, height.toFloat() / imageHeight)

    private fun currentScale(): Float {
        transform.getValues(values)
        return values[Matrix.MSCALE_X]
    }

    fun fit() {
        if (imageWidth == 0 || width == 0) return
        val scale = fitScale()
        transform.reset()
        transform.postScale(scale, scale)
        transform.postTranslate((width - imageWidth * scale) / 2f, (height - imageHeight * scale) / 2f)
        imageMatrix = transform
    }

    /** Evita que la imagen se salga: centrada si cabe, bordes pegados si no. */
    private fun clamp() {
        val rect = RectF(0f, 0f, imageWidth.toFloat(), imageHeight.toFloat())
        transform.mapRect(rect)
        val dx = when {
            rect.width() <= width -> (width - rect.width()) / 2f - rect.left
            rect.left > 0 -> -rect.left
            rect.right < width -> width - rect.right
            else -> 0f
        }
        val dy = when {
            rect.height() <= height -> (height - rect.height()) / 2f - rect.top
            rect.top > 0 -> -rect.top
            rect.bottom < height -> height - rect.bottom
            else -> 0f
        }
        transform.postTranslate(dx, dy)
        imageMatrix = transform
    }

    private fun zoomBy(factor: Float, x: Float, y: Float) {
        val minimum = fitScale()
        val target = max(minimum, min(minimum * 8f, currentScale() * factor))
        val real = target / currentScale()
        transform.postScale(real, real, x, y)
        clamp()
    }

    private val scaler = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            zoomBy(detector.scaleFactor, detector.focusX, detector.focusY)
            return true
        }
    })

    private val gestures = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onScroll(e1: MotionEvent?, e2: MotionEvent, dx: Float, dy: Float): Boolean {
            transform.postTranslate(-dx, -dy)
            clamp()
            return true
        }

        override fun onDoubleTap(e: MotionEvent): Boolean {
            if (currentScale() > fitScale() * 1.05f) fit() else zoomBy(2.5f, e.x, e.y)
            return true
        }

        override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
            onTap?.invoke()
            return true
        }
    })

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        fit()
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        scaler.onTouchEvent(event)
        gestures.onTouchEvent(event)
        return true
    }
}
