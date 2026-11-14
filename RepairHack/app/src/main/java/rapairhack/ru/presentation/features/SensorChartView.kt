package rapairhack.ru.presentation.features

import android.content.Context
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import androidx.core.content.ContextCompat
import rapairhack.ru.R
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * График датчика: линия нормы (предупреждение и аварийный порог), зона превышения,
 * точки превышений, последнее измерение. При устаревших данных график серый.
 */
class SensorChartView(context: Context) : View(context) {
    var points: List<Pair<Long, Float>> = emptyList()
        set(value) { field = value; invalidate() }
    var warning: Float? = null
        set(value) { field = value; invalidate() }
    var alarm: Float? = null
        set(value) { field = value; invalidate() }
    var minimum: Float? = null
        set(value) { field = value; invalidate() }
    var unit: String = ""
    var stale = false
        set(value) { field = value; invalidate() }
    /** Длина периода в часах: влияет на формат подписей времени. */
    var periodHours = 24

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val density = resources.displayMetrics.density
    private var selected: Int? = null
    private var touchX = 0f
    private var touchY = 0f
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private fun c(id: Int) = ContextCompat.getColor(context, id)
    private fun time(value: Long) = Instant.ofEpochMilli(value).atZone(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern(if (periodHours > 24) "dd.MM HH:mm" else "HH:mm"))
    private fun fmt(v: Float) = if (kotlin.math.abs(v) >= 100) "%.0f".format(v) else "%.1f".format(v)

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val left = 48 * density; val right = width - 12 * density; val top = 26 * density; val bottom = height - 24 * density
        paint.textSize = 12 * density; paint.strokeWidth = density; paint.style = Paint.Style.FILL; paint.pathEffect = null
        if (points.isEmpty()) {
            paint.color = c(R.color.text_secondary)
            canvas.drawText("За выбранный период измерений нет", left, top + 30 * density, paint); return
        }
        val values = points.map { it.second } + listOfNotNull(warning, alarm, minimum)
        val lo = values.min(); val hi = values.max()
        val pad = maxOf((hi - lo) * 0.12f, 0.5f)
        val min = lo - pad; val max = hi + pad
        fun y(v: Float) = bottom - (v - min) / (max - min) * (bottom - top)
        val first = points.first().first; val span = maxOf(1L, points.last().first - first)
        fun x(v: Long) = left + (v - first).toFloat() / span * (right - left)

        // Зона превышения аварийного порога.
        alarm?.let { a ->
            paint.color = c(R.color.error_soft); canvas.drawRect(left, top, right, y(a).coerceIn(top, bottom), paint)
        }
        // Сетка и шкала.
        for (i in 0..3) {
            val v = min + (max - min) * i / 3
            paint.color = c(R.color.divider); canvas.drawLine(left, y(v), right, y(v), paint)
            paint.color = c(R.color.text_secondary); canvas.drawText(fmt(v), 4 * density, y(v) + 4 * density, paint)
        }
        // Нормативы.
        val norms = listOf(
            Triple(warning, "Предупреждение ${warning?.let(::fmt)} $unit", R.color.warning),
            Triple(alarm, "Аварийный порог ${alarm?.let(::fmt)} $unit", R.color.error),
            Triple(minimum, "Минимум ${minimum?.let(::fmt)} $unit", R.color.warning),
        )
        for ((value, label, color) in norms) {
            if (value == null) continue
            paint.color = c(color); paint.strokeWidth = 1.5f * density
            paint.pathEffect = DashPathEffect(floatArrayOf(8 * density, 5 * density), 0f)
            canvas.drawLine(left, y(value), right, y(value), paint); paint.pathEffect = null
            paint.textAlign = Paint.Align.RIGHT
            canvas.drawText(label.trim(), right - 4 * density, y(value) - 5 * density, paint)
            paint.textAlign = Paint.Align.LEFT
        }
        // Линия значений.
        val lineColor = if (stale) c(R.color.stale) else c(R.color.accent)
        paint.color = lineColor; paint.strokeWidth = 2.5f * density; paint.style = Paint.Style.STROKE
        val path = Path()
        points.forEachIndexed { i, p -> if (i == 0) path.moveTo(x(p.first), y(p.second)) else path.lineTo(x(p.first), y(p.second)) }
        canvas.drawPath(path, paint)
        paint.style = Paint.Style.FILL
        // Отметки превышений.
        for (p in points) {
            val over = when {
                alarm != null && p.second > alarm!! -> R.color.error
                warning != null && p.second > warning!! -> R.color.warning
                minimum != null && p.second < minimum!! -> R.color.warning
                else -> null
            } ?: continue
            paint.color = c(over); canvas.drawCircle(x(p.first), y(p.second), 3.5f * density, paint)
        }
        // Последнее измерение.
        points.last().let { p ->
            paint.color = lineColor; canvas.drawCircle(x(p.first), y(p.second), 5 * density, paint)
            paint.color = c(R.color.surface); canvas.drawCircle(x(p.first), y(p.second), 2.2f * density, paint)
        }
        // Подписи времени.
        paint.color = c(R.color.text_secondary)
        canvas.drawText(time(first), left, height - 6 * density, paint)
        if (points.size > 1) { paint.textAlign = Paint.Align.RIGHT; canvas.drawText(time(points.last().first), right, height - 6 * density, paint); paint.textAlign = Paint.Align.LEFT }
        // Выбранная точка.
        selected?.coerceIn(0, points.lastIndex)?.let { i ->
            val p = points[i]
            paint.color = c(R.color.black); paint.strokeWidth = density
            canvas.drawLine(x(p.first), top, x(p.first), bottom, paint)
            canvas.drawText("${time(p.first)} · ${fmt(p.second)} $unit", left, 16 * density, paint)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (points.isEmpty()) return false
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                touchX = event.x; touchY = event.y
                parent?.requestDisallowInterceptTouchEvent(false)
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = kotlin.math.abs(event.x - touchX)
                val dy = kotlin.math.abs(event.y - touchY)
                if (dy > touchSlop && dy > dx) {
                    parent?.requestDisallowInterceptTouchEvent(false)
                    return false
                }
                if (dx > touchSlop) parent?.requestDisallowInterceptTouchEvent(true)
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                parent?.requestDisallowInterceptTouchEvent(false)
                if (event.actionMasked == MotionEvent.ACTION_UP) performClick()
                return true
            }
        }
        if (event.actionMasked == MotionEvent.ACTION_DOWN || event.actionMasked == MotionEvent.ACTION_MOVE) {
            val fraction = ((event.x - 48 * density) / (width - 60 * density)).coerceIn(0f, 1f)
            val target = points.first().first + ((points.last().first - points.first().first) * fraction).toLong()
            selected = points.indices.minByOrNull { kotlin.math.abs(points[it].first - target) }; invalidate(); return true
        }
        return true
    }

    override fun performClick(): Boolean { super.performClick(); return true }
}
