package rapairhack.ru.data.local

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import kotlinx.coroutines.flow.Flow
import rapairhack.ru.data.BackendApi
import java.security.MessageDigest

/**
 * Черновик осмотра. [id] одновременно служит ключом идемпотентности (ClientRequestId):
 * повторная отправка после обрыва связи не создаёт дубликат на сервере.
 */
@Entity(tableName = "inspection_drafts")
data class InspectionDraft(
    @PrimaryKey val id: String,
    val equipmentId: String,
    val equipmentName: String = "",
    val componentIndex: Int = 0,
    val componentId: String? = null,
    val installationId: String? = null,
    val actionIndex: Int = 0,
    val comment: String = "",
    val photoPath: String? = null,
    /** Ключ идемпотентности загрузки фото. Меняется при выборе нового снимка. */
    val photoClientId: String? = null,
    val photoServerId: String? = null,
    val analysisJson: String? = null,
    /** confirmed | rejected | corrected | null */
    val decision: String? = null,
    /** draft — редактируется; pending — ждёт отправки; sent — сохранён на сервере; failed — сервер отклонил. */
    val status: String = STATUS_DRAFT,
    val error: String? = null,
    val performedAt: String? = null,
    val serverInspectionId: String? = null,
    val updatedAt: Long = System.currentTimeMillis(),
) {
    companion object {
        const val STATUS_DRAFT = "draft"
        const val STATUS_PENDING = "pending"
        const val STATUS_SENT = "sent"
        const val STATUS_FAILED = "failed"
    }
}

/** Паспорт оборудования (карточка целиком), сохранённый для просмотра без сети. */
@Entity(tableName = "cached_equipment")
data class CachedEquipment(
    @PrimaryKey val id: String,
    val json: String,
    val documentsJson: String? = null,
    val savedAt: Long = System.currentTimeMillis(),
)

/** PDF паспорта или инструкции, сохранённый во внутреннем хранилище. */
@Entity(tableName = "cached_documents")
data class CachedDocument(
    @PrimaryKey val id: String,
    val equipmentId: String?,
    val title: String,
    val filePath: String,
    val savedAt: Long = System.currentTimeMillis(),
)

@Dao
interface DraftDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun upsert(draft: InspectionDraft)
    @Query("SELECT * FROM inspection_drafts WHERE id = :id") suspend fun get(id: String): InspectionDraft?
    @Query("SELECT * FROM inspection_drafts WHERE equipmentId = :equipmentId AND status = 'draft' ORDER BY updatedAt DESC LIMIT 1")
    suspend fun openDraft(equipmentId: String): InspectionDraft?
    @Query("SELECT * FROM inspection_drafts WHERE status = 'pending' ORDER BY updatedAt")
    suspend fun pending(): List<InspectionDraft>
    @Query("SELECT COUNT(*) FROM inspection_drafts WHERE status = 'pending'")
    fun pendingCount(): Flow<Int>
    @Query("DELETE FROM inspection_drafts WHERE id = :id") suspend fun delete(id: String)
    @Query("DELETE FROM inspection_drafts WHERE status = 'sent' AND updatedAt < :before") suspend fun purgeSent(before: Long)
}

@Dao
interface CacheDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun putEquipment(item: CachedEquipment)
    @Query("SELECT * FROM cached_equipment WHERE id = :id") suspend fun equipment(id: String): CachedEquipment?
    @Query("SELECT * FROM cached_equipment") suspend fun allEquipment(): List<CachedEquipment>
    @Query("UPDATE cached_equipment SET documentsJson = :json WHERE id = :id") suspend fun putDocumentList(id: String, json: String)
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun putDocument(item: CachedDocument)
    @Query("SELECT * FROM cached_documents WHERE id = :id") suspend fun document(id: String): CachedDocument?
    @Query("SELECT id FROM cached_documents") suspend fun documentIds(): List<String>
}

@Database(entities = [InspectionDraft::class, CachedEquipment::class, CachedDocument::class], version = 1, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun drafts(): DraftDao
    abstract fun cache(): CacheDao

    companion object {
        private val instances = mutableMapOf<String, AppDatabase>()
        fun get(context: Context): AppDatabase {
            val api = BackendApi(context)
            val scope = api.serverUrl + ":" + (api.accountId ?: "signed-out")
            val digest = MessageDigest.getInstance("SHA-256").digest(scope.toByteArray())
            val key = digest.take(12).joinToString("") { "%02x".format(it.toInt() and 255) }
            return synchronized(this) {
                instances.getOrPut(key) {
                    Room.databaseBuilder(context.applicationContext, AppDatabase::class.java, "kst-offline-$key.db").build()
                }
            }
        }
    }
}
