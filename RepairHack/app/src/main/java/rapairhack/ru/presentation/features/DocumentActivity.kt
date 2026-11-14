package rapairhack.ru.presentation.features

import android.graphics.Bitmap
import android.graphics.pdf.PdfRenderer
import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import rapairhack.ru.R
import rapairhack.ru.data.local.DocumentStore
import rapairhack.ru.presentation.ui.Ui
import rapairhack.ru.presentation.ui.Ui.color
import rapairhack.ru.presentation.ui.Ui.dp
import rapairhack.ru.presentation.ui.Ui.show

/** Просмотр PDF паспорта или инструкции. Открытый документ сохраняется для работы без сети. */
class DocumentActivity : TechnicalActivity() {
    private var renderer: PdfRenderer? = null
    private var page = 0
    private lateinit var header: Ui.Header
    private lateinit var status: TextView
    private lateinit var image: ImageView
    private lateinit var previous: MaterialButton
    private lateinit var next: MaterialButton
    private val title get() = intent.getStringExtra("title").orEmpty()

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setBackgroundColor(color(R.color.bg))
            setPadding(dp(20), dp(12), dp(20), dp(12))
        }
        header = Ui.header(this, title) { finish() }
        root.addView(header.view)
        status = Ui.banner(this)
        root.addView(status, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6) })
        image = ImageView(this).apply { adjustViewBounds = true; contentDescription = title }
        root.addView(ScrollView(this).apply { addView(image); background = Ui.surface(context) }, LinearLayout.LayoutParams(-1, 0, 1f).apply { topMargin = dp(8) })
        previous = Ui.secondary(this, "Предыдущая страница") { page--; renderPage() }.apply { isEnabled = false }
        next = Ui.secondary(this, "Следующая страница") { page++; renderPage() }.apply { isEnabled = false }
        root.addView(LinearLayout(this).apply {
            addView(previous, LinearLayout.LayoutParams(0, -2, 1f))
            addView(next, LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = dp(8) })
        }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        setContentView(root)
        page = state?.getInt("page") ?: 0
        val id = intent.getStringExtra("documentId")!!
        lifecycleScope.launch {
            try {
                val store = DocumentStore(this@DocumentActivity)
                val wasSaved = store.isSaved(id)
                val file = store.file(id, title, intent.getStringExtra("equipmentId"))
                renderer = PdfRenderer(ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY))
                page = page.coerceIn(0, renderer!!.pageCount - 1); renderPage()
                status.show(if (wasSaved) "Открыта сохранённая копия: документ доступен без сети." else "Документ сохранён на устройстве и будет доступен без сети.", Ui.Tone.Info)
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                status.show("Документ не открыт: нет связи с сервером, а копии на устройстве нет. Откройте его один раз при наличии сети.", Ui.Tone.Error)
            }
        }
    }

    private fun renderPage() {
        val pdf = renderer ?: return
        pdf.openPage(page).use { p ->
            val width = minOf(1600, resources.displayMetrics.widthPixels)
            val height = (width.toFloat() / p.width * p.height).toInt()
            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888); bitmap.eraseColor(android.graphics.Color.WHITE)
            p.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY); image.setImageBitmap(bitmap)
        }
        header.subtitle.text = "Страница ${page + 1} из ${pdf.pageCount}"; header.subtitle.visibility = View.VISIBLE
        previous.isEnabled = page > 0; next.isEnabled = page < pdf.pageCount - 1
    }

    override fun onSaveInstanceState(outState: Bundle) { super.onSaveInstanceState(outState); outState.putInt("page", page) }
    override fun onDestroy() { renderer?.close(); super.onDestroy() }
}
