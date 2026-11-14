package rapairhack.ru.presentation.features

import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import rapairhack.ru.data.BackendApi
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.view.isVisible
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.Fragment
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.snackbar.Snackbar
import rapairhack.ru.R
import rapairhack.ru.databinding.FragmentFeatureBinding

class FeatureFragment : Fragment() {

    private var _binding: FragmentFeatureBinding? = null
    private val binding get() = _binding!!
    private val listAdapter = FeatureListAdapter()
    private var allItems: List<FeatureListItem> = emptyList()
    private var currentSection: Section? = null
    private var assistantAnswer: String? = null
    private var reportSummary: ReportSummary? = null

    private val api by lazy { BackendApi(requireContext()) }
    private val records = mutableMapOf<String, JSONObject>()
    private var pendingPdf: ByteArray? = null
    private val pdfLauncher = registerForActivityResult(ActivityResultContracts.CreateDocument("application/pdf")) { uri ->
        val bytes = pendingPdf
        pendingPdf = null
        if (uri != null && bytes != null && _binding != null) viewLifecycleOwner.lifecycleScope.launch {
            runCatching { withContext(Dispatchers.IO) { requireContext().contentResolver.openOutputStream(uri)?.use { it.write(bytes) } ?: error("Не удалось сохранить PDF") } }.onFailure { showError(it) }
        }
    }
    var onAssistantQuestion: ((String) -> Unit)? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentFeatureBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val section = Section.from(requireArguments().getString(ARG_SECTION))
        currentSection = section

        binding.back.setOnClickListener { findNavController().popBackStack() }
        binding.title.setText(section.title)
        binding.description.setText(section.description)
        binding.searchInput.isVisible = section.searchHint != null
        section.searchHint?.let(binding.searchInput::setHint)
        binding.reportsSummary.isVisible = section == Section.REPORTS
        binding.assistantPanel.isVisible = section == Section.ASSISTANT
        binding.listFrame.isVisible = section != Section.ASSISTANT

        binding.featureList.layoutManager = LinearLayoutManager(requireContext())
        binding.featureList.adapter = listAdapter
        binding.emptyState.setText(section.emptyMessage ?: R.string.feature_empty_equipment)
        binding.searchInput.doAfterTextChanged { renderVisibleItems() }
        renderVisibleItems()

