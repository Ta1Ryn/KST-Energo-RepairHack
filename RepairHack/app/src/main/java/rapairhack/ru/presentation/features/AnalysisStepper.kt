package rapairhack.ru.presentation.features

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.view.View
import androidx.core.content.ContextCompat
import rapairhack.ru.R

/**
 * Этапы анализа фото: Загрузка → В очереди → Анализ → Готово.
 * Ошибка подсвечивает этап, на котором всё остановилось.
 */
class AnalysisStepper(context: Context) : View(context) {
    enum class Stage { Idle, Upload, Queue, Analyze, Done }

    var stage = Stage.Idle
        private set
    var failed = false
        private set
    var detail: String? = null
        private set

    private val labels = listOf("Загрузка", "В очереди", "Анализ", "Готово")
    private val density = resources.displayMetrics.density
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = 3 * density }
    private val dot = Paint(Paint.ANTI_ALIAS_FLAG)
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 14 * density; textAlign = Paint.Align.CENTER }
    private val mark = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = 13 * density; textAlign = Paint.Align.CENTER; typeface = Typeface.DEFAULT_BOLD
    }
    private fun c(id: Int) = ContextCompat.getColor(context, id)

    fun set(stage: Stage, detail: String? = null) {
        this.stage = stage; this.failed = false; this.detail = detail
        contentDescription = when (stage) {
            Stage.Idle -> "Анализ не запущен"
            Stage.Done -> "Анализ готов"
            else -> "Этап: ${labels[stage.ordinal - 1]}" + (detail?.let { ". $it" } ?: "")
        }
        invalidate()
    }

    fun fail(detail: String) {
        failed = true; this.detail = detail
        contentDescription = "Ошибка на этапе «${labels[(stage.ordinal - 1).coerceAtLeast(0)]}»: $detail"
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(MeasureSpec.getSize(widthMeasureSpec), (64 * density).toInt())
    }

    override fun onDraw(canvas: Canvas) {
        val count = labels.size
        val radius = 12 * density
        val cy = 18 * density
        val step = width / count.toFloat()
        val current = stage.ordinal - 1 // -1 у Idle
        for (i in 0 until count) {
            val cx = step * i + step / 2
            if (i < count - 1) {
                line.color = if (i < current || (stage == Stage.Done)) c(R.color.accent) else c(R.color.divider)
                canvas.drawLine(cx + radius, cy, cx + step - radius, cy, line)
            }
            val done = stage == Stage.Done || i < current
            val active = i == current && stage != Stage.Done
            val error = active && failed
            dot.style = if (done || active) Paint.Style.FILL else Paint.Style.STROKE
            dot.strokeWidth = 2 * density
            dot.color = when {
                error -> c(R.color.error)
                done && stage == Stage.Done && i == count - 1 -> c(R.color.success)
                done || active -> c(R.color.accent)
                else -> c(R.color.accent_inactive)
            }
            canvas.drawCircle(cx, cy, radius, dot)
            mark.color = c(R.color.white)
            when {
                error -> canvas.drawText("!", cx, cy + 5 * density, mark)
                done -> canvas.drawText("✓", cx, cy + 5 * density, mark)
                active -> canvas.drawCircle(cx, cy, 4 * density, dot.apply { color = c(R.color.white) })
            }
            label.color = when {
                error -> c(R.color.error)
                active || done -> c(R.color.black)
                else -> c(R.color.text_secondary)
            }
            label.typeface = if (active) Typeface.create("sans-serif-medium", Typeface.NORMAL) else Typeface.DEFAULT
            canvas.drawText(labels[i], cx, cy + radius + 22 * density, label)
        }
    }
}
