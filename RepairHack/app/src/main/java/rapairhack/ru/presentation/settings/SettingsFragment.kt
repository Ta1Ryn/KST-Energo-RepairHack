package rapairhack.ru.presentation.settings

import android.content.res.Configuration
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AppCompatDelegate
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.launch
import rapairhack.ru.data.BackendApi
import rapairhack.ru.databinding.FragmentSettingsBinding

class SettingsFragment : Fragment() {
    private var _binding: FragmentSettingsBinding? = null
    private val binding get() = _binding!!
    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentSettingsBinding.inflate(inflater, container, false); return binding.root
    }
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        binding.back.setOnClickListener { findNavController().popBackStack() }
        val preferences = requireContext().getSharedPreferences("repairhack_settings", 0)
        val mode = preferences.getInt("theme_mode", AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM)
        binding.themeSwitch.isChecked = when (mode) {
            AppCompatDelegate.MODE_NIGHT_YES -> true
            AppCompatDelegate.MODE_NIGHT_NO -> false
            else -> (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        }
        binding.themeSwitch.setOnCheckedChangeListener { _, checked ->
            val selected = if (checked) AppCompatDelegate.MODE_NIGHT_YES else AppCompatDelegate.MODE_NIGHT_NO
            preferences.edit().putInt("theme_mode", selected).apply(); AppCompatDelegate.setDefaultNightMode(selected)
        }
        val api = BackendApi(requireContext()); binding.serverUrl.setText(api.serverUrl)
        binding.saveServer.setOnClickListener {
            runCatching { api.serverUrl = binding.serverUrl.text.toString() }
                .onSuccess { Snackbar.make(binding.root, "Адрес сохранён. При смене сервера нужен новый вход.", Snackbar.LENGTH_LONG).show() }
                .onFailure { binding.serverUrl.error = it.message }
        }
        binding.checkServer.setOnClickListener {
            viewLifecycleOwner.lifecycleScope.launch {
                runCatching { api.json("/health", auth = false) }
                    .onSuccess { _binding?.let { Snackbar.make(it.root, "Сервер доступен", Snackbar.LENGTH_SHORT).show() } }
                    .onFailure { _binding?.let { b -> Snackbar.make(b.root, it.message ?: "Сервер недоступен", Snackbar.LENGTH_LONG).show() } }
            }
        }
    }
    override fun onDestroyView() { _binding = null; super.onDestroyView() }
}
