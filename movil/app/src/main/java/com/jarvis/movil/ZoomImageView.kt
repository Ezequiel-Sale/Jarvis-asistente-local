package com.jarvis.movil

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.graphics.RectF
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.TextureView
import android.view.View
import android.widget.ImageView
import kotlin.math.max
import kotlin.math.min

/**
 * Zoom de dos dedos, arrastre y doble toque (acercar / volver a encajar) para una
 * vista que muestra contenido de ``contentWidth``×``contentHeight``. ``apply`` recibe
 * la matriz contenido → vista.
 */
class Zoomer(private val view: View, private val apply: (Matrix) -> Unit) {

    var onTap: (() -> Unit)? = null
    var contentWidth = 0
        private set
    var contentHeight = 0
        private set

    private val transform = Matrix()
    private val values = FloatArray(9)

    /** Nuevo tamaño de contenido: si cambió, se vuelve a encajar. */
    fun setContentSize(width: Int, height: Int) {
        if (width == contentWidth && height == contentHeight) {
            apply(transform)
            return
        }
        contentWidth = width
        contentHeight = height
        fit()
    }

    private fun fitScale(): Float =
        if (contentWidth == 0 || view.width == 0) 1f
        else min(view.width.toFloat() / contentWidth, view.height.toFloat() / contentHeight)

    private fun currentScale(): Float {
        transform.getValues(values)
        return values[Matrix.MSCALE_X]
    }

    fun fit() {
        if (contentWidth == 0 || view.width == 0) return
        val scale = fitScale()
        transform.reset()
        transform.postScale(scale, scale)
        transform.postTranslate((view.width - contentWidth * scale) / 2f, (view.height - contentHeight * scale) / 2f)
        apply(transform)
    }

    /** Evita que el contenido se salga: centrado si cabe, bordes pegados si no. */
    private fun clamp() {
        val rect = RectF(0f, 0f, contentWidth.toFloat(), contentHeight.toFloat())
        transform.mapRect(rect)
        val w = view.width
        val h = view.height
        val dx = when {
            rect.width() <= w -> (w - rect.width()) / 2f - rect.left
            rect.left > 0 -> -rect.left
            rect.right < w -> w - rect.right
            else -> 0f
        }
        val dy = when {
            rect.height() <= h -> (h - rect.height()) / 2f - rect.top
            rect.top > 0 -> -rect.top
            rect.bottom < h -> h - rect.bottom
            else -> 0f
        }
        transform.postTranslate(dx, dy)
        apply(transform)
    }

    private fun zoomBy(factor: Float, x: Float, y: Float) {
        val minimum = fitScale()
        val target = max(minimum, min(minimum * 8f, currentScale() * factor))
        val real = target / currentScale()
        transform.postScale(real, real, x, y)
        clamp()
    }

    private val scaler = ScaleGestureDetector(view.context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            zoomBy(detector.scaleFactor, detector.focusX, detector.focusY)
            return true
        }
    })

    private val gestures = GestureDetector(view.context, object : GestureDetector.SimpleOnGestureListener() {
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

    fun onTouch(event: MotionEvent): Boolean {
        scaler.onTouchEvent(event)
        gestures.onTouchEvent(event)
        return true
    }
}

/** Imágenes JPEG de la pantalla, con zoom. Al cambiar de cuadro se conserva el zoom. */
class ZoomImageView(context: Context) : ImageView(context) {

    val zoom: Zoomer = Zoomer(this) { imageMatrix = it }

    init {
        scaleType = ScaleType.MATRIX
    }

    fun setFrame(bitmap: Bitmap) {
        setImageBitmap(bitmap)
        zoom.setContentSize(bitmap.width, bitmap.height)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        zoom.fit()
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean = zoom.onTouch(event)
}

/**
 * Vídeo de la pantalla (lo dibuja MediaCodec en esta superficie), con zoom.
 * TextureView estira el vídeo a toda la vista; la matriz lo corrige.
 */
class ZoomTextureView(context: Context) : TextureView(context) {

    val zoom: Zoomer = Zoomer(this) { matrix ->
        if (width > 0 && zoomContentReady()) {
            val stretched = Matrix(matrix)
            stretched.preScale(zoom.contentWidth / width.toFloat(), zoom.contentHeight / height.toFloat())
            setTransform(stretched)
        }
    }

    private fun zoomContentReady(): Boolean = zoom.contentWidth > 0 && zoom.contentHeight > 0

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        zoom.fit()
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean = zoom.onTouch(event)
}
