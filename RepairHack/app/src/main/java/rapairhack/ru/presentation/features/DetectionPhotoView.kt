package rapairhack.ru.presentation.features

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import androidx.core.content.ContextCompat
import org.json.JSONArray
import rapairhack.ru.R

/**
 * Фото с рамками дефектов. Рамки приходят в долях исходного (уже повёрнутого по EXIF) снимка 0–1.
 * Снимок и рамки рисуются через одну и ту же матрицу, поэтому после поворота,
 * масштабирования и сдвига рамки остаются на своих местах.
 */
class DetectionPhotoView(context: Context) : View(context) {
    var bitmap: Bitmap? = null
        set(value) { field = value; resetView() }
    var detections = JSONArray()
        set(value) { field = value; invalidate() }
    /** Отклонённый инженером результат показываем приглушённо. */
    var dimmed = false
        set(value) { field = value; invalidate() }

    private val density = resources.displayMetrics.density
    private var rotation = 0 // 0, 90, 180, 270
    private var userScale = 1f
    private var panX = 0f
    private var panY = 0f
    private val matrix = Matrix()
    private val imagePaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = 3 * density; style = Paint.Style.STROKE }
    private val maskPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val tagPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val tagText = Paint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 13 * density; color = 0xFFFFFFFF.toInt() }
    private val hint = Paint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 15 * density; textAlign = Paint.Align.CENTER }

    private val scaleDetector = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(d: ScaleGestureDetector): Boolean {
            val next = (userScale * d.scaleFactor).coerceIn(1f, 6f)
            val factor = next / userScale
            // Масштаб вокруг точки между пальцами.
            panX = (panX - (d.focusX - width / 2f)) * factor + (d.focusX - width / 2f)
            panY = (panY - (d.focusY - height / 2f)) * factor + (d.focusY - height / 2f)
            userScale = next; clampPan(); invalidate(); return true
        }
    })
    private val gestures = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent) = true
        override fun onScroll(e1: MotionEvent?, e2: MotionEvent, dx: Float, dy: Float): Boolean {
            if (userScale <= 1f) return false
            panX -= dx; panY -= dy; clampPan(); invalidate(); return true
        }
        override fun onDoubleTap(e: MotionEvent): Boolean {
            if (userScale > 1f) { userScale = 1f; panX = 0f; panY = 0f } else userScale = 2.5f
            clampPan(); invalidate(); return true
        }
    })

    init { contentDescription = "Фото осмотра. Двойное касание — увеличить" }

    fun rotateClockwise() { rotation = (rotation + 90) % 360; userScale = 1f; panX = 0f; panY = 0f; invalidate() }
    fun resetView() { rotation = 0; userScale = 1f; panX = 0f; panY = 0f; invalidate() }

    private fun rotatedSize(image: Bitmap) =
        if (rotation % 180 == 0) image.width.toFloat() to image.height.toFloat() else image.height.toFloat() to image.width.toFloat()

    private fun buildMatrix(image: Bitmap) {
        val (w, h) = rotatedSize(image)
        val fit = minOf(width / w, height / h) * userScale
        matrix.reset()
        matrix.postTranslate(-image.width / 2f, -image.height / 2f)
        matrix.postRotate(rotation.toFloat())
        matrix.postScale(fit, fit)
        matrix.postTranslate(width / 2f + panX, height / 2f + panY)
    }

    private fun clampPan() {
        val image = bitmap ?: return
        val (w, h) = rotatedSize(image)
        val fit = minOf(width / w, height / h) * userScale
        val maxX = maxOf(0f, (w * fit - width) / 2f); val maxY = maxOf(0f, (h * fit - height) / 2f)
        panX = panX.coerceIn(-maxX, maxX); panY = panY.coerceIn(-maxY, maxY)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawColor(ContextCompat.getColor(context, R.color.stale_soft))
        val image = bitmap
        if (image == null) {
            hint.color = ContextCompat.getColor(context, R.color.text_secondary)
            canvas.drawText("Сделайте фото узла или выберите снимок", width / 2f, height / 2f, hint)
            return
        }
        buildMatrix(image)
        canvas.save(); canvas.clipRect(0f, 0f, width.toFloat(), height.toFloat())
        canvas.drawBitmap(image, matrix, imagePaint)
        val color = ContextCompat.getColor(context, if (dimmed) R.color.stale else R.color.detection_outline)
        boxPaint.color = color; tagPaint.color = 0xff102746.toInt()
        maskPaint.color = color; maskPaint.alpha = if (dimmed) 22 else 58
        tagPaint.alpha = if (dimmed) 170 else 255
        boxPaint.pathEffect = if (dimmed) DashPathEffect(floatArrayOf(8 * density, 6 * density), 0f) else null
        val rect = RectF()
        val viewport = RectF(0f, 0f, width.toFloat(), height.toFloat())
        for (i in 0 until detections.length()) {
            val d = detections.getJSONObject(i)
            val box = d.optJSONObject("box") ?: continue
            val x = box.optDouble("x").toFloat(); val y = box.optDouble("y").toFloat()
            val w = box.optDouble("width").toFloat(); val h = box.optDouble("height").toFloat()
            // Координаты рамки в пикселях исходного снимка → та же матрица, что и у снимка.
            rect.set(x * image.width, y * image.height, (x + w) * image.width, (y + h) * image.height)
            matrix.mapRect(rect)
            if (!RectF.intersects(rect, viewport)) continue
            val contour = DetectionGeometry.contour(d, image.width.toFloat(), image.height.toFloat(), matrix)
            if (contour != null) {
                canvas.drawPath(contour, maskPaint)
                canvas.drawPath(contour, boxPaint)
            } else canvas.drawRect(rect, boxPaint)
            val code = d.optString("label").takeIf { it.isNotBlank() } ?: continue
            val label = labels[code] ?: code
            val conf = d.optDouble("confidence", Double.NaN).takeIf { it.isFinite() }?.let { " ${(it.coerceIn(0.0, 1.0) * 100).toInt()}%" } ?: ""
            val tag = label + conf
            val tw = minOf(tagText.measureText(tag) + 12 * density, width.toFloat())
            val left = rect.left.coerceIn(0f, maxOf(0f, width - tw))
            val top = (rect.top - 22 * density).coerceIn(0f, maxOf(0f, height - 22 * density))
            canvas.save(); canvas.clipRect(left, top, left + tw, top + 22 * density)
            canvas.drawRect(left, top, left + tw, top + 22 * density, tagPaint)
            canvas.drawText(tag, left + 6 * density, top + 16 * density, tagText)
            canvas.restore()
        }
        canvas.restore()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (bitmap == null) return false
        scaleDetector.onTouchEvent(event)
        gestures.onTouchEvent(event)
        if (userScale > 1f) parent?.requestDisallowInterceptTouchEvent(true)
        if (event.action == MotionEvent.ACTION_UP) performClick()
        return true
    }

    override fun performClick(): Boolean { super.performClick(); return true }

    companion object {
        val labels = mapOf(
            "corrosion" to "Коррозия", "leak" to "Возможные следы жидкости", "crack" to "Трещина",
            "insulation_damage" to "Повреждение изоляции", "heat_damage" to "Следы перегрева",
            "contamination" to "Загрязнение", "mechanical_damage" to "Повреждение",
            "missing_part" to "Отсутствующая деталь", "wear" to "Износ",
            "dark_spot" to "Тёмное пятно", "surface_fold" to "Складка поверхности",
            "porosity" to "Пористость", "surface_scratch" to "Царапина",
            "surface_stain" to "Пятно на поверхности",
        )
    }
}
