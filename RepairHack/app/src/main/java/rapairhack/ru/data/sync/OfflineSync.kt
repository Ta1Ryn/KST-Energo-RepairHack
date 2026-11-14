package rapairhack.ru.data.sync

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import rapairhack.ru.data.ApiError
import rapairhack.ru.data.BackendApi
import rapairhack.ru.data.local.AppDatabase
import rapairhack.ru.data.local.InspectionDraft
import java.io.File
import java.util.concurrent.TimeUnit

object Connectivity {
    fun isOnline(context: Context): Boolean {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    /** Поток «есть сеть / нет сети» для баннеров на экранах. */
    fun observe(context: Context): Flow<Boolean> = callbackFlow {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { trySend(isOnline(context)) }
            override fun onLost(network: Network) { trySend(isOnline(context)) }
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) { trySend(isOnline(context)) }
        }
        trySend(isOnline(context))
        cm?.registerDefaultNetworkCallback(callback)
        awaitClose { cm?.unregisterNetworkCallback(callback) }
    }.distinctUntilChanged()
}

/** Сборка тела запроса осмотра из черновика. Используется и экраном, и фоновой отправкой. */
object InspectionPayload {
    val actions = listOf("Проверено", "Течь масла", "Смещение", "Коррозия", "Нарушение геометрии")
    val defectCodes = listOf("", "oilLeak", "displacement", "corrosion", "geometryDeformation")
    val decisions = mapOf("confirmed" to "подтверждён инженером", "rejected" to "отклонён инженером", "corrected" to "исправлен инженером")

    fun build(draft: InspectionDraft): JSONObject {
        val analysis = draft.analysisJson?.let { JSONObject(it) }
        val decisionNote = draft.decision?.let { "Результат ИИ ${decisions[it]}." }
        val comment = listOfNotNull(draft.comment.takeIf { it.isNotBlank() }, decisionNote).joinToString("\n")
        val item = JSONObject()
            .put("componentId", draft.componentId)
            .put("installationId", draft.installationId)
            .put("action", if (draft.actionIndex == 0) "checked" else "defectRecorded")
            .put("comment", comment)
            .put("photoIds", if (draft.photoServerId == null) JSONArray() else JSONArray().put(draft.photoServerId))
        // Отклонённый результат ИИ не привязываем к осмотру.
        if (analysis != null && draft.decision != "rejected") item.put("analysisId", analysis.getString("id"))
        if (draft.actionIndex > 0) item.put("defectTypeCode", defectCodes[draft.actionIndex])
        return JSONObject()
            .put("clientRequestId", draft.id)
            .put("performedAt", draft.performedAt ?: java.time.Instant.now().toString())
            .put("comment", comment)
            .put("items", JSONArray().put(item))
    }

    /** Отправляет черновик: фото (идемпотентно) → осмотр (идемпотентно). Возвращает обновлённый черновик. */
    suspend fun send(context: Context, api: BackendApi, draft: InspectionDraft): InspectionDraft {
        val dao = AppDatabase.get(context).drafts()
        var current = draft
        if (current.photoPath != null && current.photoServerId == null) {
            val bytes = withContext(Dispatchers.IO) { File(current.photoPath!!).readBytes() }
            val photoId = api.uploadPhoto(bytes, current.photoClientId ?: current.id).getString("id")
            current = current.copy(photoServerId = photoId, updatedAt = System.currentTimeMillis())
            dao.upsert(current)
        }
        val saved = api.json("/api/equipment/${current.equipmentId}/inspections", "POST", build(current))
        current = current.copy(status = InspectionDraft.STATUS_SENT, serverInspectionId = saved.optString("id"), error = null,
            updatedAt = System.currentTimeMillis())
        dao.upsert(current)
        return current
    }
}

class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val dao = AppDatabase.get(applicationContext).drafts()
        val api = BackendApi(applicationContext)
        var retry = false
        for (draft in dao.pending()) {
            try {
                InspectionPayload.send(applicationContext, api, draft)
                draft.photoPath?.let { File(it).delete() }
            } catch (e: ApiError) {
                if (e.status == 401 || e.status >= 500) retry = true
                else dao.upsert(draft.copy(status = InspectionDraft.STATUS_FAILED, error = e.message, updatedAt = System.currentTimeMillis()))
            } catch (e: IllegalStateException) {
                retry = true // нет сессии: отправим после входа
            } catch (e: Exception) {
                retry = true // сеть пропала посреди отправки
            }
        }
        dao.purgeSent(System.currentTimeMillis() - TimeUnit.DAYS.toMillis(7))
        return if (retry) Result.retry() else Result.success()
    }

    companion object {
        private const val NAME = "kst-sync-inspections"
        /** Ставит отправку в очередь. Выполнится, как только появится сеть, даже после перезапуска. */
        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(NAME, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
        }
    }
}
