package com.jarvis.movil

import android.app.AlertDialog
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

/** Pestaña «Ajustes»: lo que antes estaba en el menú ⋮ del chat. */
class SettingsActivity : JarvisActivity() {

    private val ui = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("jarvis", MODE_PRIVATE) }
    private lateinit var list: LinearLayout
    private lateinit var lockSwitch: Switch
    private lateinit var wakeInfo: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(getColor(R.color.bg))
            fitsSystemWindows = true
        }
        root.addView(LinearLayout(this).apply {
            setBackgroundColor(getColor(R.color.panel))
            setPadding(dp(16), dp(12), dp(16), dp(12))
            addView(TextView(this@SettingsActivity).apply {
                text = "⚙  Ajustes"
                setTextColor(getColor(R.color.cyan))
                textSize = 19f
            })
        })
        list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(16))
        }
        root.addView(ScrollView(this).apply { addView(list) }, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(Tabs.bar(this, "settings"))
        setContentView(root)

        val voice = section("VOZ")
        toggle(voice, "Leer las respuestas en voz alta", "Con la misma voz de Piper que la PC.", "leer", false) { on ->
            if (!on) Speaker.stop()
        }

        val notices = section("AVISOS")
        toggle(notices, "Avisar con la app cerrada",
            "Respuestas, recordatorios, alertas del equipo y confirmaciones como notificación.", "avisos", true) { on ->
            if (on) JarvisService.start(this) else JarvisService.stop(this)
        }
        toggle(notices, "Hablar con el botón de los auriculares",
            "Un pitido: grabando. Dos: enviado. Si suena música, el botón es de la música.", "auriculares", true) {
            JarvisService.stop(this)
            JarvisService.start(this)
        }

        val security = section("SEGURIDAD")
        lockSwitch = toggle(security, "Pedir huella al abrir",
            "También al volver tras 2 minutos fuera. Las acciones de riesgo alto siempre la piden.",
            "huella", true, save = false) { on ->
            Lock.authenticate(this, "Cambiar el bloqueo de Jarvis", "Confirma que eres tú",
                onSuccess = { prefs.edit().putBoolean("huella", on).apply() },
                onFail = { lockSwitch.isChecked = !on })
        }

        val wake = section("ENCENDER LA PC")
        wakeInfo = info("")
        wake.addView(wakeInfo)
        wake.addView(action("⚡  Encender la PC", R.color.cyan) {
            thread {
                val message = Wake.send(this)
                ui.post { toast(message) }
            }
        })
        refreshWake(Wake.saved(this))
        jarvisApi()?.let { client ->
            thread {
                val data = Wake.refresh(this, client)
                if (data != null) ui.post { refreshWake(data) }
            }
        }

        val app = section("APP")
        app.addView(info("Versión ${version()}  ·  PC ${prefs.getString("base", "")}"))
        app.addView(action("Buscar actualización", R.color.cyan) {
            jarvisApi()?.let { Updater(this, it).check(manual = true) }
        })

        val actions = section("JARVIS")
        actions.addView(action("Cancelar lo que está haciendo", R.color.violet) {
            val client = jarvisApi() ?: return@action
            thread {
                val error = try {
                    client.postJson("/api/cancel")
                    null
                } catch (e: Exception) {
                    "No pude conectar con Jarvis."
                }
                ui.post { toast(error ?: "Le pedí a Jarvis que cancele.") }
            }
        })
        actions.addView(action("Desvincular este celular", R.color.error) {
            AlertDialog.Builder(this)
                .setMessage("¿Desvincular este celular? Para volver a usarlo tendrás que vincularlo con un código nuevo.")
                .setPositiveButton("Desvincular") { _, _ -> unpair() }
                .setNegativeButton("Cancelar", null)
                .show()
        })
    }

    /** Datos guardados del encendido remoto y lo que falta configurar en la PC. */
    private fun refreshWake(data: org.json.JSONObject?) {
        if (data == null) {
            wakeInfo.text = "Abre la app con la PC encendida para guardar sus datos de encendido."
            return
        }
        val tips = data.optJSONArray("tips")
        wakeInfo.text = buildString {
            append("Solo en casa (mismo Wi-Fi que la PC). PC por ")
            append(if (data.optBoolean("wifi")) "Wi-Fi: despierta desde «suspender», no desde apagado." else "cable.")
            if (tips != null && tips.length() > 0) {
                append("\n\nFalta en la PC:")
                for (i in 0 until tips.length()) append("\n• ").append(tips.getString(i))
            } else append("\nLa PC está lista para encenderse a distancia.")
        }
    }

    private fun section(name: String): LinearLayout {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(getColor(R.color.panel), getColor(R.color.border))
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        box.addView(monoLabel(name, getColor(R.color.muted)).apply { setPadding(0, 0, 0, dp(6)) })
        list.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
        return box
    }

    private fun toggle(
        parent: LinearLayout, title: String, hint: String, key: String, default: Boolean,
        save: Boolean = true, onChange: (Boolean) -> Unit,
    ): Switch {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(6), 0, dp(6))
        }
        val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        texts.addView(TextView(this).apply { text = title; setTextColor(getColor(R.color.text)); textSize = 15f })
        texts.addView(TextView(this).apply { text = hint; setTextColor(getColor(R.color.muted)); textSize = 12f })
        row.addView(texts, LinearLayout.LayoutParams(0, -2, 1f))
        val switch = Switch(this).apply {
            isChecked = prefs.getBoolean(key, default)
            thumbTintList = getColorStateList(R.color.cyan)
            setOnCheckedChangeListener { button, on ->
                if (!button.isPressed) return@setOnCheckedChangeListener  // cambios hechos por código
                if (save) prefs.edit().putBoolean(key, on).apply()
                onChange(on)
            }
        }
        row.addView(switch)
        parent.addView(row)
        return switch
    }

    private fun info(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(getColor(R.color.muted))
        textSize = 12f
        setPadding(0, 0, 0, dp(6))
    }

    private fun action(text: String, color: Int, onClick: () -> Unit): Button = Button(this).apply {
        this.text = text
        setTextColor(getColor(if (color == R.color.cyan) R.color.bg else R.color.text))
        backgroundTintList = getColorStateList(color)
        setOnClickListener { onClick() }
    }

    private fun version(): String {
        val info = packageManager.getPackageInfo(packageName, 0)
        val code = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
        return "${info.versionName} ($code)"
    }

    private fun unpair() {
        prefs.edit().remove("token").apply()
        JarvisService.stop(this)
        Speaker.stop()
        startActivity(Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()
}
