package rapairhack.ru.presentation.features

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.util.Size
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.doOnLayout
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import rapairhack.ru.data.BackendApi
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.abs

class LiveInspectionActivity : TechnicalActivity() {
    private val api by lazy { BackendApi(this) }
    private val executor=Executors.newSingleThreadExecutor()
    private val processing=AtomicBoolean(false)
    private val epoch=AtomicLong(0)
    private var provider: ProcessCameraProvider?=null
    private var previewUseCase: Preview?=null
    private var analysisUseCase: ImageAnalysis?=null
    private lateinit var preview: PreviewView
    private lateinit var frozen: ImageView
    private lateinit var overlay: LiveDetectionOverlay
    private lateinit var status: TextView
    private lateinit var pause: MaterialButton
    private lateinit var capture: MaterialButton
    private lateinit var equipment: MaterialButton
    private var equipmentId: String?=null
    @Volatile private var running=false
    @Volatile private var paused=false
    @Volatile private var latestSignature: IntArray?=null
    @Volatile private var shownSignature: IntArray?=null
    private var lastSent=0L
    private var lastMotionUi=0L
    private var lastResultAt=0L
    private var frames=0
    private var lastFrame: Frame?=null
    private data class Frame(val jpeg: ByteArray,val width: Int,val height: Int,val at: Long)
    private val permission=registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if(granted)preview.post { startCamera() } else status.text="Разрешите доступ к камере, затем нажмите «Продолжить»."
    }
    private val expire=object:Runnable {
        override fun run() {
            if(running && !paused && lastResultAt>0 && SystemClock.elapsedRealtime()-lastResultAt>2000) {overlay.clear();lastResultAt=0;status.text="Ждём свежий результат. Наведите камеру и задержитесь на объекте."}
            if(running)status.postDelayed(this,500)
        }
    }
    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        equipmentId=saved?.getString("equipmentId")?:intent.getStringExtra("equipmentId")
        val root=FrameLayout(this).apply {setBackgroundColor(Color.BLACK)}
        preview=PreviewView(this).apply {implementationMode=PreviewView.ImplementationMode.COMPATIBLE;scaleType=PreviewView.ScaleType.FIT_CENTER}
        root.addView(preview,FrameLayout.LayoutParams(-1,-1))
        frozen=ImageView(this).apply {scaleType=ImageView.ScaleType.FIT_CENTER;visibility=View.GONE;setBackgroundColor(Color.BLACK)}
        root.addView(frozen,FrameLayout.LayoutParams(-1,-1))
        overlay=LiveDetectionOverlay(this);root.addView(overlay,FrameLayout.LayoutParams(-1,-1))
        val top=LinearLayout(this).apply {orientation=LinearLayout.HORIZONTAL;gravity=Gravity.CENTER_VERTICAL;setPadding(16,8,16,8);setBackgroundColor(0xee102746.toInt())}
        top.addView(button("Назад") {finish()})
        top.addView(TextView(this).apply {text="  Дефекты онлайн";textSize=21f;setTextColor(Color.WHITE)},LinearLayout.LayoutParams(0,-2,1f))
        equipment=button(if(equipmentId==null)"Выбрать оборудование" else "Оборудование выбрано") {chooseEquipment {}}
        top.addView(equipment)
        pause=button("Пауза") {if(paused)resumePreview() else {paused=true;epoch.incrementAndGet();pause.text="Продолжить";overlay.clear();status.text="Анализ на паузе."}}
        top.addView(pause);root.addView(top,FrameLayout.LayoutParams(-1,-2,Gravity.TOP))
        val bottom=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setPadding(20,12,20,12);setBackgroundColor(0xee102746.toInt())}
        status=TextView(this).apply {text="Наведи камеру на оборудование. Подключаю анализ…";textSize=17f;setTextColor(Color.WHITE)};bottom.addView(status)
        val footer=LinearLayout(this).apply {gravity=Gravity.CENTER_VERTICAL}
        footer.addView(TextView(this).apply {text="Отмеченные области — предположения. Проценты не означают вероятность поломки.\nПоток не сохраняется; снимок и заключение — по кнопке.";textSize=12f;setTextColor(Color.LTGRAY)},LinearLayout.LayoutParams(0,-2,1f))
        capture=button("Снимок и заключение") {captureFrame()}.apply {isEnabled=false};footer.addView(capture);bottom.addView(footer)
        root.addView(bottom,FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM));setContentView(root)
        root.doOnLayout {overlay.setPadding(0,top.height,0,bottom.height)}
        bottom.addOnLayoutChangeListener {_,_,_,_,_,_,_,_,_->overlay.setPadding(0,top.height,0,bottom.height)}
        preview.post {ensureCamera()}
    }
    private fun button(title:String,action:()->Unit)=MaterialButton(this).apply {text=title;isAllCaps=false;cornerRadius=(12*resources.displayMetrics.density).toInt();minHeight=(48*resources.displayMetrics.density).toInt();setOnClickListener {action()}}
    private fun ensureCamera() {
        if(ContextCompat.checkSelfPermission(this,Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED)startCamera()
        else {paused=true;pause.text="Продолжить";permission.launch(Manifest.permission.CAMERA)}
    }
    private fun startCamera() {
        val future=ProcessCameraProvider.getInstance(this)
        future.addListener({if(isFinishing||isDestroyed)return@addListener
            try {
                provider=future.get()
                val rotation=preview.display.rotation
                val view=Preview.Builder().setTargetResolution(Size(960,720)).setTargetRotation(rotation).build().also {it.surfaceProvider=preview.surfaceProvider}
                val analysis=ImageAnalysis.Builder().setTargetResolution(Size(960,720)).setTargetRotation(rotation)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
                previewUseCase=view;analysisUseCase=analysis
                analysis.setAnalyzer(executor) {frame->consume(frame)}
                val group=UseCaseGroup.Builder().addUseCase(view).addUseCase(analysis)
                preview.viewPort?.let {group.setViewPort(it)}
                provider!!.unbindAll();provider!!.bindToLifecycle(this,CameraSelector.DEFAULT_BACK_CAMERA,group.build())
                paused=false;pause.text="Пауза"
            }catch(error:Exception){status.text="Камера недоступна: ${error.message}";paused=true;pause.text="Продолжить"}
        },ContextCompat.getMainExecutor(this))
    }
    private fun signature(image:ImageProxy):IntArray {
        val plane=image.planes[0];val bytes=plane.buffer;val base=bytes.position();val crop=image.cropRect
        return IntArray(192) {i->val x=crop.left+(i%16)*crop.width()/16;val y=crop.top+(i/16)*crop.height()/12
            val offset=base+y*plane.rowStride+x*plane.pixelStride
            ((bytes.get(offset).toInt() and 255)+(bytes.get(offset+1).toInt() and 255)+(bytes.get(offset+2).toInt() and 255))/3}
    }
    private fun moved(a:IntArray?,b:IntArray?):Boolean = a==null||b==null||a.indices.sumOf {abs(a[it]-b[it])}/a.size>22
    private fun consume(image:ImageProxy) {
        try {
            if(!running||paused)return
            val now=SystemClock.elapsedRealtime();val signature=signature(image)
            val changed=shownSignature!=null&&moved(shownSignature,signature);latestSignature=signature
            if(changed && now-lastMotionUi>200) {lastMotionUi=now;shownSignature=null;runOnUiThread {if(!paused)overlay.clear()}}
            if(now-lastSent<750 || !processing.compareAndSet(false,true))return
            lastSent=now;val generation=epoch.get()
            val sample=try {encode(image,now)}catch(error:Exception){processing.set(false);throw error}
            runOnUiThread {if(running&&!paused&&generation==epoch.get()) {lastFrame=sample;capture.isEnabled=true}}
            lifecycleScope.launch {
                try {
                    val result=api.previewFrame(sample.jpeg)
                    if(!running||paused||generation!=epoch.get())return@launch
                    frames++;lastResultAt=SystemClock.elapsedRealtime()
                    if(moved(signature,latestSignature)){overlay.clear();status.text="Камера движется. Задержитесь на объекте для точных рамок.";return@launch}
                    val detections=result.optJSONArray("detections")?:JSONArray()
                    shownSignature=signature
                    overlay.show(detections,sample.width,sample.height)
                    val labels=(0 until detections.length()).map {val d=detections.getJSONObject(it);DetectionPhotoView.labels[d.optString("label")]?:d.optString("label")}.distinct().take(4)
                    val elapsed=SystemClock.elapsedRealtime()-sample.at
                    status.text="Онлайн · ${elapsed} мс · "+if(labels.isEmpty())"Уверенных отметок нет; это не подтверждение исправности." else labels.joinToString(", ")
                    status.contentDescription="Обработано кадров: $frames. ${status.text}"
                }catch(cancel:CancellationException){throw cancel}
                catch(error:Exception){if(running&&!paused&&generation==epoch.get()){overlay.clear();lastResultAt=0;status.text=(error.message?:"Нет связи с анализом")+" Повторяю автоматически…"}}
                finally {processing.set(false)}
            }
        }catch(error:Exception){runOnUiThread {if(running&&!paused)status.text="Не удалось подготовить кадр: ${error.message}"}}
        finally {image.close()}
    }
    private fun encode(image:ImageProxy,at:Long):Frame {
        val raw=image.toBitmap();val crop=image.cropRect;val matrix=Matrix().apply {postRotate(image.imageInfo.rotationDegrees.toFloat())}
        val oriented=Bitmap.createBitmap(raw,crop.left,crop.top,crop.width(),crop.height(),matrix,true)
        val ratio=minOf(1f,960f/maxOf(oriented.width,oriented.height))
        val scaled=if(ratio<1f)Bitmap.createScaledBitmap(oriented,(oriented.width*ratio).toInt(),(oriented.height*ratio).toInt(),true) else oriented
        return try {val output=ByteArrayOutputStream();scaled.compress(Bitmap.CompressFormat.JPEG,75,output);Frame(output.toByteArray(),scaled.width,scaled.height,at)}
        finally {if(scaled!==oriented)scaled.recycle();if(oriented!==raw)oriented.recycle();raw.recycle()}
    }
    private fun resumePreview() {paused=false;epoch.incrementAndGet();lastResultAt=0;lastFrame=null;capture.isEnabled=false;overlay.clear();frozen.setImageDrawable(null);frozen.visibility=View.GONE;pause.text="Пауза";status.text="Наведи камеру и задержись на объекте…";if(provider==null)ensureCamera()}
    private fun chooseEquipment(chosen:(String)->Unit) {
        lifecycleScope.launch {try {
            val items=api.json("/api/equipment?limit=100").getJSONArray("items")
            if(items.length()==0){status.text="Нет доступного оборудования для осмотра.";return@launch}
            if(isFinishing||isDestroyed)return@launch
            AlertDialog.Builder(this@LiveInspectionActivity).setTitle("К какому оборудованию относится осмотр?")
                .setItems((0 until items.length()).map {items.getJSONObject(it).optString("name")+" · "+items.getJSONObject(it).optString("serialNumber")}.toTypedArray()) {_,index->
                    val item=items.getJSONObject(index);equipmentId=item.getString("id");equipment.text=item.optString("name").take(28);chosen(equipmentId!!)
                }.setNegativeButton("Отмена",null).show()
        }catch(error:Exception){status.text=error.message?:"Не удалось загрузить оборудование"}}
    }
    private fun captureFrame() {
        val frame=lastFrame?:return
        if(!paused && SystemClock.elapsedRealtime()-frame.at>3000){status.text="Дождитесь свежего кадра камеры.";return}
        paused=true;epoch.incrementAndGet();pause.text="Продолжить";overlay.clear()
        frozen.setImageBitmap(BitmapFactory.decodeByteArray(frame.jpeg,0,frame.jpeg.size));frozen.visibility=View.VISIBLE
        status.text="Кадр зафиксирован. Выберите оборудование для заключения."
        val open:(String)->Unit={id->
            val folder=File(cacheDir,"live-captures").apply {mkdirs()}
            val file=File.createTempFile("frame-",".jpg",folder).apply {writeBytes(frame.jpeg)}
            startActivity(Intent(this,InspectionActivity::class.java).putExtra("equipmentId",id).putExtra("livePhotoPath",file.path))
        }
        equipmentId?.let(open)?:chooseEquipment(open)
    }
    override fun onStart(){super.onStart();running=true;if(::status.isInitialized)status.post(expire)}
    override fun onStop(){running=false;epoch.incrementAndGet();if(::status.isInitialized){status.removeCallbacks(expire);overlay.clear()};super.onStop()}
    override fun onSaveInstanceState(outState:Bundle){outState.putString("equipmentId",equipmentId);super.onSaveInstanceState(outState)}
    override fun onDestroy(){listOfNotNull(previewUseCase,analysisUseCase).takeIf {it.isNotEmpty()}?.let {provider?.unbind(*it.toTypedArray())};executor.shutdown();super.onDestroy()}
}
