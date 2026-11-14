package rapairhack.ru.presentation.features

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import rapairhack.ru.R
import rapairhack.ru.data.BackendApi
import rapairhack.ru.data.ApiError
import rapairhack.ru.data.isNetworkFailure
import rapairhack.ru.data.local.AppDatabase
import rapairhack.ru.data.local.CachedEquipment
import rapairhack.ru.data.local.DocumentStore
import rapairhack.ru.presentation.ui.Ui
import rapairhack.ru.presentation.ui.Ui.color
import rapairhack.ru.presentation.ui.Ui.dp
import rapairhack.ru.presentation.ui.Ui.show
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class EquipmentActivity : TechnicalActivity() {
    private val api by lazy { BackendApi(this) }
    private val db by lazy { AppDatabase.get(this) }
    private val documents by lazy { DocumentStore(this) }
    private val eid get() = intent.getStringExtra("equipmentId")!!

    private lateinit var header: Ui.Header
    private lateinit var freshness: TextView
    private lateinit var lastMeasured: TextView
    private lateinit var passport: TextView
    private lateinit var offlineNote: TextView
    private lateinit var alertCard: LinearLayout
    private lateinit var alertTitle: TextView
    private lateinit var alertText: TextView
    private lateinit var alertAction: MaterialButton
    private lateinit var sensorPanel: LinearLayout

    private class SensorRow(val title: TextView, val status: TextView, val chart: SensorChartView)
    private val rows = linkedMapOf<String, SensorRow>()
    private var card: JSONObject? = null
    private var history = JSONArray()
    private var hours = 24
    private var lastSuccess: Instant? = null
    private var serverError: String? = null
    private var instructions: List<JSONObject>? = null

    private val clock = DateTimeFormatter.ofPattern("HH:mm:ss")
    private val periods = listOf(1 to "1 ч", 6 to "6 ч", 24 to "24 ч", 168 to "7 дней")
    private val temperatureKeys = setOf("bearing", "winding")
    private val staleAfter = Duration.ofSeconds(60)

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        hours = state?.getInt("hours") ?: 24
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setBackgroundColor(color(R.color.bg))
            setPadding(dp(20), dp(12), dp(20), dp(12))
        }
        header = Ui.header(this, "Оборудование") { finish() }
        root.addView(header.view)

        // Строка состояния: время последнего измерения и выбор периода.
        val statusRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
        lastMeasured = Ui.text(this, 15f, R.color.text_secondary)
        statusRow.addView(lastMeasured, LinearLayout.LayoutParams(0, -2, 1f))
        val toggle = MaterialButtonToggleGroup(this).apply { isSingleSelection = true; isSelectionRequired = true }
        periods.forEach { (h, label) ->
            val b = MaterialButton(this, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
                id = View.generateViewId(); text = label; isAllCaps = false; tag = h
                contentDescription = "Период $label"
            }
            toggle.addView(b)
            if (h == hours) toggle.check(b.id)
        }
        toggle.addOnButtonCheckedListener { group, id, checked ->
            if (!checked) return@addOnButtonCheckedListener
            hours = group.findViewById<View>(id).tag as Int
            lifecycleScope.launch { refresh(force = true) }
        }
        statusRow.addView(toggle)
        root.addView(statusRow, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(4) })
        freshness = Ui.banner(this)
        root.addView(freshness, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6) })

        val panes = LinearLayout(this)
        root.addView(panes, LinearLayout.LayoutParams(-1, 0, 1f).apply { topMargin = dp(12) })

        // Слева: паспорт и действия.
        val left = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, 0, dp(16), 0) }
        panes.addView(ScrollView(this).apply { addView(left) }, LinearLayout.LayoutParams(0, -1, 0.36f))
        val passportCard = Ui.card(this)
        passportCard.addView(Ui.text(this, 18f, bold = true).apply { text = "Паспорт оборудования" })
        passport = Ui.text(this, 15f).apply { setPadding(0, dp(6), 0, 0) }
        passportCard.addView(passport)
        offlineNote = Ui.text(this, 13f, R.color.text_secondary).apply { setPadding(0, dp(8), 0, 0) }
        passportCard.addView(offlineNote)
        left.addView(passportCard)
        fun add(b: View) = left.addView(b, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        add(Ui.primary(this, "Осмотр и фото") { startActivity(Intent(this, InspectionActivity::class.java).putExtra("equipmentId", eid)) })
        add(Ui.secondary(this, "Дефекты онлайн") { startActivity(Intent(this, LiveInspectionActivity::class.java).putExtra("equipmentId", eid)) })
        add(Ui.secondary(this, "Открыть паспорт PDF") { showPassportPdf() })
        add(Ui.secondary(this, "Документы и инструкции") { showDocuments() })
        add(Ui.secondary(this, "Нормы и паспортные параметры") { showParameters() })
        add(Ui.secondary(this, "Как пользоваться") { showHelp() })

        // Справа: предупреждение и графики.
        val right = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        panes.addView(ScrollView(this).apply { addView(right) }, LinearLayout.LayoutParams(0, -1, 0.64f))
        alertCard = Ui.card(this).apply { visibility = View.GONE }
        alertTitle = Ui.text(this, 18f, bold = true)
        alertText = Ui.text(this, 15f).apply { setPadding(0, dp(6), 0, dp(8)) }
        alertAction = Ui.primary(this, "Открыть инструкцию") {}
        alertCard.addView(alertTitle); alertCard.addView(alertText); alertCard.addView(alertAction)
        right.addView(alertCard, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
        sensorPanel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        right.addView(sensorPanel)
        setContentView(root)

        lifecycleScope.launch { loadCached() }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) { refresh(force = false); delay(2000) }
            }
        }
        // Паспорт и инструкции сохраняются заранее, чтобы открыть их без сети.
        lifecycleScope.launch {
            val saved = runCatching { documents.prefetch(eid) }.getOrDefault(0)
            if (saved > 0) offlineNote.text = "Паспорт и $saved ${docsWord(saved)} сохранены для просмотра без сети"
        }
    }

    private fun docsWord(n: Int) = if (n % 10 == 1 && n % 100 != 11) "документ" else if (n % 10 in 2..4 && n % 100 !in 12..14) "документа" else "документов"

    private suspend fun loadCached() {
        if (card != null) return
        val cached = db.cache().equipment(eid) ?: return
        card = JSONObject(cached.json)
        render(card!!, history)
        offlineNote.text = "Сохранённая копия от ${Instant.ofEpochMilli(cached.savedAt).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("dd.MM HH:mm"))}"
        renderFreshness()
    }

    private var historyLoadedFor = 0
    private var historyLoadedAt = 0L
    private suspend fun refresh(force: Boolean) {
        try {
            val fresh = api.json("/api/equipment/$eid")
            // История тяжелее карточки: обновляем раз в 10 с или при смене периода.
            if (force || historyLoadedFor != hours || System.currentTimeMillis() - historyLoadedAt > 10_000) {
                val points = if (hours > 24) 500 else 240
                history = JSONArray(api.request("/api/equipment/$eid/telemetry-history?hours=$hours&points=$points"))
                historyLoadedFor = hours; historyLoadedAt = System.currentTimeMillis()
            }
            card = fresh; lastSuccess = Instant.now(); serverError = null
            db.cache().putEquipment(CachedEquipment(eid, fresh.toString(), db.cache().equipment(eid)?.documentsJson))
            render(fresh, history)
        } catch (e: CancellationException) { throw e } catch (e: Exception) {
            serverError = if (e.isNetworkFailure()) null else e.message ?: "Не удалось обновить оборудование."
            if (e is ApiError && e.status in listOf(401, 403, 404)) {
                card = null; history = JSONArray(); lastSuccess = null
                rows.clear(); sensorPanel.removeAllViews(); alertCard.visibility = View.GONE
                passport.text = "Оборудование недоступно."
                offlineNote.text = ""
            }
        }
        renderFreshness()
    }

    private fun newestMeasurement(): Instant? {
        val components = card?.optJSONArray("components") ?: return null
        var newest: Instant? = null
        for (i in 0 until components.length()) {
            val sensors = components.getJSONObject(i).optJSONArray("sensors") ?: continue
            for (j in 0 until sensors.length()) {
                val at = parseTime(sensors.getJSONObject(j).optString("measuredAt")) ?: continue
                if (newest == null || at.isAfter(newest)) newest = at
            }
        }
        return newest
    }

    private fun renderFreshness() {
        val now = Instant.now()
        val measured = newestMeasurement()
        lastMeasured.text = measured?.let { "Последнее измерение: ${fmt(it)} (${ago(Duration.between(it, now))})" } ?: "Измерений пока нет"
        val noLink = lastSuccess == null || Duration.between(lastSuccess, now) > Duration.ofSeconds(10)
        val sensorsSilent = measured != null && Duration.between(measured, now) > staleAfter
        val stale = card != null && (noLink || sensorsSilent)
        when {
            serverError != null -> freshness.show(serverError, Ui.Tone.Error)
            card == null && noLink -> freshness.show("Нет связи с сервером, а сохранённой копии паспорта нет. Подключитесь к сети.", Ui.Tone.Error)
            noLink -> freshness.show("Данные устарели. Нет связи с сервером${lastSuccess?.let { " с ${fmt(it)}" } ?: ""}. Показаны последние полученные значения.", Ui.Tone.Stale)
            sensorsSilent -> freshness.show("Данные устарели. Датчики не присылали измерений с ${fmt(measured!!)}.", Ui.Tone.Stale)
            else -> freshness.visibility = View.GONE
        }
        rows.values.forEach { it.chart.stale = stale }
    }

    private fun fmt(t: Instant) = t.atZone(ZoneId.systemDefault()).format(clock)
    private fun ago(d: Duration) = when {
        d.seconds < 60 -> "${maxOf(0, d.seconds)} с назад"
        d.toMinutes() < 60 -> "${d.toMinutes()} мин назад"
        else -> "${d.toHours()} ч назад"
    }
    private fun parseTime(raw: String?) = raw?.takeIf { it.isNotBlank() && it != "null" }?.let { runCatching { OffsetDateTime.parse(it).toInstant() }.getOrNull() }
    private fun num(o: JSONObject, key: String) = if (o.isNull(key) || !o.has(key)) null else o.optDouble(key).toFloat().takeIf { it.isFinite() }

    private fun render(card: JSONObject, history: JSONArray) {
        header.title.text = card.optString("name")
        header.subtitle.text = "${card.optString("model")} · заводской № ${card.optString("serialNumber")}"
        header.subtitle.visibility = View.VISIBLE
        val p = card.optJSONObject("passport")
        passport.text = buildString {
            append("Модель: ${card.optString("model")}\nЗаводской №: ${card.optString("serialNumber")}")
            for (key in listOf("purpose", "location", "operatingMode")) p?.optString(key)?.takeIf { it.isNotBlank() && it != "null" }?.let { append("\n$it") }
        }

        data class Overheat(val sensor: JSONObject, val recommendation: JSONObject?)
        val overheats = mutableListOf<Overheat>()
        val otherWarnings = linkedSetOf<String>()
        val components = card.getJSONArray("components")
        val order = listOf("bearing", "winding", "pressure", "vibration1x", "vibration2x", "hf", "rms", "rpm")
        val measurements = (0 until components.length()).flatMap { i ->
            val c = components.getJSONObject(i)
            val sensors = c.getJSONArray("sensors"); (0 until sensors.length()).map { c to sensors.getJSONObject(it) }
        }.sortedBy { order.indexOf(it.second.getString("key")).let { index -> if (index < 0) 999 else index } }

        for (i in 0 until components.length()) {
            val c = components.getJSONObject(i)
            val recs = c.optJSONArray("recommendations") ?: JSONArray()
            val sensors = c.getJSONArray("sensors")
            val hot = (0 until sensors.length()).map { sensors.getJSONObject(it) }.filter {
                it.optString("key") in temperatureKeys && it.optString("status") in setOf("outOfRange", "attention")
            }
            val used = mutableSetOf<Int>()
            for (s in hot) {
                val index = (0 until recs.length()).firstOrNull { j -> recs.getJSONObject(j).optString("reason").startsWith(s.optString("name")) }
                index?.let(used::add)
                overheats += Overheat(s, index?.let { recs.getJSONObject(it) })
            }
            for (j in 0 until recs.length()) if (j !in used) otherWarnings += recs.getJSONObject(j).optString("message")
        }

        for ((c, s) in measurements) {
            val key = c.getString("id") + ":" + s.getString("key")
            val row = rows.getOrPut(key) {
                val block = Ui.card(this, 12)
                val top = LinearLayout(this).apply { gravity = android.view.Gravity.CENTER_VERTICAL }
                val title = Ui.text(this, 17f, bold = true)
                val status = Ui.text(this, 13f).apply { setPadding(dp(10), dp(4), dp(10), dp(4)) }
                top.addView(title, LinearLayout.LayoutParams(0, -2, 1f)); top.addView(status)
                val chart = SensorChartView(this)
                block.addView(top); block.addView(chart, LinearLayout.LayoutParams(-1, dp(170)))
                sensorPanel.addView(block, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
                SensorRow(title, status, chart)
            }
            val status = s.optString("status")
            val unit = s.optString("unit").takeIf { it != "null" }.orEmpty()
            val value = if (s.isNull("value")) "—" else s.opt("value")?.toString() ?: "—"
            val measured = parseTime(s.optString("measuredAt"))?.let(::fmt) ?: "нет"
            row.title.text = "${s.getString("name")}: $value $unit · ${measured}"
            val (label, tone) = when (status) {
                "normal" -> "Норма" to Ui.Tone.Ok
                "attention" -> "Предупреждение" to Ui.Tone.Warn
                "outOfRange" -> "Превышен порог" to Ui.Tone.Error
                "stale" -> "Данные устарели" to Ui.Tone.Stale
                else -> "Нет нормы" to Ui.Tone.Stale
            }
            row.status.text = label
            row.status.setTextColor(color(if (tone == Ui.Tone.Warn) R.color.black else tone.fg))
            row.status.background = Ui.surface(this, tone.bg, tone.bg, 12)
            row.chart.unit = unit; row.chart.periodHours = hours
            row.chart.warning = num(s, "warningMax")
            row.chart.alarm = num(s, "max")
            row.chart.minimum = num(s, "min")?.takeIf { it > 0f }
            row.chart.points = (0 until history.length()).map { history.getJSONObject(it) }
                .filter { it.getString("componentId") == c.getString("id") && it.getString("key") == s.getString("key") }
                .mapNotNull { h -> parseTime(h.getString("measuredAt"))?.let { it.toEpochMilli() to h.getDouble("value").toFloat() } }
        }
        renderAlert(overheats.map { it.sensor to it.recommendation }, otherWarnings.toList())
    }

    /** Перегрев: конкретная рекомендация и ссылка на инструкцию. Остальные предупреждения — списком. */
    private fun renderAlert(overheats: List<Pair<JSONObject, JSONObject?>>, others: List<String>) {
        if (overheats.isEmpty() && others.isEmpty()) {
            alertCard.visibility = View.GONE; return
        }
        alertCard.visibility = View.VISIBLE
        if (overheats.isNotEmpty()) {
            val critical = overheats.any { it.first.optString("status") == "outOfRange" }
            alertCard.background = Ui.surface(this, if (critical) R.color.error_soft else R.color.warning_soft, if (critical) R.color.error else R.color.warning)
            val (sensor, rec) = overheats.first()
            val unit = sensor.optString("unit").takeIf { it != "null" }.orEmpty()
            val limit = num(sensor, if (critical) "max" else "warningMax")
            alertTitle.text = "${if (critical) "Перегрев" else "Температура растёт"}: ${sensor.optString("name")} ${sensor.opt("value")} $unit" +
                (limit?.let { " при норме до ${"%.0f".format(it)} $unit" } ?: "")
            val action = rec?.optString("message")?.takeIf { it.isNotBlank() && it != "null" } ?: if (critical)
                "Снизьте нагрузку на насос и проверьте смазку и охлаждение подшипника. Если температура не снижается за 15 минут, остановите насос по регламенту и сообщите мастеру смены."
            else "Проверьте смазку подшипника, затяжку и соосность муфты. Повторите измерение через 10 минут."
            alertText.text = action + if (others.isNotEmpty()) "\n\nЕщё требует проверки:\n• " + others.joinToString("\n• ") else ""
            alertAction.visibility = View.VISIBLE
            alertAction.text = "Открыть инструкцию"
            alertAction.setOnClickListener { openInstruction(rec?.optString("instructionId")?.takeIf { it.isNotBlank() && it != "null" }) }
        } else {
            alertCard.background = Ui.surface(this, R.color.warning_soft, R.color.warning)
            alertTitle.text = "Требуется проверка"
            alertText.text = "• " + others.joinToString("\n• ")
            alertAction.visibility = View.VISIBLE
            alertAction.text = "Документы и инструкции"
            alertAction.setOnClickListener { showDocuments() }
        }
    }

    private fun openInstruction(instructionId: String?) {
        lifecycleScope.launch {
            val list = instructions ?: runCatching {
                JSONArray(api.request("/api/instructions")).let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
            }.getOrNull()?.also { instructions = it }
            val instruction = list?.firstOrNull { it.optString("id") == instructionId }
            val documentId = instruction?.optString("documentId")?.takeIf { it.isNotBlank() && it != "null" }
            when {
                documentId != null -> openDocument(documentId, instruction.optString("title"))
                instruction != null -> AlertDialog.Builder(this@EquipmentActivity).setTitle(instruction.optString("title"))
                    .setMessage(instruction.optString("text")).setPositiveButton("Закрыть", null)
                    .setNeutralButton("Все документы") { _, _ -> showDocuments() }.show()
                else -> showDocuments(preferInstruction = true)
            }
        }
    }

    private fun openDocument(id: String, title: String) {
        startActivity(Intent(this, DocumentActivity::class.java).putExtra("documentId", id).putExtra("title", title).putExtra("equipmentId", eid))
    }

    private fun showPassportPdf() {
        lifecycleScope.launch {
            try {
                val passports = documents.list(eid).documents.filter { it.optString("category").equals("passport", true) }
                if (passports.isEmpty()) {
                    showParameters()
                } else if (passports.size == 1) {
                    openDocument(passports[0].getString("id"), passports[0].getString("originalName"))
                } else {
                    AlertDialog.Builder(this@EquipmentActivity).setTitle("Паспорта PDF")
                        .setItems(passports.map { it.getString("originalName") }.toTypedArray()) { _, index ->
                            openDocument(passports[index].getString("id"), passports[index].getString("originalName"))
                        }.setNegativeButton("Закрыть", null).show()
                }
            } catch (cancel: CancellationException) { throw cancel }
            catch (error: Exception) { freshness.show(error.message ?: "Не удалось открыть паспорт", Ui.Tone.Error) }
        }
    }

    private fun showDocuments(preferInstruction: Boolean = false) {
        lifecycleScope.launch {
            try {
                val listing = documents.list(eid)
                var docs = listing.documents
                if (preferInstruction) docs = docs.sortedByDescending { it.optString("category").equals("instruction", true) }
                if (docs.isEmpty()) {
                    AlertDialog.Builder(this@EquipmentActivity).setTitle("Документы")
                        .setMessage(if (listing.fromCache) "Без сети доступны только сохранённые документы. Для этого оборудования их пока нет." else "К оборудованию ещё не прикреплены документы.")
                        .setPositiveButton("Закрыть", null).show(); return@launch
                }
                val names = docs.map { d ->
                    val saved = documents.isSaved(d.getString("id"))
                    d.getString("originalName") + if (saved) "  · есть без сети" else ""
                }
                AlertDialog.Builder(this@EquipmentActivity)
                    .setTitle(if (listing.fromCache) "Сохранённые документы (без сети)" else "Паспорт и инструкции")
                    .setItems(names.toTypedArray()) { _, i -> openDocument(docs[i].getString("id"), docs[i].getString("originalName")) }
                    .setNegativeButton("Закрыть", null).show()
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                freshness.show("Не удалось получить список документов: нет связи, а сохранённых копий нет.", Ui.Tone.Error)
            }
        }
    }

    private fun showParameters() {
        val params = card?.optJSONObject("passport")?.optJSONObject("parameters")
        val description = params?.keys()?.asSequence()?.joinToString("\n\n") { "$it: ${params.optString(it)}" } ?: "Параметры ещё не добавлены"
        AlertDialog.Builder(this).setTitle("Паспортные параметры").setMessage(description).setPositiveButton("Закрыть", null).show()
    }

    private fun showHelp() {
        AlertDialog.Builder(this).setTitle("Работа с оборудованием").setMessage(
            "1. Сверьте модель и заводской номер в паспорте.\n\n" +
                "2. Выберите период графиков. Пунктир — нормативы, красные точки — превышения.\n\n" +
                "3. Если появилась плашка «Данные устарели», значения не обновляются: проверьте сеть.\n\n" +
                "4. При перегреве выполните рекомендацию и откройте инструкцию.\n\n" +
                "5. «Осмотр и фото»: снимите узел, проверьте рамки дефектов, подтвердите результат и сохраните осмотр. Без сети осмотр сохранится на устройстве."
        ).setPositiveButton("Понятно", null).show()
    }

    override fun onSaveInstanceState(outState: Bundle) { super.onSaveInstanceState(outState); outState.putInt("hours", hours) }
}
