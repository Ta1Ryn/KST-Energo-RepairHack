package rapairhack.ru.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import rapairhack.ru.BuildConfig

/** Ответ сервера с кодом ошибки. Сетевые сбои остаются IOException. */
class ApiError(val status: Int, message: String) : Exception(message)

/** true, если запрос не дошёл до сервера (нет сети, таймаут, сервер выключен). */
fun Throwable.isNetworkFailure(): Boolean = this is java.io.IOException

class BackendApi(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("repairhack_backend", Context.MODE_PRIVATE)
    var serverUrl: String
        get() = prefs.getString("url", BuildConfig.API_BASE_URL)!!
        set(value) {
            val url = value.trim().trimEnd('/')
            val uri = URI(url)
            require(uri.scheme in listOf("http", "https") && !uri.host.isNullOrBlank() && uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null && uri.path.orEmpty().isEmpty()) { "Пример адреса: http://192.168.1.10:5080" }
            if (url != serverUrl) clearSession()
            prefs.edit().putString("url", url).apply()
        }
    val displayName: String? get() = prefs.getString("name", null)
    val accountId: String? get() = prefs.getString("accountId", null)
    fun clearSession() { prefs.edit().remove("token").remove("name").remove("accountId").apply() }
    fun saveSession(session: JSONObject) {
        val identity = session.optString("accountId").ifBlank { session.optString("engineerId") }
        prefs.edit().putString("token", session.getString("token")).putString("name", session.getString("displayName"))
            .apply { if (identity.isNotBlank()) putString("accountId", identity) }.apply()
    }
    suspend fun request(path: String, method: String = "GET", body: JSONObject? = null, auth: Boolean = true): String =
        String(bytes(path, method, body, auth), Charsets.UTF_8)
    suspend fun json(path: String, method: String = "GET", body: JSONObject? = null, auth: Boolean = true): JSONObject =
        JSONObject(request(path, method, body, auth))
    suspend fun bytes(path: String, method: String = "GET", body: JSONObject? = null, auth: Boolean = true): ByteArray = withContext(Dispatchers.IO) {
        require(path.startsWith("/api/") || path == "/health")
        val connection = URL(serverUrl + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 8000
            connection.readTimeout = if (path.endsWith("/analyses")) 125000 else 30000
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept", "application/json")
            if (auth) {
                val token = prefs.getString("token", null) ?: error("Сначала войдите в систему.")
                connection.setRequestProperty("Authorization", "Bearer $token")
            }
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val data = (if (status in 200..299) connection.inputStream else connection.errorStream)?.use { stream ->
                val result = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                while (true) {
                    val count = stream.read(buffer)
                    if (count < 0) break
                    check(result.size() + count <= 20 * 1024 * 1024) { "Ответ сервера слишком большой." }
                    result.write(buffer, 0, count)
                }
                result.toByteArray()
            } ?: ByteArray(0)
            if (status !in 200..299) {
                if (status == 401 && auth) clearSession()
                val message = runCatching { JSONObject(String(data, Charsets.UTF_8)).optString("detail") }.getOrNull()
                throw ApiError(status, message?.takeIf { it.isNotBlank() } ?: "Ошибка сервера: $status")
            }
            data
        } finally { connection.disconnect() }
    }
    suspend fun login(pin: String): JSONObject {
        val response = json("/api/workforce/login", "POST", JSONObject().put("pin", pin), false)
        saveSession(response)
        return response
    }
    suspend fun logout() { try { request("/api/workforce/logout", "POST") } finally { clearSession() } }
    suspend fun previewFrame(content: ByteArray): JSONObject = withContext(Dispatchers.IO) {
        require(content.size <= 512 * 1024) { "Кадр слишком большой." }
        val token = prefs.getString("token", null) ?: error("Сначала войдите в систему.")
        val boundary = "LiveFrame" + java.util.UUID.randomUUID().toString().replace("-", "")
        val connection = URL(serverUrl + "/api/vision/live").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"; connection.connectTimeout = 3000; connection.readTimeout = 17000
            connection.instanceFollowRedirects = false; connection.doOutput = true
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            connection.outputStream.use { stream ->
                stream.write(("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").toByteArray())
                stream.write(content); stream.write(("\r\n--$boundary--\r\n").toByteArray())
            }
            val status = connection.responseCode
            val response = (if(status in 200..299) connection.inputStream else connection.errorStream)?.use { stream ->
                val output = java.io.ByteArrayOutputStream(); val buffer = ByteArray(8192)
                while(true) { val count=stream.read(buffer); if(count<0)break
                    check(output.size()+count<=1024*1024) { "Ответ анализа слишком большой." }; output.write(buffer,0,count) }
                output.toByteArray()
            } ?: byteArrayOf()
            val json = runCatching { JSONObject(String(response,Charsets.UTF_8)) }.getOrNull()
            if(status !in 200..299) {
                if(status==401)clearSession()
                error(json?.optString("detail")?.takeIf {it.isNotBlank()} ?: "Нет связи с анализом: HTTP $status")
            }
            json ?: error("Сервис не вернул результат анализа.")
        } finally { connection.disconnect() }
    }
    /** [clientRequestId] защищает от повторной загрузки того же снимка при повторе запроса. */
    suspend fun uploadPhoto(content: ByteArray, clientRequestId: String? = null): JSONObject = withContext(Dispatchers.IO) {
        require(content.size <= 10 * 1024 * 1024) { "Фотография слишком большая." }
        val token = prefs.getString("token", null) ?: error("Сначала войдите в систему.")
        val boundary = "KstPhoto" + java.util.UUID.randomUUID().toString().replace("-", "")
        val connection = URL(serverUrl + "/api/photos").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 8000; connection.readTimeout = 30000
            connection.instanceFollowRedirects = false; connection.doOutput = true
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            connection.outputStream.use { stream ->
                if (clientRequestId != null)
                    stream.write(("--$boundary\r\nContent-Disposition: form-data; name=\"clientRequestId\"\r\n\r\n$clientRequestId\r\n").toByteArray())
                stream.write(("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"inspection.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").toByteArray())
                stream.write(content)
                stream.write(("\r\n--$boundary--\r\n").toByteArray())
            }
            val status = connection.responseCode
            val response = (if (status in 200..299) connection.inputStream else connection.errorStream)?.use { it.readBytes() } ?: byteArrayOf()
            val json = runCatching { JSONObject(String(response, Charsets.UTF_8)) }.getOrNull()
            if (status !in 200..299) {
                if (status == 401) clearSession()
                throw ApiError(status, json?.optString("detail")?.takeIf { it.isNotBlank() } ?: "Ошибка загрузки фото: $status")
            }
            json ?: error("Сервер вернул некорректный ответ при загрузке фото.")
        } finally { connection.disconnect() }
    }
}
