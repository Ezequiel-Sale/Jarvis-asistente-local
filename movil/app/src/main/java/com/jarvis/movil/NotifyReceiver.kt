package com.jarvis.movil

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import kotlin.concurrent.thread

/** Botones SÍ / NO de la notificación de una confirmación (las de riesgo alto abren la app). */
class NotifyReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_ANSWER = "com.jarvis.movil.RESPONDER"
        const val EXTRA_QID = "qid"
        const val EXTRA_APPROVE = "aprobar"
        const val EXTRA_TEXT = "texto"
        const val ACTION_COPY = "com.jarvis.movil.COPIAR"
        const val ACTION_WAKE = "com.jarvis.movil.ENCENDER"

        fun wakeIntent(context: Context): PendingIntent =
            PendingIntent.getBroadcast(context, 40,
                Intent(context, NotifyReceiver::class.java).setAction(ACTION_WAKE),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        /** Botón «Copiar» del portapapeles que manda la PC (escribir el portapapeles sí se permite en segundo plano). */
        fun copyIntent(context: Context, text: String, code: Int): PendingIntent =
            PendingIntent.getBroadcast(context, code,
                Intent(context, NotifyReceiver::class.java).setAction(ACTION_COPY).putExtra(EXTRA_TEXT, text),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        fun answerIntent(context: Context, qid: String, approve: Boolean, code: Int): PendingIntent =
            PendingIntent.getBroadcast(context, code,
                Intent(context, NotifyReceiver::class.java).setAction(ACTION_ANSWER)
                    .putExtra(EXTRA_QID, qid).putExtra(EXTRA_APPROVE, approve),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        /** Envía la respuesta a la PC; llama a ``done`` con el error o null. */
        fun send(context: Context, qid: String, approve: Boolean, done: (String?) -> Unit) {
            val prefs = context.getSharedPreferences("jarvis", Context.MODE_PRIVATE)
            val base = prefs.getString("base", null)
            val token = prefs.getString("token", null)
            if (base.isNullOrEmpty() || token.isNullOrEmpty()) return done("Este celular no está vinculado.")
            thread {
                val error = try {
                    Api(base, token).postJson("/api/answer", JSONObject().put("qid", qid).put("approve", approve))
                    null
                } catch (e: Exception) {
                    e.message ?: "No pude conectar con Jarvis."
                }
                done(error)
            }
        }
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == ACTION_WAKE) {
            val result = goAsync()
            thread {
                val message = Wake.send(context)
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    android.widget.Toast.makeText(context, message, android.widget.Toast.LENGTH_LONG).show()
                    result.finish()
                }
            }
            return
        }
        if (intent.action == ACTION_COPY) {
            val text = intent.getStringExtra(EXTRA_TEXT) ?: return
            context.getSystemService(android.content.ClipboardManager::class.java)
                .setPrimaryClip(android.content.ClipData.newPlainText("Portapapeles de la PC", text))
            context.getSystemService(NotificationManager::class.java).cancel(JarvisService.ID_CLIPBOARD)
            android.widget.Toast.makeText(context, "Copiado en el celular.", android.widget.Toast.LENGTH_SHORT).show()
            return
        }
        if (intent.action != ACTION_ANSWER) return
        val qid = intent.getStringExtra(EXTRA_QID) ?: return
        val result = goAsync()
        context.getSystemService(NotificationManager::class.java).cancel(JarvisService.ID_QUESTION)
        send(context, qid, intent.getBooleanExtra(EXTRA_APPROVE, false)) { error ->
            if (error != null) {
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    android.widget.Toast.makeText(context, "No se envió: $error", android.widget.Toast.LENGTH_LONG).show()
                }
            }
            result.finish()
        }
    }
}

/** Al encender el teléfono o actualizar la app, vuelve a escuchar los avisos. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            JarvisService.start(context)
        }
    }
}
