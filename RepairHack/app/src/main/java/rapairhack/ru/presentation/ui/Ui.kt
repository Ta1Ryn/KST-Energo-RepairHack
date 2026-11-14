package rapairhack.ru.presentation.ui

import android.app.Activity
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import rapairhack.ru.R

/**
 * Небольшой набор фирменных элементов КСТ для экранов, собранных в коде:
 * белые поверхности, один синий акцент, статусные цвета только для состояний оборудования.
 */
object Ui {
    fun Context.dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    fun Context.color(@ColorRes id: Int) = ContextCompat.getColor(this, id)

    fun text(context: Context, size: Float = 16f, @ColorRes color: Int = R.color.black, bold: Boolean = false) =
        TextView(context).apply {
            textSize = size
            setTextColor(context.color(color))
            if (bold) typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            setLineSpacing(0f, 1.15f)
        }

    fun surface(context: Context, @ColorRes fill: Int = R.color.surface, @ColorRes stroke: Int = R.color.divider, radius: Int = 16) =
        GradientDrawable().apply {
            cornerRadius = context.dp(radius).toFloat()
            setColor(context.color(fill))
            setStroke(context.dp(1), context.color(stroke))
        }

    fun card(context: Context, padding: Int = 16) = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        background = surface(context)
        setPadding(context.dp(padding), context.dp(padding), context.dp(padding), context.dp(padding))
    }

    fun primary(context: Context, label: String, onClick: () -> Unit) = MaterialButton(context).apply {
        text = label; isAllCaps = false; minHeight = context.dp(52); cornerRadius = context.dp(12)
        backgroundTintList = ContextCompat.getColorStateList(context, R.color.accent)
        setTextColor(context.color(R.color.on_accent))
        setOnClickListener { onClick() }
    }

    fun secondary(context: Context, label: String, onClick: () -> Unit) =
        MaterialButton(context, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
            text = label; isAllCaps = false; minHeight = context.dp(52); cornerRadius = context.dp(12)
            strokeColor = ContextCompat.getColorStateList(context, R.color.accent_inactive)
            setTextColor(context.color(R.color.accent))
            setOnClickListener { onClick() }
        }

    /** Цветная плашка состояния: «Данные устарели», «Нет сети», ошибка, успех. */
    fun banner(context: Context) = TextView(context).apply {
        textSize = 15f
        setPadding(context.dp(14), context.dp(10), context.dp(14), context.dp(10))
        visibility = View.GONE
    }

    enum class Tone(@ColorRes val fg: Int, @ColorRes val bg: Int) {
        Info(R.color.accent_deep, R.color.accent_soft),
        Ok(R.color.success, R.color.success_soft),
        Warn(R.color.warning, R.color.warning_soft),
        Error(R.color.error, R.color.error_soft),
        Stale(R.color.text_secondary, R.color.stale_soft),
    }

    fun TextView.show(message: String?, tone: Tone) {
        if (message.isNullOrBlank()) { visibility = View.GONE; return }
        text = message
        setTextColor(context.color(if (tone == Tone.Warn) R.color.black else tone.fg))
        background = surface(context, tone.bg, tone.bg, 12)
        visibility = View.VISIBLE
    }

    /** Шапка экрана: «Назад», логотип КСТ, заголовок и подзаголовок. */
    class Header(val view: LinearLayout, val title: TextView, val subtitle: TextView)

    fun header(activity: Activity, title: String, onBack: () -> Unit): Header {
        val row = LinearLayout(activity).apply { gravity = Gravity.CENTER_VERTICAL; orientation = LinearLayout.HORIZONTAL }
        val back = MaterialButton(activity, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
            text = "Назад"; isAllCaps = false; setTextColor(activity.color(R.color.accent))
            icon = ContextCompat.getDrawable(activity, androidx.appcompat.R.drawable.abc_ic_ab_back_material)
            iconTint = ColorStateList.valueOf(activity.color(R.color.accent))
            minHeight = activity.dp(48)
            setOnClickListener { onBack() }
        }
        row.addView(back)
        row.addView(ImageView(activity).apply {
            setImageResource(R.drawable.logo_kst); adjustViewBounds = true
            setBackgroundResource(R.drawable.bg_logo_backplate)
            setPadding(activity.dp(5), activity.dp(5), activity.dp(5), activity.dp(5))
            contentDescription = activity.getString(R.string.company_name)
        }, LinearLayout.LayoutParams(-2, activity.dp(44)).apply { marginStart = activity.dp(8); marginEnd = activity.dp(20) })
        val column = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        val titleView = text(activity, 22f, bold = true).apply { text = title; maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END }
        column.addView(titleView)
        val subtitle = text(activity, 14f, R.color.text_secondary).apply { visibility = View.GONE }
        column.addView(subtitle)
        row.addView(column, LinearLayout.LayoutParams(0, -2, 1f))
        return Header(row, titleView, subtitle)
    }

    fun spacer(context: Context, height: Int) = View(context).apply { layoutParams = LinearLayout.LayoutParams(1, context.dp(height)) }
}
