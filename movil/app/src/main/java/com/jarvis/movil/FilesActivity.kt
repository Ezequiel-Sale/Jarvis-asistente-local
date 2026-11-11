package com.jarvis.movil

import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

/**
 * 📁 Archivos y portapapeles: traer o mandar el portapapeles, enviar archivos a
 * Descargas de la PC y explorar Descargas, Documentos y los otros discos de la PC
 * (la lista la da /api/files/roots) para bajarlos (quedan en «Descargas/Jarvis»).
 */
class FilesActivity : JarvisActivity() {

    companion object {
        const val EXTRA_ROOT = "root"
        const val EXTRA_PATH = "path"
        const val EXTRA_NAME = "name"
        private const val PICK = 7
    }

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var list: LinearLayout
    private lateinit var where: TextView
    private lateinit var progressText: TextView
    private lateinit var rootButtons: LinearLayout
    private var root = "descargas"
    private var path = ""
    private var roots = listOf("descargas" to "Descargas", "documentos" to "Documentos")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(getColor(R.color.bg))
            fitsSystemWindows = true
        }
        page.addView(LinearLayout(this).apply {
            setBackgroundColor(getColor(R.color.panel))
            setPadding(dp(16), dp(12), dp(16), dp(12))
            addView(TextView(this@FilesActivity).apply {
                text = "📁  Archivos"
                setTextColor(getColor(R.color.cyan))
                textSize = 19f
            })
        })
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(16))
        }
        page.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(page)

        val clip = section(content, "PORTAPAPELES")
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(action("⬇ Traer de la PC") { pullClipboard() }, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(6) })
        row.addView(action("⬆ Enviar a la PC") { pushClipboard() }, LinearLayout.LayoutParams(0, -2, 1f))
        clip.addView(row)

        val send = section(content, "ENVIAR A LA PC")
        send.addView(action("Elegir archivos…") {
            @Suppress("DEPRECATION")
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true), PICK)
        })
        send.addView(hint("Llegan a la carpeta Descargas de la PC. También puedes usar «Compartir → Jarvis» desde cualquier app."))
        progressText = hint("")
        send.addView(progressText)

        val browse = section(content, "ARCHIVOS DE LA PC")
        rootButtons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        browse.addView(android.widget.HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            addView(rootButtons)
        })
        where = TextView(this).apply {
            setTextColor(getColor(R.color.violet))
            textSize = 13f
            setPadding(0, dp(8), 0, dp(4))
        }
        browse.addView(where)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        browse.addView(list)
        refreshRoots()
    }

    override fun onStart() {
        super.onStart()
        loadRoots()
        load()
        handleDownloadIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleDownloadIntent(intent)
    }

    /** Desde la notificación «Jarvis te manda un archivo». */
    private fun handleDownloadIntent(intent: Intent?) {
        val target = intent?.getStringExtra(EXTRA_PATH) ?: return
        val targetRoot = intent.getStringExtra(EXTRA_ROOT) ?: return
        val name = intent.getStringExtra(EXTRA_NAME) ?: target.substringAfterLast('/')
        intent.removeExtra(EXTRA_PATH)
        download(targetRoot, target, name)
    }

    private fun section(parent: LinearLayout, title: String): LinearLayout {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = rounded(getColor(R.color.panel), getColor(R.color.border))
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        box.addView(monoLabel(title, getColor(R.color.muted)).apply { setPadding(0, 0, 0, dp(8)) })
        parent.addView(box, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
        return box
    }

    private fun action(text: String, onClick: () -> Unit): Button = Button(this).apply {
        this.text = text
        setTextColor(getColor(R.color.bg))
        backgroundTintList = getColorStateList(R.color.cyan)
        setOnClickListener { onClick() }
    }

    private fun hint(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(getColor(R.color.muted))
        textSize = 12f
        setPadding(0, dp(6), 0, 0)
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    // --- Portapapeles -----------------------------------------------------------------------

    private fun pullClipboard() {
        val client = jarvisApi() ?: return
        thread {
            try {
                val text = client.getJson("/api/clipboard").optString("text")
                ui.post {
                    if (text.isEmpty()) toast("El portapapeles de la PC no tiene texto.") else {
                        getSystemService(ClipboardManager::class.java)
                            .setPrimaryClip(ClipData.newPlainText("Portapapeles de la PC", text))
                        toast("Copiado en el celular (${text.length} caracteres).")
                    }
                }
            } catch (e: Exception) {
                ui.post { toast(e.message ?: "No pude leer el portapapeles de la PC.") }
            }
        }
    }

    private fun pushClipboard() {
        val text = getSystemService(ClipboardManager::class.java).primaryClip
            ?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
        if (text.isEmpty()) {
            toast("El portapapeles del celular está vacío.")
            return
        }
        val client = jarvisApi() ?: return
        thread {
            try {
                client.postJson("/api/clipboard", JSONObject().put("text", text))
                ui.post { toast("Listo: ya puedes pegarlo en la PC (Ctrl+V).") }
            } catch (e: Exception) {
                ui.post { toast(e.message ?: "No pude copiar en la PC.") }
            }
        }
    }

    // --- Enviar archivos ----------------------------------------------------------------------

    @Deprecated("Selector de archivos del sistema.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != PICK) {
            @Suppress("DEPRECATION")
            super.onActivityResult(requestCode, resultCode, data)
            return
        }
        if (resultCode != RESULT_OK || data == null) return
        val uris = ArrayList<Uri>()
        data.clipData?.let { clip -> for (i in 0 until clip.itemCount) uris.add(clip.getItemAt(i).uri) }
        if (uris.isEmpty()) data.data?.let { uris.add(it) }
        val client = jarvisApi() ?: return
        val window = ProgressWindow(this, if (uris.size == 1) "Enviando a la PC" else "Enviando ${uris.size} archivos a la PC")
        thread {
            var sentFiles = 0
            for (uri in uris) {
                if (window.cancelled) break
                try {
                    Transfers.upload(this, client, uri) { sent, size -> window.update(sent, size) }
                    sentFiles++
                } catch (e: Exception) {
                    if (!window.cancelled) ui.post { toast("No se envió un archivo: ${e.message}") }
                }
            }
            ui.post {
                window.close()
                progressText.text = if (window.cancelled) "Envío cancelado." else
                    "Listo: $sentFiles de ${uris.size} archivo(s) en Descargas de la PC."
                if (root == "descargas" && path.isEmpty()) load()
            }
        }
    }

    // --- Explorar la PC ------------------------------------------------------------------------

    /** Descargas, Documentos y los otros discos de la PC. */
    private fun loadRoots() {
        val client = jarvisApi() ?: return
        thread {
            try {
                val list = client.getJson("/api/files/roots").getJSONArray("roots")
                val loaded = (0 until list.length()).map { list.getJSONObject(it).let { r -> r.optString("key") to r.optString("name") } }
                if (loaded.isNotEmpty()) ui.post {
                    roots = loaded
                    refreshRoots()
                }
            } catch (e: Exception) {
                // se quedan Descargas y Documentos
            }
        }
    }

    private fun rootName(key: String): String = roots.firstOrNull { it.first == key }?.second ?: key

    private fun refreshRoots() {
        rootButtons.removeAllViews()
        for ((key, label) in roots) {
            val selected = key == root
            rootButtons.addView(Button(this).apply {
                text = label
                setTextColor(getColor(if (selected) R.color.bg else R.color.text))
                backgroundTintList = getColorStateList(if (selected) R.color.cyan else R.color.panel_light)
                setOnClickListener {
                    root = key
                    path = ""
                    refreshRoots()
                    load()
                }
            }, LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(6) })
        }
    }

    private fun load() {
        val client = jarvisApi() ?: return
        val wantedRoot = root
        val wantedPath = path
        where.text = rootName(root) +
            (if (path.isEmpty()) "" else " / " + path.replace("/", " / "))
        thread {
            try {
                val reply = client.getJson("/api/files?root=" + java.net.URLEncoder.encode(wantedRoot, "UTF-8") + "&path=" +
                    java.net.URLEncoder.encode(wantedPath, "UTF-8"))
                ui.post { if (wantedRoot == root && wantedPath == path) render(reply) }
            } catch (e: Exception) {
                ui.post { list.removeAllViews(); list.addView(hint(e.message ?: "No pude leer la carpeta.")) }
            }
        }
    }

    private fun render(reply: JSONObject) {
        list.removeAllViews()
        if (path.isNotEmpty()) list.addView(item("⬅  Volver", "") {
            path = path.substringBeforeLast('/', "")
            load()
        })
        val items = reply.optJSONArray("items")
        if (items == null || items.length() == 0) {
            list.addView(hint("Carpeta vacía."))
            return
        }
        val dates = SimpleDateFormat("dd/MM/yy HH:mm", Locale.getDefault())
        for (i in 0 until items.length()) {
            val entry = items.getJSONObject(i)
            val name = entry.optString("name")
            val child = if (path.isEmpty()) name else "$path/$name"
            if (entry.optBoolean("dir")) {
                list.addView(item("📁  $name", "") {
                    path = child
                    load()
                })
            } else {
                val detail = Transfers.readable(entry.optLong("size")) + "  ·  " +
                    dates.format(Date(entry.optLong("modified") * 1000))
                list.addView(item("📄  $name", detail) {
                    AlertDialog.Builder(this)
                        .setMessage("¿Bajar «$name» (${Transfers.readable(entry.optLong("size"))}) al celular?")
                        .setPositiveButton("Bajar") { _, _ -> download(root, child, name) }
                        .setNegativeButton("Cancelar", null)
                        .show()
                })
            }
        }
    }

    private fun item(title: String, detail: String, onClick: () -> Unit): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(4), dp(10), dp(4), dp(10))
            addView(TextView(this@FilesActivity).apply {
                text = title
                setTextColor(getColor(R.color.text))
                textSize = 15f
                gravity = Gravity.START
            })
            if (detail.isNotEmpty()) addView(TextView(this@FilesActivity).apply {
                text = detail
                setTextColor(getColor(R.color.muted))
                textSize = 12f
            })
            setOnClickListener { onClick() }
        }

    private fun download(targetRoot: String, target: String, name: String) {
        val client = jarvisApi() ?: return
        val window = ProgressWindow(this, "Bajando «$name»")
        thread {
            try {
                val uri = Transfers.download(this, client, targetRoot, target, name) { received, total ->
                    window.update(received, total)
                }
                ui.post {
                    window.close()
                    progressText.text = "«$name» quedó en Descargas/Jarvis del celular."
                    val done = AlertDialog.Builder(this).setMessage("«$name» quedó en Descargas/Jarvis del celular.")
                    if (uri != null) {
                        done.setPositiveButton("Abrir") { _, _ ->
                            try {
                                startActivity(Transfers.openIntent(this, uri))
                            } catch (e: Exception) {
                                toast("No hay una app para abrir ese archivo.")
                            }
                        }.setNegativeButton("Cerrar", null)
                    } else done.setPositiveButton("Listo", null)
                    done.show()
                }
            } catch (e: Exception) {
                ui.post {
                    window.close()
                    toast(if (window.cancelled) "Descarga cancelada." else "No se pudo bajar: ${e.message ?: "sin conexión con la PC"}")
                }
            }
        }
    }
}

