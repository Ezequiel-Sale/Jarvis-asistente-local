package com.jarvis.movil

import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.text.Normalizer
import kotlin.concurrent.thread

/**
 * Guía de comandos de voz (la del botón «? COMANDOS» de la PC), con tus frases primero y un
 * buscador. GET /api/commands. Se abre desde el chat (❔) y desde «Atajos».
 */
class CommandsActivity : JarvisActivity() {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var list: LinearLayout
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
            setPadding(dp(8), dp(10), dp(16), dp(10))
            addView(TextView(this@CommandsActivity).apply {
                text = "←"
                textSize = 22f
                setTextColor(getColor(R.color.text))
                setPadding(dp(8), 0, dp(12), 0)
                setOnClickListener { finish() }
            })
            addView(TextView(this@CommandsActivity).apply {
                text = "❔  Comandos de voz"
                setTextColor(getColor(R.color.cyan))
                textSize = 19f
            })
        })
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(16))
        }
        search = EditText(this).apply {
            hint = "Buscar: tele, volumen, monitor…"
            setTextColor(getColor(R.color.text))
            setHintTextColor(getColor(R.color.muted))
            textSize = 14f
            background = rounded(getColor(R.color.panel_light))
            setPadding(dp(12), dp(8), dp(12), dp(8))
            isSingleLine = true
            addTextChangedListener(object : TextWatcher {
                override fun afterTextChanged(s: Editable?) { render() }
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            })
        }
        body.addView(search, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(10) })
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        body.addView(list)
        root.addView(ScrollView(this).apply { addView(body) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        list.addView(TextView(this).apply { text = "Cargando…"; setTextColor(getColor(R.color.muted)) })
    }

    override fun onStart() {
        super.onStart()
        val client = jarvisApi() ?: return finish()
        thread {
            try {
                val answer = client.getJson("/api/commands")
                ui.post { data = answer; render() }
            } catch (e: Exception) {
                ui.post {
                    list.removeAllViews()
                    list.addView(TextView(this).apply {
                        text = e.message ?: "Sin conexión con la PC."
                        setTextColor(getColor(R.color.error))
                    })
                }
            }
        }
    }

    private fun plain(text: String): String =
        Normalizer.normalize(text.lowercase(), Normalizer.Form.NFD).replace(Regex("\\p{M}"), "")

    private fun render() {
        val current = data ?: return
        val words = plain(search.text.toString()).split(" ").filter { it.isNotBlank() }
        list.removeAllViews()
        val sections = current.optJSONArray("sections") ?: JSONArray()
        var shown = 0
        for (i in 0 until sections.length()) {
            val section = sections.getJSONObject(i)
            val title = "${section.optString("icon")}  ${section.optString("title")}"
            val items = section.optJSONArray("items") ?: JSONArray()
            val matching = (0 until items.length()).map { items.getJSONObject(it) }.filter { item ->
                val all = plain(title + " " + item.optString("phrase") + " " + item.optString("what"))
                words.all { all.contains(it) }
            }
            if (matching.isEmpty()) continue
            shown += matching.size
            val box = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = rounded(getColor(R.color.panel), getColor(R.color.border))
                setPadding(dp(14), dp(12), dp(14), dp(10))
            }
            box.addView(TextView(this).apply {
                text = title
                textSize = 15f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(getColor(R.color.cyan))
                setPadding(0, 0, 0, dp(6))
            })
            for (item in matching) {
                box.addView(TextView(this).apply {
                    text = "«${item.optString("phrase").trim('«', '»')}»"
                    textSize = 14f
                    setTextColor(getColor(R.color.text))
                    setPadding(0, dp(6), 0, 0)
                })
                box.addView(TextView(this).apply {
                    text = item.optString("what")
                    textSize = 12f
                    setTextColor(getColor(R.color.muted))
                })
            }
            list.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(10) })
        }
        if (shown == 0) list.addView(TextView(this).apply {
            text = "Ningún comando coincide."
            setTextColor(getColor(R.color.muted))
        })
        list.addView(TextView(this).apply {
            text = current.optString("aiHelp")
            textSize = 12f
            setTextColor(getColor(R.color.muted))
            setPadding(dp(2), dp(4), dp(2), dp(8))
        })
    }
}
