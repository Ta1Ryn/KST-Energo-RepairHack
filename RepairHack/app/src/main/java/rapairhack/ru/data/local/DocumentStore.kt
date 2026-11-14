package rapairhack.ru.data.local

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import rapairhack.ru.data.BackendApi
import rapairhack.ru.data.isNetworkFailure
import java.io.File

/** Паспорта и инструкции: сначала сеть, при её отсутствии — сохранённые на устройстве копии. */
class DocumentStore(private val context: Context) {
    private val api = BackendApi(context)
    private val cache = AppDatabase.get(context).cache()
    private val directory get() = File(context.filesDir, "documents").apply { mkdirs() }

    data class Listing(val documents: List<JSONObject>, val fromCache: Boolean)

    suspend fun list(equipmentId: String): Listing = try {
        val raw = api.request("/api/documents?equipmentId=$equipmentId")
        runCatching { cache.putDocumentList(equipmentId, raw) }
        Listing(parse(raw), false)
    } catch (e: CancellationException) { throw e } catch (e: Exception) {
        if (!e.isNetworkFailure()) throw e
        val cached = cache.equipment(equipmentId)?.documentsJson ?: throw e
        val savedIds = cache.documentIds().toSet()
        Listing(parse(cached).filter { it.getString("id") in savedIds }, true)
    }

    suspend fun isSaved(id: String) = cache.document(id)?.let { File(it.filePath).exists() } == true

    /** Возвращает файл PDF: из кэша или скачивает и сохраняет для просмотра без сети. */
    suspend fun file(id: String, title: String, equipmentId: String?): File {
        cache.document(id)?.let { saved -> File(saved.filePath).takeIf { it.exists() }?.let { return it } }
        val data = api.bytes("/api/documents/$id")
        val file = withContext(Dispatchers.IO) { File(directory, "$id.pdf").apply { writeBytes(data) } }
        cache.putDocument(CachedDocument(id, equipmentId, title, file.path))
        return file
    }

    /** Сохраняет все документы оборудования заранее, пока есть сеть. */
    suspend fun prefetch(equipmentId: String): Int {
        val listing = list(equipmentId)
        if (listing.fromCache) return 0
        var saved = 0
        for (doc in listing.documents) {
            runCatching { file(doc.getString("id"), doc.optString("originalName"), equipmentId) }.onSuccess { saved++ }
        }
        return saved
    }

    private fun parse(raw: String) = JSONArray(raw).let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
}
