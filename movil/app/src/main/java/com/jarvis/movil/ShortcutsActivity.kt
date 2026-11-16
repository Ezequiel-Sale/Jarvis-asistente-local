package com.jarvis.movil

import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Pestaña «Atajos»: las rutinas de Jarvis (ejecutar ya, activar o pausar) y los juegos
 * (abrir, con sus «acciones al abrir»). GET/POST /api/shortcuts. Arriba, «❔ Comandos».
 */
class ShortcutsActivity : JarvisActivity() {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var routines: LinearLayout
    private lateinit var games: LinearLayout
    private lateinit var note: TextView
    private lateinit var search: EditText
    private var data: JSONObject? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(getColor(R.color.bg))
            fitsSystemWindows = true
        }
        root.addView(LinearLayout(this).apply {
            setBackgroundColor(getColor(R.color.panel))
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(10), dp(10), dp(10))
            addView(TextView(this@ShortcutsActivity).apply {
                text = "⚡  Atajos"
                setTextColor(getColor(R.color.cyan))
                textSize = 19f
            }, LinearLayout.LayoutParams(0, -2, 1f))
            addView(pill("❔ Comandos", R.color.panel_light, R.color.cyan) {
                startActivity(Intent(this@ShortcutsActivity, CommandsActivity::class.java))
            })
        })
        val list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(16))
        }
        root.addView(ScrollView(this).apply { addView(list) }, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(Tabs.bar(this, "shortcuts"))
        setContentView(root)

        note = TextView(this).apply { setTextColor(getColor(R.color.muted)); textSize = 13f; text = "Cargando…" }
        list.addView(note)
        list.addView(monoLabel("RUTINAS", getColor(R.color.muted)).apply { setPadding(dp(2), dp(10), 0, dp(6)) })
        routines = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        list.addView(routines)
        list.addView(monoLabel("JUEGOS", getColor(R.color.muted)).apply { setPadding(dp(2), dp(14), 0, dp(6)) })
        search = EditText(this).apply {
            hint = "Buscar juego…"
            setTextColor(getColor(R.color.text))
            setHintTextColor(getColor(R.color.muted))
            textSize = 14f
            background = rounded(getColor(R.color.panel_light))
            setPadding(dp(12), dp(8), dp(12), dp(8))
            isSingleLine = true
            addTextChangedListener(object : TextWatcher {
                override fun afterTextChanged(s: Editable?) { renderGames() }
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            })
        }
        list.addView(search, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
        games = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        list.addView(games)
    }

    override fun onStart() {
        super.onStart()
        load()
    }

    private fun load() {
        val client = jarvisApi() ?: return finish()
        thread {
            try {
                val answer = client.getJson("/api/shortcuts", 30000)
                ui.post { data = answer; render() }
            } catch (e: Exception) {
                ui.post { note.text = e.message ?: "Sin conexión con la PC." }
            }
        }
    }

    private fun send(body: JSONObject) {
        val client = jarvisApi() ?: return
        thread {
            try {
                val answer = client.postJson("/api/shortcuts", body)
                ui.post {
                    Toast.makeText(this, answer.optString("text"), Toast.LENGTH_SHORT).show()
                    answer.optJSONObject("shortcuts")?.let { data = it; render() }
                }
            } catch (e: Exception) {
                ui.post { Toast.makeText(this, e.message ?: "No pude hablar con Jarvis.", Toast.LENGTH_LONG).show() }
            }
        }
    }

    // --- Vista -------------------------------------------------------------------------

    private fun pill(label: String, bg: Int, fg: Int, onClick: () -> Unit): TextView = TextView(this).apply {
        text = label
        gravity = Gravity.CENTER
        textSize = 13f
        typeface = Typeface.DEFAULT_BOLD
        setTextColor(getColor(fg))
        setPadding(dp(12), dp(8), dp(12), dp(8))
        val fill = GradientDrawable().apply { setColor(getColor(bg)); cornerRadius = dp(10).toFloat() }
        background = RippleDrawable(ColorStateList.valueOf(getColor(R.color.hover)), fill, null)
        isClickable = true
        setOnClickListener {
            it.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
            onClick()
        }
    }

    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = rounded(getColor(R.color.panel), getColor(R.color.border))
        setPadding(dp(14), dp(12), dp(14), dp(12))
    }

    private fun render() {
        val current = data ?: return
        val list = current.optJSONArray("routines") ?: JSONArray()
        val gameList = current.optJSONArray("games") ?: JSONArray()
        note.text = "${list.length()} rutinas · ${gameList.length()} juegos. «Ejecutar» hace la rutina ahora, " +
            "aunque sea de una hora o de un evento."
        routines.removeAllViews()
        if (list.length() == 0) routines.addView(TextView(this).apply {
            text = "No hay rutinas. Créalas en Jarvis (⚙ Rutinas en la PC)."
            setTextColor(getColor(R.color.muted))
        })
        for (i in 0 until list.length()) {
            val r = list.getJSONObject(i)
            val id = r.optInt("id")
            val active = r.optBoolean("active", true)
            val box = card()
            val top = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
            top.addView(TextView(this).apply {
                text = r.optString("name")
                textSize = 16f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(getColor(if (active) R.color.text else R.color.muted))
            }, LinearLayout.LayoutParams(0, -2, 1f))
            top.addView(Switch(this).apply {
                isChecked = active
                setOnCheckedChangeListener { _, on ->
                    send(JSONObject().put("routine", id).put("action", if (on) "enable" else "disable"))
                }
            })
            box.addView(top)
            box.addView(TextView(this).apply {
                text = r.optString("when").replaceFirstChar { it.uppercase() }
                textSize = 13f
                setTextColor(getColor(R.color.cyan))
            })
            val steps = r.optJSONArray("steps") ?: JSONArray()
            box.addView(TextView(this).apply {
                text = (0 until steps.length()).joinToString("\n") { "• " + steps.optString(it) }
                textSize = 13f
                setTextColor(getColor(R.color.muted))
                setPadding(0, dp(4), 0, dp(8))
            })
            box.addView(pill("▶  Ejecutar", R.color.cyan_dark, R.color.cyan) {
                send(JSONObject().put("routine", id).put("action", "run"))
            }, LinearLayout.LayoutParams(-2, -2))
            routines.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(10) })
        }
        renderGames()
    }

    private fun renderGames() {
        val current = data ?: return
        val list = current.optJSONArray("games") ?: JSONArray()
        val filter = search.text.toString().trim().lowercase()
        games.removeAllViews()
        var shown = 0
        for (i in 0 until list.length()) {
            val g = list.getJSONObject(i)
            val name = g.optString("name")
            if (filter.isNotEmpty() && !name.lowercase().contains(filter)) continue
            shown++
            val row = LinearLayout(this).apply {
                gravity = Gravity.CENTER_VERTICAL
                background = rounded(getColor(R.color.panel), getColor(R.color.border))
                setPadding(dp(14), dp(10), dp(10), dp(10))
            }
            val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            texts.addView(TextView(this).apply { text = name; textSize = 15f; setTextColor(getColor(R.color.text)) })
            val actions = g.optInt("actions")
            if (actions > 0) texts.addView(TextView(this).apply {
                text = "⚙ $actions ${if (actions == 1) "acción" else "acciones"} al abrir"
                textSize = 12f
                setTextColor(getColor(R.color.muted))
            })
            row.addView(texts, LinearLayout.LayoutParams(0, -2, 1f))
            row.addView(pill("▶  Jugar", R.color.cyan, R.color.bg) { send(JSONObject().put("game", name)) })
            games.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
        }
        if (shown == 0) games.addView(TextView(this).apply {
            text = if (filter.isEmpty()) "No encontré juegos en la PC." else "Ningún juego coincide."
            setTextColor(getColor(R.color.muted))
        })
    }
}
