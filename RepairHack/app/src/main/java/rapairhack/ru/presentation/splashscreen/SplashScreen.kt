package rapairhack.ru.presentation.splashscreen

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import rapairhack.ru.presentation.MainActivity

/** Совместимость со старыми ярлыками: сразу открываем вход, без заставки и задержки. */
class SplashScreen : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NO_ANIMATION))
        overridePendingTransition(0, 0)
        finish()
    }
}
