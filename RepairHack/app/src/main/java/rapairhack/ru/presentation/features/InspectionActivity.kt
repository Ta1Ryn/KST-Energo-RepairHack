package rapairhack.ru.presentation.features

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import rapairhack.ru.R
import rapairhack.ru.data.ApiError
import rapairhack.ru.data.BackendApi
import rapairhack.ru.data.isNetworkFailure
import rapairhack.ru.data.local.AppDatabase
import rapairhack.ru.data.local.CachedEquipment
import rapairhack.ru.data.local.InspectionDraft
import rapairhack.ru.data.sync.Connectivity
import rapairhack.ru.data.sync.InspectionPayload
import rapairhack.ru.data.sync.SyncWorker
import rapairhack.ru.presentation.ui.Ui
import rapairhack.ru.presentation.ui.Ui.color
import rapairhack.ru.presentation.ui.Ui.dp
import rapairhack.ru.presentation.ui.Ui.show
import java.io.File
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.util.UUID

class InspectionActivity : TechnicalActivity() {
    private val api by lazy { BackendApi(this) }
    private val db by lazy { AppDatabase.get(this) }
    private val equipmentId get() = intent.getStringExtra("equipmentId")!!

    private lateinit var header: Ui.Header
    private lateinit var networkBanner: TextView
    private lateinit var node: Spinner
    private lateinit var action: Spinner
    private lateinit var comment: EditText
    private lateinit var photo: DetectionPhotoView
    private lateinit var stepper: AnalysisStepper
    private lateinit var stageDetail: TextView
    private lateinit var analyze: MaterialButton
    private lateinit var retry: MaterialButton
    private lateinit var resultCard: LinearLayout
    private lateinit var resultText: TextView
    private lateinit var decisionNote: TextView
    private lateinit var confirm: MaterialButton
    private lateinit var reject: MaterialButton
    private lateinit var correct: MaterialButton
    private lateinit var save: MaterialButton
    private lateinit var draftNote: TextView
    private lateinit var photoButtons: List<MaterialButton>

    private var card: JSONObject? = null
    private var draft: InspectionDraft? = null
    private var cameraFile: File? = null
    private var analysisJob: Job? = null
    private var saving = false
    private var online = true
    private var restoring = false

