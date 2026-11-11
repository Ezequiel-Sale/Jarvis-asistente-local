package com.jarvis.movil

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.media.ToneGenerator
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.view.KeyEvent
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Servicio en primer plano que mantiene la conexión con Jarvis aunque la app esté
 * cerrada: notifica respuestas, recordatorios, alertas y confirmaciones (con SÍ / NO),
 * lee las respuestas con la voz de Piper y deja hablar con el botón de los auriculares.
 *
 * Mientras la app está a la vista no notifica nada: el chat ya lo muestra.
 */
class JarvisService : Service() {

    companion object {
        const val CHANNEL_CONNECTION = "conexion"
        const val CHANNEL_REPLIES = "respuestas"
        const val CHANNEL_ALERTS = "avisos"
        const val CHANNEL_QUESTIONS = "confirmaciones"
        const val ID_FOREGROUND = 1
        const val ID_REPLY = 2
        const val ID_QUESTION = 3
        private const val HEADSET_WINDOW_MS = 3 * 60 * 1000L

        /** Arranca (o refresca) el servicio si la app está vinculada y los avisos están activos. */
        fun start(context: Context) {
            val prefs = context.getSharedPreferences("jarvis", MODE_PRIVATE)
            if (!prefs.getBoolean("avisos", true) || prefs.getString("token", null).isNullOrEmpty()) return
            try {
                context.startForegroundService(Intent(context, JarvisService::class.java))
            } catch (e: Exception) {
                // Android no deja arrancarlo ahora (p. ej. desde segundo plano): se intentará al abrir la app.
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, JarvisService::class.java))
        }
    }

    private val prefs by lazy { getSharedPreferences("jarvis", MODE_PRIVATE) }
    private val manager by lazy { getSystemService(NotificationManager::class.java) }

    @Volatile
    private var generation = 0
    private var session: MediaSession? = null
    private var recorder: Recorder? = null
    private var micAllowed = false
    private var connected = true
    private var nextAlertId = 100
    private var pendingQuestion: String? = null

    @Volatile
    private var headsetAt = 0L

    private fun api(): Api? {
        val base = prefs.getString("base", null)
        val token = prefs.getString("token", null)
        return if (base.isNullOrEmpty() || token.isNullOrEmpty()) null else Api(base, token)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        goForeground()
        if (session == null) setupHeadset()
        val client = api()
        if (client == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        val mine = ++generation
        thread(name = "avisos") { poll(client, mine) }
        return START_STICKY
    }

    override fun onDestroy() {
        generation++
        recorder?.stop()
        recorder = null
        Speaker.onPlaying = null
        session?.release()
        session = null
        super.onDestroy()
    }

    // --- Notificación fija y canales ---------------------------------------------------

    private fun createChannels() {
        manager.createNotificationChannels(listOf(
            NotificationChannel(CHANNEL_CONNECTION, "Conexión con la PC", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Aviso fijo mientras la app está conectada en segundo plano."
            },
            NotificationChannel(CHANNEL_REPLIES, "Respuestas de Jarvis", NotificationManager.IMPORTANCE_DEFAULT),
            NotificationChannel(CHANNEL_ALERTS, "Recordatorios y alertas del equipo", NotificationManager.IMPORTANCE_HIGH),
            NotificationChannel(CHANNEL_QUESTIONS, "Confirmaciones", NotificationManager.IMPORTANCE_HIGH),
        ))
    }

    private fun openApp(): PendingIntent = PendingIntent.getActivity(
        this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun foregroundNotification(text: String): Notification =
        Notification.Builder(this, CHANNEL_CONNECTION)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Jarvis")
            .setContentText(text)
            .setContentIntent(openApp())
            .setOngoing(true)
            .build()

    private fun foregroundText(): String = when {
        recorder != null -> "🎙 Grabando… pulsa otra vez el botón de los auriculares para enviar"
        !connected -> "Sin conexión con la PC (¿Tailscale activado?)"
        else -> "Conectado a tu PC"
    }

    /** Con micrófono si se puede (solo arrancando con la app a la vista); si no, solo datos. */
    private fun goForeground() {
        val notification = foregroundNotification(foregroundText())
        if (Build.VERSION.SDK_INT < 29) {
            startForeground(ID_FOREGROUND, notification)
            micAllowed = hasMicPermission()
            return
        }
        val data = ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        if (hasMicPermission()) {
            try {
                startForeground(ID_FOREGROUND, notification, data or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
                micAllowed = true
                return
            } catch (e: Exception) {
                // Arrancado en segundo plano (p. ej. al encender el teléfono): sin micrófono por ahora.
            }
        }
        startForeground(ID_FOREGROUND, notification, data)
        micAllowed = false
    }

    private fun hasMicPermission() =
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun refreshForeground() = manager.notify(ID_FOREGROUND, foregroundNotification(foregroundText()))

    // --- Escucha de eventos --------------------------------------------------------------

    private fun poll(client: Api, mine: Int) {
        var last = -1
        while (mine == generation) {
            try {
                val initial = last < 0
                val reply = client.getJson("/api/events?since=$last&wait=${if (initial) 0 else 25}", 40000)
                last = reply.getInt("last")
                setConnected(true)
                if (initial) continue  // lo anterior ya se vio
                val pending = if (reply.isNull("pending")) null else reply.optString("pending")
                val events = reply.getJSONArray("events")
                for (i in 0 until events.length()) handle(client, events.getJSONObject(i), pending)
            } catch (e: ApiException) {
                if (e.code == 401) {
                    stopSelf()
                    return
                }
                setConnected(false)
                Thread.sleep(5000)
            } catch (e: Exception) {
                setConnected(false)
                Thread.sleep(5000)
            }
        }
    }

    private fun setConnected(value: Boolean) {
        if (connected == value) return
        connected = value
        refreshForeground()
    }

    private fun handle(client: Api, event: JSONObject, pending: String?) {
        val text = event.optString("text")
        val visible = Lock.visible
        when (event.optString("type")) {
            "assistant" -> {
                val alert = event.optString("alert")
                if (!visible) {
                    if (alert.isNotEmpty()) notifyAlert(text, alert == "alarm") else notifyReply(text)
                    val fromHeadset = SystemClock.elapsedRealtime() - headsetAt < HEADSET_WINDOW_MS
                    if (prefs.getBoolean("leer", false) || fromHeadset) Speaker.say(this, client, text)
                }
            }
            "question" -> if (!visible && event.optString("qid") == pending) notifyQuestion(event)
            "resolved" -> if (event.optString("qid") == pendingQuestion) {
                manager.cancel(ID_QUESTION)
                pendingQuestion = null
            }
        }
    }

    private fun notifyReply(text: String) {
        manager.notify(ID_REPLY, Notification.Builder(this, CHANNEL_REPLIES)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Jarvis")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(openApp())
            .setAutoCancel(true)
            .build())
    }

    private fun notifyAlert(text: String, alarm: Boolean) {
        manager.notify(nextAlertId++, Notification.Builder(this, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(if (alarm) "⏰ Jarvis" else "Jarvis: aviso del equipo")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setCategory(if (alarm) Notification.CATEGORY_REMINDER else Notification.CATEGORY_STATUS)
            .setContentIntent(openApp())
            .setAutoCancel(true)
            .build())
    }

    private fun notifyQuestion(event: JSONObject) {
        val qid = event.optString("qid")
        val high = event.optBoolean("high")
        pendingQuestion = qid
        val yesLabel = (if (high) "🔒 " else "") + event.optString("yes", "SÍ")
        // Riesgo alto: el SÍ abre la app para pedir la huella; si no, responde desde aquí.
        val yes = if (high) {
            PendingIntent.getActivity(this, 11,
                Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    .putExtra(NotifyReceiver.EXTRA_QID, qid)
                    .putExtra(NotifyReceiver.EXTRA_APPROVE, true)
                    .putExtra(NotifyReceiver.EXTRA_TEXT, event.optString("text")),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        } else NotifyReceiver.answerIntent(this, qid, true, 12)
        val no = NotifyReceiver.answerIntent(this, qid, false, 13)
        manager.notify(ID_QUESTION, Notification.Builder(this, CHANNEL_QUESTIONS)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Jarvis pregunta")
            .setContentText(event.optString("text"))
            .setStyle(Notification.BigTextStyle().bigText(event.optString("text")))
            .setCategory(Notification.CATEGORY_CALL)
            .setContentIntent(openApp())
            .addAction(Notification.Action.Builder(null, yesLabel, yes).build())
            .addAction(Notification.Action.Builder(null, event.optString("no", "NO"), no).build())
            .setAutoCancel(true)
            .build())
    }

    // --- Botón de los auriculares --------------------------------------------------------

    /**
     * Android manda el botón de los auriculares a la última app que reprodujo sonido:
     * funciona cuando Jarvis fue la última (p. ej. después de que te leyó una respuesta)
     * o cuando no suena nada más. Si estás con música, el botón sigue siendo de la música.
     */
    private fun setupHeadset() {
        if (!prefs.getBoolean("auriculares", true)) return
        val media = MediaSession(this, "Jarvis")
        media.setCallback(object : MediaSession.Callback() {
            override fun onMediaButtonEvent(intent: Intent): Boolean {
                val key: KeyEvent? = if (Build.VERSION.SDK_INT >= 33) {
                    intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT, KeyEvent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT)
                }
                val handled = key != null && key.keyCode in listOf(
                    KeyEvent.KEYCODE_HEADSETHOOK, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                    KeyEvent.KEYCODE_MEDIA_PLAY, KeyEvent.KEYCODE_MEDIA_PAUSE,
                )
                if (!handled) return super.onMediaButtonEvent(intent)
                if (key?.action == KeyEvent.ACTION_UP && key.repeatCount == 0) toggleRecording()
                return true
            }
        })
        session = media
        setPlaying(false)
        media.isActive = true
        Speaker.onPlaying = { playing -> setPlaying(playing) }
    }

    private fun setPlaying(playing: Boolean) {
        session?.setPlaybackState(PlaybackState.Builder()
            .setActions(PlaybackState.ACTION_PLAY_PAUSE or PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE)
            .setState(if (playing) PlaybackState.STATE_PLAYING else PlaybackState.STATE_PAUSED, 0, 1f)
            .build())
    }

    private fun beep(times: Int) {
        thread {
            val tone = ToneGenerator(AudioManager.STREAM_MUSIC, 80)
            repeat(times) {
                tone.startTone(ToneGenerator.TONE_PROP_BEEP, 120)
                Thread.sleep(200)
            }
            tone.release()
        }
    }

    @Synchronized
    private fun toggleRecording() {
        val current = recorder
        if (current != null) {
            recorder = null
            beep(2)
            refreshForeground()
            val wav = current.stop()
            val client = api() ?: return
            if (wav == null) return
            headsetAt = SystemClock.elapsedRealtime()
            thread {
                try {
                    client.postWav("/api/audio", wav)
                } catch (e: Exception) {
                    notifyAlert("No pude enviar lo que dijiste: ${e.message ?: "sin conexión"}", false)
                }
            }
            return
        }
        if (!micAllowed) {
            notifyAlert("Para hablar con el botón de los auriculares, abre la app de Jarvis una vez " +
                "(Android no deja usar el micrófono si el servicio arrancó solo).", false)
            return
        }
        Speaker.stop()
        val fresh = Recorder()
        if (!fresh.start()) {
            notifyAlert("No pude usar el micrófono.", false)
            return
        }
        recorder = fresh
        beep(1)
        refreshForeground()
    }
}
