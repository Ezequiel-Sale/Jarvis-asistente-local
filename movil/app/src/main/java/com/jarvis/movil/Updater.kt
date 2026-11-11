package com.jarvis.movil

import android.app.Activity
import android.app.AlertDialog
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.Toast
import java.security.MessageDigest
import kotlin.concurrent.thread

/**
 * Actualización desde la PC: /api/app dice qué versión tiene Jarvis y /api/app/apk la
 * entrega. Se comprueba el SHA-256 y se instala con PackageInstaller; Android siempre
 * muestra su pantalla de confirmación (no se puede saltar, y está bien que así sea).
 */
class Updater(private val activity: Activity, private val api: Api) {

    private val ui = Handler(Looper.getMainLooper())
    private val prefs = activity.getSharedPreferences("jarvis", Activity.MODE_PRIVATE)

    private fun installedVersion(): Long {
        val info = activity.packageManager.getPackageInfo(activity.packageName, 0)
        return if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
    }

    /** ``manual``: desde el menú (avisa también si no hay nada nuevo y no espera las 6 h). */
    fun check(manual: Boolean) {
        val now = System.currentTimeMillis()
        if (!manual && now - prefs.getLong("update_checked", 0) < CHECK_EVERY_MS &&
            !prefs.getBoolean("update_pending", false)) return
        prefs.edit().putLong("update_checked", now).apply()
        thread(name = "actualizacion") {
            try {
                val info = api.getJson("/api/app")
                val remote = info.getLong("versionCode")
                val name = info.optString("versionName")
                if (remote <= installedVersion()) {
                    prefs.edit().putBoolean("update_pending", false).apply()
                    if (manual) ui.post { toast("Ya tienes la última versión de la app.") }
                    return@thread
                }
                ui.post { offer(name, info.getString("sha256")) }
            } catch (e: ApiException) {
                if (manual) ui.post { toast(e.message ?: "No pude buscar actualizaciones.") }
            } catch (e: Exception) {
                if (manual) ui.post { toast("No pude conectar con Jarvis para buscar actualizaciones.") }
            }
        }
    }

    private fun offer(name: String, sha256: String) {
        if (activity.isFinishing) return
        AlertDialog.Builder(activity)
            .setTitle("Hay una versión nueva de la app")
            .setMessage("Versión $name. Se descarga desde tu PC y Android te pedirá confirmar la instalación.")
            .setPositiveButton("Actualizar") { _, _ -> start(sha256) }
            .setNegativeButton("Más tarde", null)
            .show()
    }

    private fun start(sha256: String) {
        if (Build.VERSION.SDK_INT >= 26 && !activity.packageManager.canRequestPackageInstalls()) {
            prefs.edit().putBoolean("update_pending", true).apply()
            AlertDialog.Builder(activity)
                .setMessage("Para actualizarse sola, Android necesita que permitas que la app de Jarvis instale " +
                    "aplicaciones. Actívalo en la pantalla que se abre y vuelve: la actualización seguirá.")
                .setPositiveButton("Abrir ajustes") { _, _ ->
                    activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + activity.packageName)))
                }
                .setNegativeButton("Cancelar", null)
                .show()
            return
        }
        prefs.edit().putBoolean("update_pending", false).apply()
        toast("Descargando la actualización…")
        thread(name = "descarga-app") {
            try {
                val apk = api.getBytes("/api/app/apk")
                val digest = MessageDigest.getInstance("SHA-256").digest(apk).joinToString("") { "%02x".format(it) }
                if (!digest.equals(sha256, ignoreCase = true)) throw IllegalStateException("La descarga llegó dañada.")
                install(apk)
            } catch (e: Exception) {
                ui.post { toast("No pude actualizar: ${e.message ?: "error desconocido"}") }
            }
        }
    }

    private fun install(apk: ByteArray) {
        val installer = activity.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(activity.packageName)
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("jarvis.apk", 0, apk.size.toLong()).use { out ->
                out.write(apk)
                session.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val intent = Intent(activity, InstallReceiver::class.java)
            session.commit(PendingIntent.getBroadcast(activity, id, intent, flags).intentSender)
        }
    }

    private fun toast(text: String) = Toast.makeText(activity, text, Toast.LENGTH_LONG).show()

    companion object {
        const val CHECK_EVERY_MS = 6 * 60 * 60 * 1000L
    }
}

/** Resultado de PackageInstaller: abre la confirmación de Android o avisa del error. */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm: Intent? = if (Build.VERSION.SDK_INT >= 33) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(Intent.EXTRA_INTENT)
                }
                confirm?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                if (confirm != null) context.startActivity(confirm)
            }
            PackageInstaller.STATUS_SUCCESS -> Unit  // Android reinicia la app ya actualizada
            else -> {
                val reason = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "error desconocido"
                Toast.makeText(context, "No se instaló la actualización: $reason", Toast.LENGTH_LONG).show()
            }
        }
    }
}
