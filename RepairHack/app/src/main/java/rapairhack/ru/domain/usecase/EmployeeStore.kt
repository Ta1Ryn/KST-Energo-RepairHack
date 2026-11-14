package rapairhack.ru.domain.usecase
import android.content.Context
import rapairhack.ru.data.BackendApi
class EmployeeStore(context: Context) {
    private val api = BackendApi(context)
    fun currentEmployee(): String? = api.displayName
    fun setCurrentEmployee(name: String?) { if (name == null) api.clearSession() }
}
