package rapairhack.ru.presentation.login

import android.os.Bundle
import android.widget.EditText
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import rapairhack.ru.data.BackendApi
import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.GridLayout
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import rapairhack.ru.R
import rapairhack.ru.databinding.FragmentLoginBinding


class LoginFragment : Fragment() {

    private var _binding: FragmentLoginBinding? = null
    private val binding get() = _binding!!
    private val api by lazy { BackendApi(requireContext()) }
    private var submitting = false
    private val pin = StringBuilder()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentLoginBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.serverSettings.setOnClickListener {
            val input = EditText(requireContext()).apply { setText(api.serverUrl); setSingleLine() }
            AlertDialog.Builder(requireContext()).setTitle("Адрес C# сервера").setView(input).setNegativeButton("Отмена", null).setPositiveButton("Сохранить") { _, _ ->
                runCatching { api.serverUrl = input.text.toString() }.onFailure { binding.error.text = it.message; binding.error.isVisible = true }
            }.show()
        }
        buildKeypad()
        updatePinDisplay()
        binding.modeToggle.isVisible = false
        binding.employeeNumber.isVisible = false
    }

    private fun buildKeypad() {
        val labels = listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫")
        val surfaceColor = ColorStateList.valueOf(requireContext().getColor(R.color.surface))
        val borderColor = ColorStateList.valueOf(requireContext().getColor(R.color.card_stroke))
        val accentColor = requireContext().getColor(R.color.accent)
        labels.forEachIndexed { index, label ->
            val button = MaterialButton(requireContext()).apply {
                text = label
                isEnabled = label.isNotEmpty()
                if (label.isEmpty()) visibility = View.INVISIBLE
                minimumWidth = 0
                minimumHeight = 0
                insetTop = 0
                insetBottom = 0
                cornerRadius = dp(16)
                backgroundTintList = surfaceColor
                setStrokeColor(borderColor)
                strokeWidth = dp(1)
                setTextColor(accentColor)
                rippleColor = ColorStateList.valueOf(requireContext().getColor(R.color.accent_soft))
                textSize = 29f
                elevation = dp(1).toFloat()
                setOnClickListener {
                    if (label == "⌫") {
                        if (pin.isNotEmpty()) pin.deleteCharAt(pin.lastIndex)
                        binding.error.isVisible = false
                        updatePinDisplay()
                    } else {
                        enterDigit(label)
                    }
                }
            }
            val params = GridLayout.LayoutParams(
                GridLayout.spec(index / 3, 1f),
                GridLayout.spec(index % 3, 1f)
            ).apply {
                width = 0
                height = 0
                setMargins(dp(7), dp(5), dp(7), dp(5))
            }
            binding.keypad.addView(button, params)
        }
    }

    private fun enterDigit(digit: String) {
        if (submitting || pin.length >= 4) return
        pin.append(digit)
        updatePinDisplay()
        if (pin.length == 4) {
            submitPin(pin.toString())
        }
    }

    private fun submitPin(value: String) {
        submitting = true
        binding.error.isVisible = false
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val session = api.login(value)
                if (_binding == null) return@launch
                if (session.optBoolean("mustChangePin")) {
                    val input = EditText(requireContext()).apply { hint = "Новый PIN: 4 цифры"; inputType = 18 }
                    val dialog = AlertDialog.Builder(requireContext()).setTitle("Замените первичный PIN").setMessage("Новый PIN должен отличаться от первичного.").setView(input).setCancelable(false).setPositiveButton("Изменить", null).create()
                    dialog.setOnShowListener {
                        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                            viewLifecycleOwner.lifecycleScope.launch {
                                runCatching { api.json("/api/workforce/pin", "POST", org.json.JSONObject().put("currentPin", value).put("newPin", input.text.toString())) }
                                    .onSuccess { api.saveSession(it); dialog.dismiss(); navigateAfterLogin() }
                                    .onFailure { input.error = it.message }
                            }
                        }
                    }
                    dialog.show()
                } else navigateAfterLogin()
            } catch (error: Exception) {
                _binding?.let { it.error.text = error.message ?: "Сервер недоступен"; it.error.isVisible = true }
            } finally {
                submitting = false; pin.clear(); if (_binding != null) updatePinDisplay()
            }
        }
    }
    private suspend fun navigateAfterLogin() {
        val me = api.json("/api/workforce/me")
        val admin = me.getJSONArray("roles").toString().contains("\"admin\"")
        if (_binding == null) return
        rapairhack.ru.data.sync.SyncWorker.enqueue(requireContext())
        findNavController().navigate(if (admin) R.id.action_loginFragment_to_adminFragment else R.id.action_loginFragment_to_homeFragment)
    }

    private fun updatePinDisplay() {
        binding.pinDots.text = buildString {
            repeat(4) { index ->
                if (index > 0) append("  ")
                append(if (index < pin.length) "●" else "○")
            }
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }
}
