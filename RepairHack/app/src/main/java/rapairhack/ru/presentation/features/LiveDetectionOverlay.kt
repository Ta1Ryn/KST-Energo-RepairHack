package rapairhack.ru.presentation.features

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.view.View
import org.json.JSONArray

class LiveDetectionOverlay(context: Context) : View(context) {
    private var detections = JSONArray()
    private var frameWidth = 1
    private var frameHeight = 1
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { color=Color.rgb(255,196,87); strokeWidth=2f*resources.displayMetrics.density; style=Paint.Style.STROKE }
    private val background = Paint(Paint.ANTI_ALIAS_FLAG).apply { color=Color.rgb(16,39,70) }
    private val mask = Paint(Paint.ANTI_ALIAS_FLAG).apply { color=Color.argb(58,255,196,87);style=Paint.Style.FILL }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply { color=Color.WHITE; textSize=14f*resources.displayMetrics.density }
    fun show(items: JSONArray, width: Int, height: Int) { detections=items;frameWidth=width;frameHeight=height;invalidate() }
    fun clear() { detections=JSONArray();invalidate() }
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val scale=minOf(width.toFloat()/frameWidth,height.toFloat()/frameHeight)
        val fw=frameWidth*scale;val fh=frameHeight*scale;val left=(width-fw)/2;val top=(height-fh)/2
        val safeTop=maxOf(top,paddingTop.toFloat());val safeBottom=minOf(top+fh,(height-paddingBottom).toFloat())
        val padding=4f*resources.displayMetrics.density
        for(i in 0 until detections.length()) {
            val item=detections.getJSONObject(i);val box=item.optJSONObject("box")?:continue
            val x=left+box.getDouble("x").toFloat()*fw;val y=top+box.getDouble("y").toFloat()*fh
            val w=box.getDouble("width").toFloat()*fw;val h=box.getDouble("height").toFloat()*fh
            val contour=DetectionGeometry.contour(item,fw,fh,left=left,top=top)
            if(contour!=null){canvas.drawPath(contour,mask);canvas.drawPath(contour,line)}
            else canvas.drawRect(x,y,x+w,y+h,line)
            val code=item.optString("label")
            val text="${DetectionPhotoView.labels[code]?:code} · ${(item.optDouble("confidence")*100).toInt()}%"
            val tw=minOf(label.measureText(text)+2*padding,fw);val th=label.fontSpacing+2*padding
            if(y+h<safeTop||y>safeBottom)continue
            val tx=x.coerceIn(left,left+fw-tw);val ty=(y-th).coerceIn(safeTop,maxOf(safeTop,safeBottom-th))
            canvas.save();canvas.clipRect(tx,ty,tx+tw,ty+th)
            canvas.drawRect(tx,ty,tx+tw,ty+th,background)
            canvas.drawText(text,tx+padding,ty+padding-label.fontMetrics.top,label);canvas.restore()
        }
    }
}