/**
 * Ventana de progreso de una transferencia: porcentaje, MB, velocidad, tiempo restante
 * y «Cancelar». ``update`` se puede llamar desde cualquier hilo; si se canceló, lanza
 * una excepción para cortar la transferencia (el archivo a medias se borra).
 */
class ProgressWindow(private val activity: android.app.Activity, title: String) {
    @Volatile
    var cancelled = false
        private set
    private val ui = Handler(Looper.getMainLooper())
    private val text = TextView(activity).apply {
        setTextColor(activity.getColor(R.color.text))
        textSize = 14f
        text = "Conectando con la PC…"
    }
    private val bar = ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal).apply {
        max = 1000
        isIndeterminate = true
        progressTintList = activity.getColorStateList(R.color.cyan)
    }
    private val started = android.os.SystemClock.elapsedRealtime()
    private var lastUpdate = 0L
    private val dialog: AlertDialog = AlertDialog.Builder(activity)
        .setTitle(title)
        .setView(LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(activity.dp(24), activity.dp(8), activity.dp(24), 0)
            addView(bar, LinearLayout.LayoutParams(-1, activity.dp(10)))
            addView(text, LinearLayout.LayoutParams(-1, -2).apply { topMargin = activity.dp(10) })
        })
        .setNegativeButton("Cancelar") { _, _ -> cancelled = true }
        .setCancelable(false)
        .show()

    fun update(done: Long, total: Long) {
        if (cancelled) throw java.io.InterruptedIOException("Cancelado")
        val now = android.os.SystemClock.elapsedRealtime()
        if (now - lastUpdate < 200 && done != total) return  // no saturar la interfaz
        lastUpdate = now
        val seconds = maxOf(0.5, (now - started) / 1000.0)
        val speed = done / seconds
        val line = buildString {
            append(Transfers.readable(done))
            if (total > 0) append(" de ${Transfers.readable(total)}  (${done * 100 / total} %)")
            append("\n${Transfers.readable(speed.toLong())}/s")
            if (total > 0 && speed > 0) {
                val left = ((total - done) / speed).toLong()
                append("  ·  faltan " + if (left >= 60) "${left / 60} min ${left % 60} s" else "$left s")
            }
        }
        ui.post {
            if (total > 0) {
                bar.isIndeterminate = false
                bar.progress = (done * 1000 / total).toInt()
            }
            text.text = line
        }
    }

    fun close() {
        if (dialog.isShowing) dialog.dismiss()
    }
}
