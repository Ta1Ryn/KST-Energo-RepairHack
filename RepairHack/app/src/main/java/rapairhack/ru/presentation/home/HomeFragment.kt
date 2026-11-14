package rapairhack.ru.presentation.home

import android.os.Bundle
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import rapairhack.ru.data.BackendApi
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.navigation.fragment.findNavController
import com.google.android.material.snackbar.Snackbar
import rapairhack.ru.R
import rapairhack.ru.databinding.FragmentHomeBinding
import rapairhack.ru.databinding.ItemHomeTileBinding
import rapairhack.ru.domain.usecase.EmployeeStore
import rapairhack.ru.presentation.features.FeatureFragment

class HomeFragment : Fragment() {

    private var _binding: FragmentHomeBinding? = null
    private val binding get() = _binding!!
    private val employeeStore by lazy { EmployeeStore(requireContext()) }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentHomeBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val name = employeeStore.currentEmployee() ?: getString(R.string.employee_placeholder)
        binding.employeeBadge.text = name
        binding.logout.setOnClickListener {
            viewLifecycleOwner.lifecycleScope.launch {
                runCatching { BackendApi(requireContext()).logout() }
                if (_binding != null) findNavController().navigate(R.id.action_homeFragment_to_loginFragment)
            }
        }

        bindTile(
            binding.tileScanner,
            R.drawable.ic_home_scanner,
            R.string.tile_scanner,
            R.string.tile_scanner_hint
        ) {
            findNavController().navigate(R.id.cameraFragment)
        }

        bindTile(
            binding.tileEquipment,
            R.drawable.ic_home_equipment,
            R.string.tile_equipment,
            R.string.tile_equipment_hint
        ) {
            navigateToFeature(FeatureFragment.SECTION_EQUIPMENT)
        }
        bindTile(
            binding.tileHistory,
            R.drawable.ic_home_history,
            R.string.tile_history,
            R.string.tile_history_hint
        ) {
            navigateToFeature(FeatureFragment.SECTION_HISTORY)
        }
        bindTile(
            binding.tileSettings,
            R.drawable.ic_home_settings,
            R.string.tile_settings,
            R.string.tile_settings_hint
        ) {
            findNavController().navigate(R.id.settingsFragment)
        }
        bindTile(
            binding.tileAi,
            R.drawable.ic_home_ai,
            R.string.tile_ai,
            R.string.tile_ai_hint
        ) {
            navigateToFeature(FeatureFragment.SECTION_ASSISTANT)
        }
        bindTile(
            binding.tileReporting,
            R.drawable.ic_home_reports,
            R.string.tile_reporting,
            R.string.tile_reporting_hint
        ) {
            navigateToFeature(FeatureFragment.SECTION_REPORTS)
        }
    }

    private fun bindTile(
        tile: ItemHomeTileBinding,
        icon: Int,
        title: Int,
        hint: Int,
        action: (() -> Unit)? = null
    ) {
        tile.icon.setImageResource(icon)
        tile.title.setText(title)
        tile.hint.setText(hint)
        tile.root.setOnClickListener {
            if (action != null) action()
            else Snackbar.make(binding.root, R.string.coming_soon, Snackbar.LENGTH_SHORT).show()
        }
    }

    private fun navigateToFeature(section: String) {
        findNavController().navigate(
            R.id.featureFragment,
            Bundle().apply {
                putString(FeatureFragment.ARG_SECTION, section)
            }
        )
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }
}
