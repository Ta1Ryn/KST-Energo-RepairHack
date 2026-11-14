package rapairhack.ru.presentation.features

import android.graphics.Matrix
import android.graphics.Path
import org.json.JSONObject

/** Только настоящие нормализованные контуры модели; прямоугольник в маску не преобразуем. */
object DetectionGeometry {
    fun contour(detection: JSONObject, width: Float, height: Float, matrix: Matrix? = null,
                left: Float = 0f, top: Float = 0f): Path? {
        val polygon = detection.optJSONArray("polygon") ?: return null
        if (polygon.length() !in 3..96) return null
        val points = FloatArray(polygon.length() * 2)
        for (i in 0 until polygon.length()) {
            val point = polygon.optJSONArray(i) ?: return null
            if (point.length() != 2) return null
            val x = point.optDouble(0, Double.NaN); val y = point.optDouble(1, Double.NaN)
            if (!x.isFinite() || !y.isFinite() || x !in 0.0..1.0 || y !in 0.0..1.0) return null
            points[i * 2] = left + x.toFloat() * width
            points[i * 2 + 1] = top + y.toFloat() * height
        }
        matrix?.mapPoints(points)
        return Path().apply {
            moveTo(points[0], points[1])
            for (i in 1 until polygon.length()) lineTo(points[i * 2], points[i * 2 + 1])
            close()
        }
    }
}
