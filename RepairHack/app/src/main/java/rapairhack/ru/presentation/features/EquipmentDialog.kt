package rapairhack.ru.presentation.features

import android.content.Intent
import androidx.fragment.app.Fragment
import org.json.JSONObject
import rapairhack.ru.data.BackendApi

object EquipmentDialog {
    fun describe(card: JSONObject): String = "${card.optString("name")} · ${card.optString("model")} · ${card.optString("serialNumber")}" 
    fun show(fragment: Fragment, api: BackendApi, card: JSONObject) {
        if(fragment.isAdded) fragment.startActivity(Intent(fragment.requireContext(), EquipmentActivity::class.java).putExtra("equipmentId",card.getString("id")))
    }
}
