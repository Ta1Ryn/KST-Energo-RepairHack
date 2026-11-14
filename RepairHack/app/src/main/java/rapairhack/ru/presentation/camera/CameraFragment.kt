package rapairhack.ru.presentation.camera

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.snackbar.Snackbar
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.json.JSONObject
import rapairhack.ru.data.BackendApi
import rapairhack.ru.databinding.FragmentCameraBinding
import rapairhack.ru.presentation.features.EquipmentActivity
import rapairhack.ru.presentation.features.LiveInspectionActivity
import java.net.URLEncoder
import java.util.Locale
import java.util.concurrent.Executors

class CameraFragment : Fragment() {
    private var _binding: FragmentCameraBinding? = null
    private val binding get() = _binding!!
    private var cameraProvider: ProcessCameraProvider? = null
    private var previewUseCase: Preview? = null
    private var analysisUseCase: ImageAnalysis? = null
    private var cameraStarting = false
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private val api by lazy { BackendApi(requireContext()) }
    @Volatile private var lookingUp = false
    @Volatile private var recognizing = false
    private val recognizer by lazy { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }
    private var catalog: List<JSONObject> = emptyList()
    private var lastText = ""
    private var lastScanAt = 0L
    private var retryAt = 0L
    private var confirmation: AlertDialog? = null
    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCamera() else showPermission()
    }
    private val photo = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null || _binding == null) return@registerForActivityResult
        lookingUp = true
        try {
            recognizer.process(InputImage.fromFilePath(requireContext(), uri))
                .addOnSuccessListener { result ->
                    lookingUp = false
                    if (_binding != null) lookupText(result.text, manual = true)
                }
                .addOnFailureListener { error -> lookingUp = false; message(error.message ?: "Не удалось прочитать фотографию") }
        } catch (error: Exception) {
            lookingUp = false
            message(error.message ?: "Не удалось открыть фотографию")
        }
    }
    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentCameraBinding.inflate(inflater, container, false)
        binding.root.keepScreenOn = true
        return binding.root
    }
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        binding.back.setOnClickListener { findNavController().popBackStack() }
        binding.grantCamera.setOnClickListener { ensureCamera() }
        binding.manualPlate.setOnClickListener {
            val input = EditText(requireContext()).apply { hint = "Модель или заводской номер"; isSingleLine = true }
            AlertDialog.Builder(requireContext()).setTitle("Найти по шильдику").setView(input)
                .setNegativeButton("Отмена", null)
                .setPositiveButton("Найти") { _, _ -> lookupText(input.text.toString(), manual = true) }.show()
        }
        binding.platePhoto.setOnClickListener { photo.launch("image/*") }
        binding.liveInspection.setOnClickListener { startActivity(Intent(requireContext(), LiveInspectionActivity::class.java)) }
        binding.scanStatus.setOnClickListener { loadCatalog() }
        loadCatalog()
        ensureCamera()
    }
    private fun normalize(value: String) = value.uppercase(Locale.ROOT).replace(Regex("[^A-ZА-ЯЁ0-9]"), "")
    private fun message(value: String) { _binding?.let { Snackbar.make(it.root, value, Snackbar.LENGTH_LONG).show() } }
    private fun loadCatalog() {
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val result = mutableListOf<JSONObject>()
                var offset = 0
                do {
                    val page = api.json("/api/equipment?offset=$offset&limit=100")
                    val items = page.getJSONArray("items")
                    for (i in 0 until items.length()) result.add(items.getJSONObject(i))
                    offset += items.length()
                } while (items.length() > 0 && offset < page.getInt("total"))
                catalog = result
                _binding?.scanStatus?.text = if (result.isEmpty()) "Нет доступного оборудования" else "Наведите камеру на модель и заводской номер"
            } catch (error: CancellationException) { throw error }
            catch (error: Exception) {
                _binding?.scanStatus?.text = "Каталог недоступен · нажмите для повторной загрузки"
                message(error.message ?: "Нет связи с сервером")
            }
        }
    }
    private fun ensureCamera() {
        if (ContextCompat.checkSelfPermission(requireContext(), Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) startCamera()
        else { showPermission(); permission.launch(Manifest.permission.CAMERA) }
    }
    private fun showPermission() { _binding?.let { it.permissionCard.isVisible = true; it.scanFrame.isVisible = false } }
    override fun onResume() {
        super.onResume()
        if (_binding != null && ContextCompat.checkSelfPermission(requireContext(), Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
            && analysisUseCase?.let { cameraProvider?.isBound(it) } != true) startCamera()
    }
    @androidx.annotation.OptIn(markerClass = [androidx.camera.core.ExperimentalGetImage::class])
    private fun startCamera() {
        _binding?.let { it.permissionCard.isVisible = false; it.scanFrame.isVisible = true } ?: return
        if (cameraStarting) return
        cameraStarting = true
        val future = ProcessCameraProvider.getInstance(requireContext())
        future.addListener({
            cameraStarting = false
            if (_binding == null || !isAdded) return@addListener
            runCatching {
                val provider = future.get()
                cameraProvider = provider
                val preview = Preview.Builder().build().also { it.surfaceProvider = binding.previewView.surfaceProvider }
                previewUseCase = preview
                val analysis = ImageAnalysis.Builder().setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
                analysisUseCase = analysis
                analysis.setAnalyzer(cameraExecutor) { image ->
                    if (!recognizing && !lookingUp && image.image != null && System.currentTimeMillis()-lastScanAt > 1200) {
                        recognizing = true
                        lastScanAt = System.currentTimeMillis()
                        try {
                            recognizer.process(InputImage.fromMediaImage(image.image!!, image.imageInfo.rotationDegrees))
                                .addOnSuccessListener { result ->
                                    if (_binding != null && viewLifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) lookupText(result.text)
                                }
                                .addOnFailureListener { _binding?.scanStatus?.text = "Не удалось прочитать текст · выберите фото или введите номер" }
                                .addOnCompleteListener { recognizing = false; image.close() }
                        } catch (_: Exception) { recognizing = false; image.close() }
                    } else image.close()
                }
                provider.unbindAll()
                provider.bindToLifecycle(viewLifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }.onFailure { message(it.message ?: "Не удалось открыть камеру") }
        }, ContextCompat.getMainExecutor(requireContext()))
    }
    private fun lookupText(text: String, manual: Boolean = false) {
        val normalized = normalize(text)
        if (!manual && _binding != null && normalized.length >= 3) {
            binding.scanHint.text = "Распознано: " + text.trim().replace(Regex("\\s+"), " ").take(180)
        }
        if (_binding == null || lookingUp || (!manual && (normalized == lastText || System.currentTimeMillis() < retryAt))) return
        if (normalized.length < 3) { if (manual) message("Введите модель или заводской номер"); return }
        val serialMatches = catalog.filter {
            val serial = normalize(it.optString("serialNumber"))
            serial.length >= 3 && normalized.contains(serial)
        }
        val matches = if (serialMatches.isNotEmpty()) serialMatches else catalog.filter {
            val model = normalize(it.optString("model"))
            model.length >= 3 && normalized.contains(model)
        }
        if (matches.isEmpty()) {
            if (manual) viewLifecycleOwner.lifecycleScope.launch {
                lookingUp = true
                try {
                    val page = api.json("/api/equipment?limit=100&search=${URLEncoder.encode(text.trim(), "UTF-8")}")
                    val items = page.getJSONArray("items")
                    val found = (0 until items.length()).map { items.getJSONObject(it) }
                    if (found.isEmpty()) message("Оборудование с таким номером или моделью не найдено") else confirm(found)
                } catch (error: CancellationException) { throw error }
                catch (error: Exception) { message(error.message ?: "Нет связи") }
                finally { if (confirmation == null) lookingUp = false }
            }
            return
        }
        lastText = normalized
        confirm(matches)
    }
    private fun confirm(matches: List<JSONObject>) {
        if (_binding == null || !isAdded) return
        lookingUp = true
        val options = matches.map { "${it.optString("name")}\nМодель ${it.optString("model")} · № ${it.optString("serialNumber")}" }.toTypedArray()
        confirmation = AlertDialog.Builder(requireContext()).setTitle("Проверьте модель и заводской номер")
            .apply {
                if (matches.size == 1) setMessage(options[0]).setPositiveButton("Открыть паспорт") { _, _ -> openEquipment(matches[0].getString("id")) }
                else setItems(options) { _, i -> openEquipment(matches[i].getString("id")) }
            }
            .setNegativeButton("Отмена", null).create().also { dialog ->
                dialog.setOnDismissListener { confirmation = null; lastText = ""; retryAt = System.currentTimeMillis()+3000; lookingUp = false }
                dialog.show()
            }
    }
    private fun openEquipment(id: String) {
        if (_binding != null) startActivity(Intent(requireContext(), EquipmentActivity::class.java).putExtra("equipmentId", id))
    }
    override fun onDestroyView() {
        confirmation?.dismiss()
        listOfNotNull(previewUseCase, analysisUseCase).takeIf { it.isNotEmpty() }?.let { cameraProvider?.unbind(*it.toTypedArray()) }
        cameraProvider = null
        previewUseCase = null
        analysisUseCase = null
        cameraStarting = false
        _binding = null
        super.onDestroyView()
    }
    override fun onDestroy() { cameraExecutor.shutdown(); recognizer.close(); super.onDestroy() }
}
