package rapairhack.ru.presentation.admin

import android.os.Bundle
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.core.view.isVisible
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import rapairhack.ru.R
import rapairhack.ru.data.BackendApi
import rapairhack.ru.data.isNetworkFailure
import rapairhack.ru.databinding.FragmentAdminBinding
import rapairhack.ru.presentation.ui.Ui
import rapairhack.ru.presentation.ui.Ui.dp
import rapairhack.ru.presentation.ui.Ui.show
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Админка: поиск сотрудников и оборудования, назначение доступа,
 * блокировка и сброс PIN с подтверждением. Итог каждой операции показывается плашкой.
 */
class AdminFragment : Fragment() {
    private var _binding: FragmentAdminBinding? = null
    private val binding get() = _binding!!
    private val api by lazy { BackendApi(requireContext()) }
    private var export = ""
    private var employees: List<JSONObject> = emptyList()
    private var equipment: List<JSONObject> = emptyList()
    private var access: List<JSONObject> = emptyList()
    private var busy = false

    private val exportLauncher = registerForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if (uri != null && _binding != null) viewLifecycleOwner.lifecycleScope.launch {
            runCatching { withContext(Dispatchers.IO) { requireContext().contentResolver.openOutputStream(uri)?.use { it.write(export.toByteArray(Charsets.UTF_8)) } ?: error("Не удалось сохранить файл.") } }
                .onSuccess { result("Выгрузка сохранена: сотрудники и журнал действий.", true) }
                .onFailure { result("Выгрузка не сохранена: ${reason(it)}", false) }
        }
    }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentAdminBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        binding.adminPin.hint = "Новый PIN: 4 цифры"
        binding.addEmployee.setOnClickListener { employeeForm() }
        binding.employeeSearch.doAfterTextChanged { renderEmployees() }
        binding.equipmentSearch.doAfterTextChanged { renderEquipment() }
        binding.saveAdminPin.setOnClickListener { changeOwnPin() }
        binding.exportData.setOnClickListener {
            run("Выгрузка") {
                export = JSONObject().put("employees", JSONArray(api.request("/api/admin/employees"))).put("audit", JSONArray(api.request("/api/admin/audit"))).toString(2)
                exportLauncher.launch("kst-admin.json"); null
            }
        }
        binding.logout.setOnClickListener {
            viewLifecycleOwner.lifecycleScope.launch {
                runCatching { api.logout() }
                if (_binding != null) findNavController().navigate(R.id.action_adminFragment_to_loginFragment)
            }
        }
        refresh()
    }

    // ---------- Данные ----------
    private fun refresh() {
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                employees = JSONArray(api.request("/api/admin/employees")).list()
                access = runCatching { JSONArray(api.request("/api/admin/access")).list() }.getOrDefault(emptyList())
                equipment = runCatching { api.json("/api/equipment?limit=100").getJSONArray("items").list() }.getOrDefault(emptyList())
                renderEmployees(); renderEquipment()
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                result("Не удалось загрузить данные: ${reason(e)}", false)
            }
        }
    }

    private fun JSONArray.list() = (0 until length()).map { getJSONObject(it) }
    private fun norm(s: String) = s.lowercase(Locale("ru")).trim()
    private fun isAdmin(e: JSONObject) = e.optJSONArray("roles")?.toString()?.contains("\"admin\"") == true
    private fun activeAccess(employeeId: String) = access.filter { it.optString("employeeId") == employeeId && it.optBoolean("active") }
    private fun statusLabel(e: JSONObject) = mapOf("active" to "Активен", "suspended" to "Заблокирован", "terminated" to "Уволен")[e.optString("employmentStatus")] ?: e.optString("employmentStatus")
    private fun name(e: JSONObject) = e.optString("fullName")

    // ---------- Сотрудники ----------
    private fun renderEmployees() {
        val b = _binding ?: return
        val query = norm(b.employeeSearch.text.toString())
        val visible = employees.filter { e ->
            query.isBlank() || listOf(name(e), e.optString("positionTitle"), statusLabel(e))
                .plus(activeAccess(e.optString("id")).map { it.optString("targetName") })
                .any { norm(it).contains(query) }
        }
        b.employeesTitle.text = if (query.isBlank()) "Сотрудники: ${employees.size}" else "Найдено: ${visible.size} из ${employees.size}"
        b.employeeList.removeAllViews()
        b.emptyEmployees.isVisible = visible.isEmpty()
        b.emptyEmployees.text = if (employees.isEmpty()) getString(R.string.admin_empty_employees) else "Никого не нашли по запросу «${b.employeeSearch.text}». Проверьте написание."
        for (employee in visible) {
            val row = layoutInflater.inflate(R.layout.item_employee_admin, b.employeeList, false)
            val admin = isAdmin(employee)
            val status = employee.optString("employmentStatus")
            val role = employee.optString("positionTitle").takeIf { it.isNotBlank() && it != "null" } ?: if (admin) "Администратор" else "Инженер"
            val grants = activeAccess(employee.optString("id")).map { it.optString("targetName") }
            row.findViewById<TextView>(R.id.employeeName).text = name(employee)
            row.findViewById<TextView>(R.id.employeePin).text = buildString {
                append("$role · ${statusLabel(employee)}")
                if (!admin) append("\nДоступ: ${if (grants.isEmpty()) "не назначен" else grants.joinToString(", ")}")
            }
            row.findViewById<View>(R.id.editEmployee).isVisible = !admin
            row.findViewById<View>(R.id.resetPin).isVisible = !admin && status != "terminated"
            row.findViewById<View>(R.id.employeeAccess).isVisible = !admin && status == "active"
            val block = row.findViewById<MaterialButton>(R.id.deleteEmployee)
            block.isVisible = !admin && status != "terminated"
            block.text = if (status == "active") "Заблокировать" else "Разблокировать"
            row.findViewById<View>(R.id.employeeAccess).setOnClickListener { accessForEmployee(employee) }
            row.findViewById<View>(R.id.editEmployee).setOnClickListener { employeeForm(employee) }
            row.findViewById<View>(R.id.resetPin).setOnClickListener { resetPin(employee) }
            block.setOnClickListener { if (status == "active") blockEmployee(employee) else unblockEmployee(employee) }
            b.employeeList.addView(row)
        }
    }

    private fun blockEmployee(employee: JSONObject) {
        AlertDialog.Builder(requireContext()).setTitle("Заблокировать ${name(employee)}?")
            .setMessage("Сотрудник не сможет войти по PIN, пока вы его не разблокируете. Назначенный доступ к оборудованию сохранится. Действие попадёт в журнал.")
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Заблокировать") { _, _ ->
                run("Блокировка") {
                    val body = JSONObject(employee.toString()).put("reason", "Блокировка через мобильную админку").put("employmentStatus", "suspended")
                    api.request("/api/admin/employees/${employee.getString("id")}", "PUT", body)
                    "${name(employee)} заблокирован."
                }
            }.show()
    }

    private fun unblockEmployee(employee: JSONObject) {
        AlertDialog.Builder(requireContext()).setTitle("Разблокировать ${name(employee)}?")
            .setMessage("Сотрудник снова сможет входить по своему PIN.")
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Разблокировать") { _, _ ->
                run("Разблокировка") {
                    val body = JSONObject(employee.toString()).put("reason", "Разблокировка через мобильную админку").put("employmentStatus", "active")
                    api.request("/api/admin/employees/${employee.getString("id")}", "PUT", body)
                    "${name(employee)} разблокирован и может входить."
                }
            }.show()
    }

    private fun pinField(hint: String) = EditText(requireContext()).apply {
        this.hint = hint; inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
        filters = arrayOf(android.text.InputFilter.LengthFilter(4))
    }

    private fun resetPin(employee: JSONObject) {
        val context = requireContext()
        val form = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(context.dp(24), context.dp(8), context.dp(24), 0) }
        form.addView(TextView(context).apply { textSize = 15f; text = "Сообщите новый PIN сотруднику лично. При первом входе он должен будет сменить PIN." })
        val pin = pinField("Новый PIN: 4 цифры"); val repeat = pinField("Повторите PIN")
        val error = TextView(context).apply { setTextColor(context.getColor(R.color.error)); isVisible = false }
        form.addView(pin); form.addView(repeat); form.addView(error)
        val dialog = AlertDialog.Builder(context).setTitle("Сбросить PIN: ${name(employee)}").setView(form)
            .setNegativeButton("Отмена", null).setPositiveButton("Сбросить PIN", null).create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val value = pin.text.toString()
                val problem = when {
                    !Regex("\\d{4}").matches(value) -> "PIN должен состоять из 4 цифр."
                    value != repeat.text.toString() -> "PIN не совпадает с повтором."
                    value.toSet().size == 1 || value in setOf("1234", "4321", "0000") -> "Слишком простой PIN. Выберите другой."
                    else -> null
                }
                if (problem != null) { error.text = problem; error.isVisible = true; return@setOnClickListener }
                dialog.dismiss()
                run("Сброс PIN") {
                    api.request("/api/admin/employees/${employee.getString("id")}/pin", "POST",
                        JSONObject().put("pin", value).put("reason", "Сброс PIN через мобильную админку"))
                    "PIN для ${name(employee)} сброшен. При входе сотрудник сменит его."
                }
            }
        }
        dialog.show()
    }

    private fun employeeForm(employee: JSONObject? = null) {
        val context = requireContext()
        val form = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(context.dp(24), context.dp(8), context.dp(24), 0) }
        val nameInput = EditText(context).apply { hint = "ФИО"; setText(employee?.optString("fullName").orEmpty()) }
        val position = EditText(context).apply { hint = "Должность (необязательно)"; setText(employee?.optString("positionTitle")?.takeIf { it != "null" }.orEmpty()) }
        val pin = pinField("Первичный PIN: 4 цифры")
        form.addView(nameInput); form.addView(position); if (employee == null) form.addView(pin)
        val dialog = AlertDialog.Builder(context).setTitle(if (employee == null) "Новый инженер" else "Изменить сотрудника").setView(form)
            .setNegativeButton("Отмена", null).setPositiveButton(if (employee == null) "Добавить" else "Сохранить", null).create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (nameInput.text.isBlank()) { nameInput.error = "Укажите ФИО"; return@setOnClickListener }
                if (employee == null && !Regex("\\d{4}").matches(pin.text)) { pin.error = "4 цифры"; return@setOnClickListener }
                dialog.dismiss()
                run(if (employee == null) "Добавление" else "Изменение") {
                    val body = employee?.let { JSONObject(it.toString()) } ?: JSONObject().put("role", "engineer").put("pin", pin.text.toString())
                    body.put("fullName", nameInput.text.toString().trim()).put("positionTitle", position.text.toString().trim().ifBlank { JSONObject.NULL })
                        .put("reason", "Сохранение сотрудника через мобильную админку")
                    api.request(if (employee == null) "/api/admin/employees" else "/api/admin/employees/${employee.getString("id")}", if (employee == null) "POST" else "PUT", body)
                    if (employee == null) "Сотрудник ${nameInput.text.trim()} добавлен. Назначьте ему доступ к оборудованию." else "Данные ${nameInput.text.trim()} сохранены."
                }
            }
        }
        dialog.show()
    }

    private fun changeOwnPin() {
        val newPin = binding.adminPin.text.toString()
        if (!Regex("\\d{4}").matches(newPin)) { result("Новый PIN должен состоять из 4 цифр.", false); return }
        val input = pinField("Текущий PIN")
        AlertDialog.Builder(requireContext()).setTitle("Смена своего PIN").setView(input).setNegativeButton("Отмена", null).setPositiveButton("Изменить") { _, _ ->
            run("Смена PIN", reload = false) {
                val session = api.json("/api/workforce/pin", "POST", JSONObject().put("currentPin", input.text.toString()).put("newPin", newPin))
                api.saveSession(session); _binding?.adminPin?.text?.clear()
                "Ваш PIN изменён."
            }
        }.show()
    }

    // ---------- Доступ к оборудованию ----------
    /** Отмечаем оборудование галочками: снятая галочка — отзыв, новая — выдача. */
    private fun accessForEmployee(employee: JSONObject) {
        if (equipment.isEmpty()) { result("Список оборудования не загружен. Проверьте связь и обновите экран.", false); return }
        val current = activeAccess(employee.getString("id"))
        val checked = equipment.map { eq -> current.any { it.optString("equipmentId") == eq.getString("id") } }.toBooleanArray()
        val initial = checked.copyOf()
        AlertDialog.Builder(requireContext()).setTitle("Доступ: ${name(employee)}")
            .setMultiChoiceItems(equipment.map { "${it.optString("name")} · ${it.optString("model")}" }.toTypedArray(), checked) { _, i, value -> checked[i] = value }
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Сохранить доступ") { _, _ ->
                val grant = equipment.indices.filter { checked[it] && !initial[it] }.map { equipment[it] }
                val revoke = equipment.indices.filter { !checked[it] && initial[it] }.mapNotNull { i -> current.firstOrNull { it.optString("equipmentId") == equipment[i].getString("id") } }
                applyAccess(listOf(employee to grant), revoke)
            }.show()
    }

    private fun applyAccess(grants: List<Pair<JSONObject, List<JSONObject>>>, revokes: List<JSONObject>) {
        if (grants.all { it.second.isEmpty() } && revokes.isEmpty()) { result("Изменений нет.", true); return }
        run("Назначение доступа") {
            val done = mutableListOf<String>(); val failed = mutableListOf<String>()
            for ((employee, items) in grants) for (eq in items) {
                val body = JSONObject().put("employeeId", employee.getString("id")).put("equipmentId", eq.getString("id")).put("facilityId", JSONObject.NULL)
                    .put("includeDescendants", false).put("reason", "Назначение оборудования через мобильную админку")
                runCatching { api.json("/api/admin/access", "POST", body) }
                    .onSuccess { done += "выдан: ${name(employee)} → ${eq.optString("name")}" }
                    .onFailure { failed += "${name(employee)} → ${eq.optString("name")}: ${reason(it)}" }
            }
            for (grant in revokes) {
                runCatching { api.request("/api/admin/access/${grant.getString("id")}/revoke", "POST", JSONObject().put("reason", "Отзыв доступа через мобильную админку")) }
                    .onSuccess { done += "отозван: ${grant.optString("employeeName")} → ${grant.optString("targetName")}" }
                    .onFailure { failed += "отзыв ${grant.optString("targetName")}: ${reason(it)}" }
            }
            if (failed.isNotEmpty()) throw IllegalStateException(
                (if (done.isNotEmpty()) "Выполнено: ${done.joinToString("; ")}.\n" else "") + "Не выполнено: ${failed.joinToString("; ")}")
            "Доступ ${done.joinToString("; ")}."
        }
    }

    // ---------- Оборудование ----------
    private fun renderEquipment() {
        val b = _binding ?: return
        val query = norm(b.equipmentSearch.text.toString())
        val visible = equipment.filter { eq ->
            query.isBlank() || listOf("name", "model", "serialNumber").any { norm(eq.optString(it)).contains(query) }
        }
        b.equipmentList.removeAllViews()
        if (visible.isEmpty()) {
            b.equipmentList.addView(Ui.text(requireContext(), 15f, R.color.text_secondary).apply {
                text = if (equipment.isEmpty()) "Оборудование не загружено." else "Ничего не нашли по запросу."
                setPadding(0, requireContext().dp(8), 0, 0)
            })
            return
        }
        for (eq in visible) {
            val holders = access.filter { it.optString("equipmentId") == eq.getString("id") && it.optBoolean("active") }
            val row = LinearLayout(requireContext()).apply {
                orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding(0, requireContext().dp(8), 0, requireContext().dp(8))
            }
            row.addView(Ui.text(requireContext(), 15f).apply {
                text = "${eq.optString("name")}\n${eq.optString("model")} · № ${eq.optString("serialNumber")}\n" +
                    "Доступ: ${if (holders.isEmpty()) "никому не назначен" else holders.joinToString(", ") { it.optString("employeeName") }}"
            }, LinearLayout.LayoutParams(0, -2, 1f))
            row.addView(MaterialButton(requireContext(), null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
                text = "Назначить"; isAllCaps = false; setOnClickListener { accessForEquipment(eq) }
            })
            b.equipmentList.addView(row)
        }
    }

    private fun accessForEquipment(eq: JSONObject) {
        val engineers = employees.filter { !isAdmin(it) && it.optString("employmentStatus") == "active" }
        if (engineers.isEmpty()) { result("Нет активных инженеров для назначения.", false); return }
        val holders = access.filter { it.optString("equipmentId") == eq.getString("id") && it.optBoolean("active") }
        val checked = engineers.map { e -> holders.any { it.optString("employeeId") == e.getString("id") } }.toBooleanArray()
        val initial = checked.copyOf()
        AlertDialog.Builder(requireContext()).setTitle("Кто работает с «${eq.optString("name")}»")
            .setMultiChoiceItems(engineers.map { name(it) }.toTypedArray(), checked) { _, i, value -> checked[i] = value }
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Сохранить доступ") { _, _ ->
                val grant = engineers.indices.filter { checked[it] && !initial[it] }.map { engineers[it] to listOf(eq) }
                val revoke = engineers.indices.filter { !checked[it] && initial[it] }.mapNotNull { i -> holders.firstOrNull { it.optString("employeeId") == engineers[i].getString("id") } }
                applyAccess(grant, revoke)
            }.show()
    }

    // ---------- Выполнение операций и отображение результата ----------
    private fun reason(e: Throwable) = when {
        e.isNetworkFailure() -> "нет связи с сервером"
        else -> e.message ?: "сервер недоступен"
    }

    private fun result(message: String, success: Boolean) {
        val time = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm"))
        _binding?.operationResult?.show("$time · $message", if (success) Ui.Tone.Ok else Ui.Tone.Error)
    }

    /** Блокирует повторный запуск, показывает итог операции и обновляет списки. */
    private fun run(operation: String, reload: Boolean = true, block: suspend () -> String?) {
        if (busy) return
        busy = true
        _binding?.operationResult?.show("$operation: выполняется…", Ui.Tone.Info)
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                block()?.let { result(it, true) } ?: _binding?.operationResult?.let { it.visibility = View.GONE }
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                result("$operation не выполнено: ${reason(e)}", false)
            } finally {
                busy = false
                if (reload) refresh()
            }
        }
    }

    override fun onDestroyView() { _binding = null; super.onDestroyView() }
}
