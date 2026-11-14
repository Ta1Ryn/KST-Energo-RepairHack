package rapairhack.ru.presentation

import android.os.Bundle
import android.view.View
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatDelegate
import androidx.appcompat.app.AppCompatActivity
import androidx.constraintlayout.widget.ConstraintLayout
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.core.view.updateLayoutParams
import android.content.res.Configuration
import androidx.navigation.NavController
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.setupWithNavController
import rapairhack.ru.R
import rapairhack.ru.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private val binding: ActivityMainBinding by lazy { ActivityMainBinding.inflate(layoutInflater) }
    private lateinit var navController: NavController

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applySavedTheme()
        enableEdgeToEdge()
        rapairhack.ru.data.sync.SyncWorker.enqueue(this)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.main) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(bars.left, bars.top, bars.right, 0)
            binding.bot.setPadding(binding.bot.paddingLeft, binding.bot.paddingTop, binding.bot.paddingRight, bars.bottom)
            insets
        }

        val navHostFragment = supportFragmentManager.findFragmentById(R.id.fragmcont) as NavHostFragment
        navController = navHostFragment.navController

        val graph = navController.navInflater.inflate(R.navigation.nav_graph)
        graph.setStartDestination(R.id.loginFragment)
        navController.graph = graph
        binding.bot.setupWithNavController(navController)

        navController.addOnDestinationChangedListener { _, destination, _ ->
            when (destination.id) {
                R.id.homeFragment, R.id.adminFragment -> {
                    binding.bot.visibility = View.GONE
                    setSystemBarsHidden(true)
                    applySolidNav()
                    setLightBars(true)
                }
                R.id.profileFragment -> {
                    binding.bot.visibility = View.VISIBLE
                    setSystemBarsHidden(false)
                    applySolidNav()
                    setLightBars(true)
                }
                R.id.cameraFragment -> {
                    binding.bot.visibility = View.GONE
                    setSystemBarsHidden(true)
                    setLightBars(false)
                }
                else -> {
                    binding.bot.visibility = View.GONE
                    setSystemBarsHidden(false)
                    applySolidNav()
                    setLightBars(true)
                }
            }
        }
    }

    private fun setLightBars(light: Boolean) {
        val isNight = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK ==
            Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = light && !isNight
            isAppearanceLightNavigationBars = light && !isNight
        }
    }

    private fun setSystemBarsHidden(hidden: Boolean) {
        WindowCompat.getInsetsController(window, window.decorView).apply {
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            if (hidden) hide(WindowInsetsCompat.Type.systemBars())
            else show(WindowInsetsCompat.Type.systemBars())
        }
    }

    private fun applySavedTheme() {
        val mode = getSharedPreferences("repairhack_settings", MODE_PRIVATE)
            .getInt("theme_mode", AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM)
        if (mode != AppCompatDelegate.getDefaultNightMode()) {
            AppCompatDelegate.setDefaultNightMode(mode)
        }
    }

    private fun applySolidNav() {
        binding.bot.setBackgroundResource(R.drawable.bg_nav_solid)
        binding.bot.itemIconTintList = ContextCompat.getColorStateList(this, R.color.nav_item_color)
        binding.bot.itemTextColor = ContextCompat.getColorStateList(this, R.color.nav_item_color)
        binding.bot.itemRippleColor = ContextCompat.getColorStateList(this, R.color.accent_inactive)
        binding.bot.updateLayoutParams<ConstraintLayout.LayoutParams> {
            marginStart = 0
            marginEnd = 0
            bottomMargin = 0
            matchConstraintPercentWidth = 1f
        }
        binding.bot.alpha = 1f
    }

}