    private val pick = registerForActivityResult(ActivityResultContracts.GetContent()) { uri -> if (uri != null) loadPhoto(uri) }
    private val capture = registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        cameraFile?.let { file -> if (ok) loadPhoto(Uri.fromFile(file)) else file.delete() }
    }

    private val analysis get() = draft?.analysisJson?.let { JSONObject(it) }
    private val analyzing get() = analysisJob?.isActive == true

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        cameraFile = state?.getString("cameraFile")?.let(::File)
        buildLayout()
        lifecycleScope.launch { Connectivity.observe(this@InspectionActivity).collect { online = it; renderNetwork() } }
        lifecycleScope.launch {
            loadDraftAndCard()
            intent.getStringExtra("livePhotoPath")?.let { path ->
                val file = File(path)
                val folder = File(cacheDir, "live-captures").canonicalFile
                if (file.isFile && file.canonicalFile.parentFile == folder) loadPhoto(Uri.fromFile(file))
                intent.removeExtra("livePhotoPath")
            }
        }
    }

    // ---------- Разметка: слева фото, справа анализ и форма ----------
    private fun buildLayout() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setBackgroundColor(color(R.color.bg))
            setPadding(dp(20), dp(12), dp(20), dp(12))
        }
        header = Ui.header(this, "Осмотр оборудования") { finish() }
        root.addView(header.view)
        networkBanner = Ui.banner(this)
        root.addView(networkBanner, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })

        val panes = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        root.addView(panes, LinearLayout.LayoutParams(-1, 0, 1f).apply { topMargin = dp(12) })

        // Левая колонка: фото и действия с ним.
        val left = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        photo = DetectionPhotoView(this).apply { background = Ui.surface(context) ; clipToOutline = true }
        left.addView(photo, LinearLayout.LayoutParams(-1, 0, 1f))
        val camera = Ui.primary(this, "Сделать фото") { openCamera() }
        val gallery = Ui.secondary(this, "Из галереи") { pick.launch("image/*") }
        val cases = Ui.secondary(this, "Фото из кейса") { chooseCasePhoto() }
        val rotate = Ui.secondary(this, "Повернуть") { photo.rotateClockwise() }.apply {
            contentDescription = "Повернуть фото на 90 градусов"
        }
        photoButtons = listOf(camera, gallery, cases)
        left.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            listOf(camera, gallery, cases, rotate).forEachIndexed { i, b ->
                addView(b, LinearLayout.LayoutParams(0, -2, 1f).apply { if (i > 0) marginStart = dp(8) })
            }
        }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        left.addView(Ui.text(this, 13f, R.color.text_secondary).apply {
            text = "Двойное касание или два пальца — увеличить. Контуры и отметки сохраняются при повороте и масштабе."
        })
        panes.addView(left, LinearLayout.LayoutParams(0, -1, 0.55f))

        // Правая колонка: узел, анализ, решение, форма.
        val right = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), 0, 0, 0) }
        panes.addView(ScrollView(this).apply { addView(right); isFillViewport = true }, LinearLayout.LayoutParams(0, -1, 0.45f))

        right.addView(label("Узел"))
        node = Spinner(this); right.addView(node, LinearLayout.LayoutParams(-1, dp(52)))

        val analysisCard = Ui.card(this)
        analysisCard.addView(Ui.text(this, 17f, bold = true).apply { text = "Проверка фото" })
        stepper = AnalysisStepper(this)
        analysisCard.addView(stepper, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        stageDetail = Ui.banner(this)
        analysisCard.addView(stageDetail, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6) })
        analyze = Ui.primary(this, "Проверить фото") { analyzePhoto() }
        retry = Ui.secondary(this, "Повторить проверку") { analyzePhoto() }.apply { visibility = View.GONE }
        analysisCard.addView(analyze, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        analysisCard.addView(retry, LinearLayout.LayoutParams(-1, -2))
        right.addView(analysisCard, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(12) })

        resultCard = Ui.card(this).apply { visibility = View.GONE }
        resultCard.addView(Ui.text(this, 17f, bold = true).apply { text = "Результат анализа" })
        resultText = Ui.text(this, 15f).apply { setPadding(0, dp(6), 0, dp(6)) }
        resultCard.addView(resultText)
        decisionNote = Ui.banner(this)
        resultCard.addView(decisionNote, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(6) })
        confirm = Ui.primary(this, "Подтвердить") { decide("confirmed") }
        reject = Ui.secondary(this, "Отклонить") { decide("rejected") }
        correct = Ui.secondary(this, "Исправить") { correctResult() }
        resultCard.addView(LinearLayout(this).apply {
            listOf(confirm, reject, correct).forEachIndexed { i, b -> addView(b, LinearLayout.LayoutParams(0, -2, 1f).apply { if (i > 0) marginStart = dp(8) }) }
        })
        right.addView(resultCard, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(12) })

        right.addView(label("Результат осмотра"))
        action = Spinner(this).apply {
            adapter = ArrayAdapter(this@InspectionActivity, android.R.layout.simple_spinner_dropdown_item, InspectionPayload.actions)
        }
        right.addView(action, LinearLayout.LayoutParams(-1, dp(52)))
        comment = EditText(this).apply { hint = "Замечание инженера"; minLines = 2; textSize = 16f }
        right.addView(comment, LinearLayout.LayoutParams(-1, -2))
        save = Ui.primary(this, "Сохранить осмотр") { saveInspection() }
        right.addView(save, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        draftNote = Ui.text(this, 13f, R.color.text_secondary)
        right.addView(draftNote)

        setContentView(root)
        action.onItemSelectedListener = simpleSelection { position ->
            if (position != draft?.actionIndex) persist { copy(actionIndex = position, decision = if (analysisJson != null) "corrected" else decision) }
        }
        comment.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) { persist { copy(comment = s?.toString().orEmpty()) } }
        })
        renderControls()
    }

    private fun label(text: String) = Ui.text(this, 14f, R.color.text_secondary).apply { this.text = text; setPadding(0, dp(12), 0, dp(2)) }
    private fun simpleSelection(onSelect: (Int) -> Unit) = object : AdapterView.OnItemSelectedListener {
        override fun onNothingSelected(parent: AdapterView<*>?) {}
        override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) { if (!restoring) onSelect(position) }
    }

    // ---------- Черновик и карточка оборудования ----------
    private suspend fun loadDraftAndCard() {
        val existing = intent.getStringExtra("draftId")?.let { db.drafts().get(it) } ?: db.drafts().openDraft(equipmentId)
        draft = existing ?: InspectionDraft(id = UUID.randomUUID().toString(), equipmentId = equipmentId)
        restoring = true
        draft!!.let { d ->
            action.setSelection(d.actionIndex)
            comment.setText(d.comment)
            d.photoPath?.let { path -> withContext(Dispatchers.IO) { BitmapFactory.decodeFile(path) } }?.let { photo.bitmap = it }
            if (d.analysisJson != null) stepper.set(AnalysisStepper.Stage.Done)
        }
        restoring = false
        if (existing != null) draftNote.text = "Черновик восстановлен с устройства"

        card = try {
            api.json("/api/equipment/$equipmentId").also {
                db.cache().putEquipment(CachedEquipment(equipmentId, it.toString(), db.cache().equipment(equipmentId)?.documentsJson))
            }
        } catch (e: CancellationException) { throw e } catch (e: Exception) {
            if (!e.isNetworkFailure()) {
                networkBanner.show(e.message ?: "Оборудование недоступно", Ui.Tone.Error)
                renderControls()
                return
            }
            db.cache().equipment(equipmentId)?.let { cached ->
                networkBanner.show("Нет связи с сервером. Паспорт из сохранённой копии, осмотр сохранится на устройстве.", Ui.Tone.Stale)
                JSONObject(cached.json)
            } ?: run {
                networkBanner.show("Нет связи, а паспорт этого оборудования ещё не сохранялся. Откройте его один раз при наличии сети.", Ui.Tone.Error)
                null
            }
        }
        card?.let { c ->
            header.title.text = c.optString("name", "Осмотр")
            header.subtitle.text = "Осмотр · ${c.optString("model")} · № ${c.optString("serialNumber")}"
            header.subtitle.visibility = View.VISIBLE
            val components = c.getJSONArray("components")
            node.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
                (0 until components.length()).map { components.getJSONObject(it).getString("name") })
            restoring = true
            node.setSelection(draft!!.componentIndex.coerceIn(0, maxOf(0, components.length() - 1)))
            restoring = false
            node.onItemSelectedListener = simpleSelection { position ->
                // Первое событие Spinner после восстановления не означает смену узла.
                if (position == draft?.componentIndex) return@simpleSelection
                analysisJob?.cancel()
                stepper.set(AnalysisStepper.Stage.Idle); stageDetail.visibility = View.GONE
                persist { copy(componentIndex = position, analysisJson = null, decision = null) }
            }
            persist { copy(equipmentName = c.optString("name")) }
        }
        renderResult(); renderControls()
    }

    /** Сохраняет черновик локально после каждого изменения. */
    private fun persist(change: InspectionDraft.() -> InspectionDraft) {
        val current = draft ?: return
        if (current.status != InspectionDraft.STATUS_DRAFT) return
        val component = componentOrNull()
        val next = current.change().copy(
            componentId = component?.optString("id") ?: current.componentId,
            installationId = component?.optJSONObject("current")?.optString("id") ?: current.installationId,
            updatedAt = System.currentTimeMillis()
        )
        draft = next
        lifecycleScope.launch {
            db.drafts().upsert(next)
            draftNote.text = "Черновик сохранён на устройстве в ${LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm:ss"))}"
        }
        renderResult(); renderControls()
    }

    private fun componentOrNull(): JSONObject? {
        val components = card?.optJSONArray("components") ?: return null
        val index = if (::node.isInitialized && node.adapter != null) node.selectedItemPosition else draft?.componentIndex ?: 0
        return if (index in 0 until components.length()) components.getJSONObject(index) else null
    }

    // ---------- Фото ----------
    private fun openCamera() {
        val directory = File(cacheDir, "camera").apply { mkdirs() }
        val file = File.createTempFile("capture-", ".jpg", directory)
        cameraFile = file
        runCatching { capture.launch(FileProvider.getUriForFile(this, "$packageName.fileprovider", file)) }
            .onFailure { stageDetail.show("Не удалось открыть камеру. Выберите фото из галереи.", Ui.Tone.Error) }
    }

    private fun chooseCasePhoto() {
        val files = assets.list("case_photos")?.filter { it.endsWith(".jpg") }?.sorted() ?: emptyList()
        AlertDialog.Builder(this).setTitle("Фотографии насосов из кейса").setItems(files.toTypedArray()) { _, which ->
            val file = File(cacheDir, files[which]); assets.open("case_photos/" + files[which]).use { input -> file.outputStream().use { input.copyTo(it) } }
            loadPhoto(Uri.fromFile(file))
        }.setNegativeButton("Закрыть", null).show()
    }

    /** Поворачивает снимок по EXIF и сохраняет его во внутреннее хранилище черновиков. */
    private fun loadPhoto(uri: Uri) {
        if (analyzing || saving) return
        lifecycleScope.launch {
            try {
                val stored = withContext(Dispatchers.IO) {
                    val bytes = contentResolver.openInputStream(uri)?.use { stream ->
                        val limit = 20 * 1024 * 1024
                        val output = java.io.ByteArrayOutputStream()
                        val buffer = ByteArray(8192)
                        while (true) {
                            val count = stream.read(buffer)
                            if (count < 0) break
                            require(output.size() + count <= limit) { "Фото больше 20 МБ. Выберите снимок меньшего размера." }
                            output.write(buffer, 0, count)
                        }
                        output.toByteArray()
                    } ?: error("Не удалось прочитать фото.")
                    val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
                    require(options.outWidth > 0 && options.outHeight > 0) { "Файл не похож на изображение." }
                    options.inJustDecodeBounds = false; options.inSampleSize = maxOf(1, maxOf(options.outWidth, options.outHeight) / 1600)
                    val image = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options) ?: error("Не удалось прочитать фото.")
                    val orientation = runCatching { ExifInterface(bytes.inputStream()).getAttributeInt(ExifInterface.TAG_ORIENTATION, 1) }.getOrDefault(1)
                    val matrix = Matrix()
                    when (orientation) {
                        2 -> matrix.setScale(-1f, 1f); 3 -> matrix.setRotate(180f); 4 -> matrix.setScale(1f, -1f)
                        5 -> { matrix.setRotate(90f); matrix.postScale(-1f, 1f) }; 6 -> matrix.setRotate(90f)
                        7 -> { matrix.setRotate(-90f); matrix.postScale(-1f, 1f) }; 8 -> matrix.setRotate(-90f)
                    }
                    val normalized = Bitmap.createBitmap(image, 0, 0, image.width, image.height, matrix, true)
                    val directory = File(filesDir, "drafts").apply { mkdirs() }
                    val destination = File(directory, "${UUID.randomUUID()}.jpg")
                    destination.outputStream().use { normalized.compress(Bitmap.CompressFormat.JPEG, 88, it) }
                    destination
                }
                cameraFile?.takeIf { it.path == uri.path }?.delete()
                draft?.photoPath?.let { old -> if (old != stored.path) File(old).delete() }
                photo.bitmap = BitmapFactory.decodeFile(stored.path)
                stepper.set(AnalysisStepper.Stage.Idle); stageDetail.visibility = View.GONE
                persist { copy(photoPath = stored.path, photoClientId = UUID.randomUUID().toString(), photoServerId = null, analysisJson = null, decision = null) }
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                stageDetail.show(e.message ?: "Ошибка чтения фото.", Ui.Tone.Error)
            }
        }
    }

    // ---------- Анализ: Загрузка → В очереди → Анализ → Готово ----------
    private fun analyzePhoto() {
        // Защита от повторных нажатий: пока задача идёт, новая не запускается.
        if (analyzing || saving) return
        val d = draft ?: return
        val component = componentOrNull() ?: return
        if (d.photoPath == null) return
        retry.visibility = View.GONE
        if (!Connectivity.isOnline(this)) {
            stepper.set(AnalysisStepper.Stage.Upload)
            failAnalysis("Нет сети. Фото сохранено в черновике. Проверку можно запустить, когда появится связь, а осмотр — сохранить уже сейчас.")
            return
        }
        analysisJob = lifecycleScope.launch {
            renderControls()
            try {
                stepper.set(AnalysisStepper.Stage.Upload); stageDetail.show("Отправляем фото на сервер…", Ui.Tone.Info)
                var photoId = draft!!.photoServerId
                if (photoId == null) {
                    val bytes = withContext(Dispatchers.IO) { File(d.photoPath).readBytes() }
                    photoId = api.uploadPhoto(bytes, d.photoClientId ?: d.id).getString("id")
                    persist { copy(photoServerId = photoId) }
                }
                stepper.set(AnalysisStepper.Stage.Queue); stageDetail.show("Ставим фото в очередь на анализ…", Ui.Tone.Info)
                // Один и тот же ключ для пары «фото + узел»: повторный запрос вернёт ту же задачу.
                val clientRequestId = "${d.photoClientId ?: d.id}:${component.getString("id")}".take(64)
                val body = JSONObject().put("photoId", photoId)
                    .put("installationId", component.getJSONObject("current").getString("id"))
                    .put("clientRequestId", clientRequestId)
                val base = "/api/equipment/$equipmentId/components/${component.getString("id")}/analyses"
                val result = try {
                    val job = api.json("$base/jobs", "POST", body)
                    pollJob(job)
                } catch (e: ApiError) {
                    if (e.status != 404 && e.status != 405) throw e
                    // Старый сервер без очереди: синхронный запрос.
                    stepper.set(AnalysisStepper.Stage.Analyze); stageDetail.show("Нейросеть проверяет фото…", Ui.Tone.Info)
                    api.json(base, "POST", body)
                }
                stepper.set(AnalysisStepper.Stage.Done)
                stageDetail.show("Готово. Проверьте отмеченные области на фото и подтвердите, отклоните или исправьте результат.", Ui.Tone.Ok)
                persist { copy(analysisJson = result.toString(), decision = null) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                failAnalysis(explain(e))
            } finally {
                if (analysisJob === coroutineContext[Job]) analysisJob = null
                renderControls()
            }
        }
        renderControls()
    }

    /** Опрашивает задачу, пока она в очереди или в работе. Кратковременные обрывы сети переживает. */
    private suspend fun pollJob(initial: JSONObject): JSONObject {
        var job = initial
        var networkMisses = 0
        while (true) {
            when (job.optString("status")) {
                "queued" -> {
                    stepper.set(AnalysisStepper.Stage.Queue)
                    val position = job.optInt("position")
                    val attempt = job.optInt("attempts")
                    stageDetail.show(when {
                        attempt > 0 -> "Сервис анализа занят, повторим автоматически (попытка ${attempt + 1}). Ничего нажимать не нужно."
                        position > 0 -> "В очереди: перед вами $position фото. Ничего нажимать не нужно."
                        else -> "В очереди, анализ начнётся через несколько секунд."
                    }, Ui.Tone.Info)
                }
                "running" -> { stepper.set(AnalysisStepper.Stage.Analyze); stageDetail.show("Нейросеть проверяет фото…", Ui.Tone.Info) }
                "done" -> return job.getJSONObject("analysis")
                "failed" -> throw ApiError(502, job.optString("error").ifBlank { "Не удалось проверить фото." })
            }
            delay(1500)
            job = try {
                api.json("/api/analysis-jobs/${initial.getString("id")}").also { networkMisses = 0 }
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                if (e is ApiError && e.status == 404) throw ApiError(410, "Сервер перезапустился и потерял задачу. Нажмите «Повторить проверку».")
                if (!e.isNetworkFailure() || ++networkMisses > 20) throw e
                stageDetail.show("Связь прервалась, ждём ответа сервера…", Ui.Tone.Stale)
                job
            }
        }
    }

    private fun explain(e: Exception): String = when {
        e.isNetworkFailure() -> "Нет связи с сервером. Фото сохранено в черновике. Повторите проверку, когда появится сеть."
        e is ApiError && e.status == 503 -> "Сервис анализа сейчас недоступен. Повторите проверку через минуту."
        e is ApiError && e.status == 504 -> "Сервис анализа не успел ответить. Повторите проверку."
        e is ApiError && e.status == 413 -> "Фото слишком большое для сервера. Сделайте снимок заново."
        e is ApiError && e.status == 415 -> "Формат фото не поддерживается. Нужен JPEG, PNG или WebP."
        e is ApiError && e.status == 401 -> "Сессия истекла. Войдите по PIN заново, черновик сохранится."
        else -> e.message ?: "Не удалось проверить фото."
    }

    private fun failAnalysis(reason: String) {
        stepper.fail(reason)
        stageDetail.show(reason, Ui.Tone.Error)
        retry.visibility = View.VISIBLE
    }

    // ---------- Решение инженера по результату ----------
    private fun decide(decision: String) {
        val a = analysis ?: return
        val index = when (decision) {
            "rejected" -> 0
            else -> if (a.optString("visualStatus").equals("noVisibleProblems", true)) 0 else suggestedAction(a)
        }
        restoring = true; action.setSelection(index); restoring = false
        persist { copy(decision = decision, actionIndex = index) }
    }

    private fun suggestedAction(a: JSONObject): Int {
        val labels = buildString {
            val detections = a.optJSONArray("detections") ?: JSONArray()
            for (i in 0 until detections.length()) append(detections.getJSONObject(i).optString("label").lowercase()).append(' ')
            append(a.optString("summary").substringBefore(" Проверка:").lowercase())
        }
        return when {
            "масл" in labels || "течь" in labels || "oil" in labels -> 1
            "смещ" in labels || "displace" in labels -> 2
            "корроз" in labels || "ржав" in labels || "corros" in labels -> 3
            "геометр" in labels || "деформ" in labels || "deform" in labels -> 4
            else -> draft?.actionIndex ?: 0
        }
    }

    private fun correctResult() {
        if (analysis == null) return
        var chosen = action.selectedItemPosition
        AlertDialog.Builder(this).setTitle("Исправить результат")
            .setSingleChoiceItems(InspectionPayload.actions.toTypedArray(), chosen) { _, which -> chosen = which }
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Сохранить исправление") { _, _ ->
                restoring = true; action.setSelection(chosen); restoring = false
                persist { copy(decision = "corrected", actionIndex = chosen) }
                comment.requestFocus()
            }.show()
    }

    private fun renderResult() {
        val a = analysis
        photo.detections = a?.optJSONArray("detections") ?: JSONArray()
        photo.dimmed = draft?.decision == "rejected"
        resultCard.visibility = if (a == null) View.GONE else View.VISIBLE
        if (a == null) return
        val confidence = a.optDouble("confidence", Double.NaN).takeIf { it.isFinite() }?.let { "\nУверенность модели: ${(it.coerceIn(0.0, 1.0) * 100).toInt()}%" } ?: ""
        // Архитектуры и версии моделей доступны в сравнении; в осмотре важны сами признаки.
        val summary = a.optString("summary").substringBefore(" Проверка:")
        resultText.text = summary + "\n\n" + a.optString("recommendation") + confidence
        when (draft?.decision) {
            "confirmed" -> decisionNote.show("Подтверждено. Результат попадёт в осмотр: «${InspectionPayload.actions[action.selectedItemPosition]}».", Ui.Tone.Ok)
            "rejected" -> decisionNote.show("Отклонено. Результат ИИ не попадёт в отчёт, осмотр сохранится как «Проверено».", Ui.Tone.Stale)
            "corrected" -> decisionNote.show("Исправлено на «${InspectionPayload.actions[action.selectedItemPosition]}». Опишите причину в замечании.", Ui.Tone.Warn)
            else -> decisionNote.show("ИИ может ошибаться. Сверьте отмеченные области с узлом и выберите действие.", Ui.Tone.Info)
        }
    }

    private fun renderControls() {
        val ready = card != null && draft != null
        val busy = analyzing || saving
        photoButtons.forEach { it.isEnabled = !busy }
        if (::node.isInitialized) node.isEnabled = !busy && ready
        action.isEnabled = !busy; comment.isEnabled = !saving
        analyze.isEnabled = !busy && ready && draft?.photoPath != null
        analyze.text = when {
            analyzing -> "Идёт проверка…"
            draft?.photoPath == null -> "Сначала добавьте фото"
            draft?.analysisJson != null -> "Проверить заново"
            else -> "Проверить фото"
        }
        retry.isEnabled = !busy
        listOf(confirm, reject, correct).forEach { it.isEnabled = !busy }
        save.isEnabled = !busy && ready
        save.text = if (saving) "Сохраняем…" else if (online) "Сохранить осмотр" else "Сохранить на устройстве"
    }

    private fun renderNetwork() {
        if (!online) networkBanner.show("Нет сети. Осмотр и фото сохраняются на устройстве и отправятся сами, когда связь вернётся.", Ui.Tone.Stale)
        else if (card != null) networkBanner.visibility = View.GONE
        renderControls()
    }

    // ---------- Сохранение осмотра ----------
    private fun saveInspection() {
        if (saving || analyzing) return
        val d = draft ?: return
        if (componentOrNull() == null) return
        if (analysis != null && d.decision == null) {
            AlertDialog.Builder(this).setTitle("Результат анализа не проверен")
                .setMessage("Подтвердите, отклоните или исправьте результат ИИ перед сохранением.")
                .setPositiveButton("Понятно", null).show()
            return
        }
        saving = true; renderControls()
        lifecycleScope.launch {
            val pending = d.copy(status = InspectionDraft.STATUS_PENDING, performedAt = d.performedAt ?: java.time.Instant.now().toString(),
                comment = comment.text.toString(), actionIndex = action.selectedItemPosition, updatedAt = System.currentTimeMillis())
            db.drafts().upsert(pending); draft = pending
            try {
                if (!Connectivity.isOnline(this@InspectionActivity)) throw java.io.IOException("offline")
                InspectionPayload.send(this@InspectionActivity, api, pending)
                pending.photoPath?.let { File(it).delete() }
                done("Осмотр сохранён", "Фото и результат осмотра отправлены на сервер. PDF доступен в отчётах.")
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                if (e.isNetworkFailure() || (e is ApiError && e.status >= 500)) {
                    SyncWorker.enqueue(this@InspectionActivity)
                    done("Сохранено на устройстве", "Связи с сервером нет. Осмотр и фото отправятся автоматически, когда появится сеть. Повторная отправка не создаст дубликат.")
                } else {
                    // Сервер отклонил данные: возвращаем черновик в редактирование.
                    val back = pending.copy(status = InspectionDraft.STATUS_DRAFT); db.drafts().upsert(back); draft = back
                    saving = false; renderControls()
                    stageDetail.show("Осмотр не сохранён: ${explain(e)}", Ui.Tone.Error)
                }
            }
        }
    }

    private fun done(title: String, message: String) {
        AlertDialog.Builder(this).setTitle(title).setMessage(message).setCancelable(false)
            .setPositiveButton("Готово") { _, _ -> finish() }.show()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("cameraFile", cameraFile?.path)
    }
}