        reportSummary?.let(::showReportSummary)
        assistantAnswer?.let { binding.assistantAnswer.text = it }
        binding.askAssistant.setOnClickListener { submitAssistantQuestion() }
        listAdapter.onItemClick = { item ->
            viewLifecycleOwner.lifecycleScope.launch {
                runCatching {
                    if (section == Section.EQUIPMENT) startActivity(android.content.Intent(requireContext(), EquipmentActivity::class.java).putExtra("equipmentId", item.id))
                    else if (item.id.startsWith("draft:")) {
                        AlertDialog.Builder(requireContext()).setTitle("Осмотр ждёт отправки")
                            .setMessage("Осмотр сохранён на устройстве и отправится автоматически, когда появится сеть. Повторная отправка не создаст дубликат.")
                            .setPositiveButton("Отправить сейчас") { _, _ -> rapairhack.ru.data.sync.SyncWorker.enqueue(requireContext()) }
                            .setNegativeButton("Закрыть", null).show()
                    }
                    else {
                        val record = records[item.id] ?: return@runCatching
                        val equipmentId = record.getString("equipmentId")
                        val reportId = if (section == Section.HISTORY) record.getString("inspectionId") else record.getString("id")
                        val report = api.json("/api/equipment/$equipmentId/inspections/$reportId")
                        AlertDialog.Builder(requireContext()).setTitle("Осмотр").setMessage("${report.getString("authorName")}\n${report.optString("comment")}\n${report.getString("performedAt")}").setNegativeButton("Закрыть", null).setPositiveButton("Скачать PDF") { _, _ ->
                            viewLifecycleOwner.lifecycleScope.launch {
                                runCatching { api.bytes("/api/equipment/$equipmentId/inspections/$reportId/pdf") }
                                    .onSuccess { pendingPdf = it; pdfLauncher.launch("inspection-$reportId.pdf") }.onFailure { showError(it) }
                            }
                        }.show()
                    }
                }.onFailure { showError(it) }
            }
        }
        onAssistantQuestion = { question ->
            viewLifecycleOwner.lifecycleScope.launch {
                runCatching { api.json("/api/operations/assistant", "POST", JSONObject().put("question", question)).getString("answer") }
                    .onSuccess { renderAssistantAnswer(it) }.onFailure { showError(it) }
            }
        }
        loadSection(section)
    }

    fun renderItems(items: List<FeatureListItem>) {
        allItems = items.toList()
        if (_binding != null && currentSection != Section.ASSISTANT) {
            renderVisibleItems()
        }
    }

    fun renderReportSummary(reportCount: Int, inspectionCount: Int, openDefectCount: Int) {
        val summary = ReportSummary(reportCount, inspectionCount, openDefectCount)
        reportSummary = summary
        if (_binding != null) showReportSummary(summary)
    }

    fun renderAssistantAnswer(answer: String) {
        assistantAnswer = answer
        _binding?.assistantAnswer?.text = answer
    }

    private fun showError(error: Throwable) { _binding?.let { Snackbar.make(it.root, error.message ?: "Сервер недоступен", Snackbar.LENGTH_LONG).show() } }
    private fun loadSection(section: Section) {
        if (section == Section.ASSISTANT) {
            renderAssistantAnswer("Справочная помощь использует текущие датчики, нормы и инструкции. Для проверки фотографии откройте оборудование и выберите «Осмотр».")
            return
        }
        viewLifecycleOwner.lifecycleScope.launch {
            runCatching {
                val rows = when (section) {
                    Section.EQUIPMENT -> runCatching { api.json("/api/equipment?limit=100").getJSONArray("items") }.getOrElse { error ->
                        // Без сети показываем паспорта, сохранённые на устройстве.
                        val cached = rapairhack.ru.data.local.AppDatabase.get(requireContext()).cache().allEquipment()
                        if (cached.isEmpty()) throw error
                        showError(Exception("Нет сети. Показаны паспорта, сохранённые на устройстве."))
                        JSONArray(cached.map { JSONObject(it.json) })
                    }
                    Section.HISTORY -> JSONArray(api.request("/api/operations/findings"))
                    else -> api.json("/api/operations/reports?limit=100").getJSONArray("items")
                }
                records.clear()
                val items = (0 until rows.length()).map { index ->
                    val row = rows.getJSONObject(index); val id = row.getString("id"); records[id] = row
                    when (section) {
                        Section.EQUIPMENT -> FeatureListItem(id, row.getString("name"), "${row.getString("model")} · № ${row.getString("serialNumber")}", "QR")
                        Section.HISTORY -> FeatureListItem(id, row.getString("title"), row.optString("description"), row.getString("status"))
                        else -> FeatureListItem(id, "Осмотр · ${row.getString("authorName")}", "${row.getString("performedAt")} · ${row.optString("comment")}", "PDF")
                    }
                }
                val local = if (section == Section.REPORTS) rapairhack.ru.data.local.AppDatabase.get(requireContext()).drafts().pending().map { d ->
                    FeatureListItem("draft:${d.id}", "Осмотр · ${d.equipmentName.ifBlank { "оборудование" }}", "Сохранён на устройстве · ждёт отправки", "Ждёт сети")
                } else emptyList()
                renderItems(local + items)
                if (section == Section.REPORTS) {
                    val findings = JSONArray(api.request("/api/operations/findings"))
                    renderReportSummary(rows.length(), rows.length(), (0 until findings.length()).count { findings.getJSONObject(it).getString("status") in listOf("open", "assigned", "in_progress") })
                }
            }.onFailure { showError(it) }
        }
    }

    private fun renderVisibleItems() {
        val query = _binding?.searchInput?.text?.toString()?.trim().orEmpty()
        val filteredItems = if (query.isBlank()) {
            allItems
        } else {
            allItems.filter { item ->
                item.title.contains(query, ignoreCase = true) ||
                    item.subtitle.contains(query, ignoreCase = true) ||
                    item.badge?.contains(query, ignoreCase = true) == true
            }
        }

        listAdapter.submitList(filteredItems)
        binding.featureList.isVisible = filteredItems.isNotEmpty()
        binding.emptyState.isVisible = filteredItems.isEmpty()
        binding.emptyState.setText(
            if (allItems.isNotEmpty() && query.isNotBlank()) {
                R.string.feature_empty_search
            } else {
                currentSection?.emptyMessage ?: R.string.feature_empty_equipment
            }
        )
    }

    private fun submitAssistantQuestion() {
        val question = binding.questionInput.text.toString().trim()
        if (question.isBlank()) {
            Snackbar.make(binding.root, R.string.feature_ai_empty_question, Snackbar.LENGTH_SHORT)
                .show()
            return
        }

        val handler = onAssistantQuestion
        if (handler == null) {
            Snackbar.make(binding.root, R.string.feature_ai_api_not_connected, Snackbar.LENGTH_SHORT)
                .show()
        } else {
            handler(question)
        }
    }

    private fun showReportSummary(summary: ReportSummary) {
        binding.reportCountValue.text = summary.reportCount.toString()
        binding.inspectionCountValue.text = summary.inspectionCount.toString()
        binding.openDefectCountValue.text = summary.openDefectCount.toString()
    }

    override fun onDestroyView() {
        binding.featureList.adapter = null
        _binding = null
        super.onDestroyView()
    }

    private enum class Section(
        val title: Int,
        val description: Int,
        val searchHint: Int? = null,
        val emptyMessage: Int? = null
    ) {
        EQUIPMENT(
            R.string.tile_equipment,
            R.string.feature_equipment_description,
            R.string.feature_equipment_search,
            R.string.feature_empty_equipment
        ),
        HISTORY(
            R.string.tile_history,
            R.string.feature_history_description,
            R.string.feature_history_search,
            R.string.feature_empty_history
        ),
        REPORTS(
            R.string.tile_reporting,
            R.string.feature_reports_description,
            R.string.feature_reports_search,
            R.string.feature_empty_reports
        ),
        ASSISTANT(
            R.string.tile_ai,
            R.string.feature_ai_description
        );

        companion object {
            fun from(value: String?): Section = entries.firstOrNull { it.routeKey == value }
                ?: throw IllegalArgumentException("Feature section argument is missing or unknown")
        }

        val routeKey: String
            get() = when (this) {
                EQUIPMENT -> SECTION_EQUIPMENT
                HISTORY -> SECTION_HISTORY
                REPORTS -> SECTION_REPORTS
                ASSISTANT -> SECTION_ASSISTANT
            }
    }

    private data class ReportSummary(
        val reportCount: Int,
        val inspectionCount: Int,
        val openDefectCount: Int
    )

    companion object {
        const val ARG_SECTION = "section"
        const val SECTION_EQUIPMENT = "equipment"
        const val SECTION_HISTORY = "history"
        const val SECTION_REPORTS = "reports"
        const val SECTION_ASSISTANT = "assistant"
    }
}
