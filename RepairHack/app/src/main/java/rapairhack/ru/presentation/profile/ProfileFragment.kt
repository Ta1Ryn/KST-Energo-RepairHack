package rapairhack.ru.presentation.profile

import android.os.Bundle
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import rapairhack.ru.data.BackendApi
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.navigation.fragment.findNavController
import rapairhack.ru.R
import rapairhack.ru.databinding.FragmentProfileBinding
import rapairhack.ru.domain.usecase.EmployeeStore

class ProfileFragment : Fragment() {

    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!
    private val employeeStore by lazy { EmployeeStore(requireContext()) }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentProfileBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val displayName = employeeStore.currentEmployee() ?: getString(R.string.employee_placeholder)
        val email = getString(R.string.profile_pin_access)
        binding.name.text = displayName
        binding.email.text = email
        binding.avatar.text = initials(displayName)

        binding.logout.setOnClickListener {
            viewLifecycleOwner.lifecycleScope.launch {
                runCatching { BackendApi(requireContext()).logout() }
                if (_binding != null) findNavController().navigate(R.id.action_profileFragment_to_loginFragment)
            }
        }
    }

    private fun initials(name: String): String {
        val parts = name.trim().split(" ").filter { it.isNotBlank() }
        return when {
            parts.size >= 2 -> "${parts[0].first()}${parts[1].first()}".uppercase()
            parts.isNotEmpty() -> parts[0].take(2).uppercase()
            else -> "RH"
        }
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }
}
