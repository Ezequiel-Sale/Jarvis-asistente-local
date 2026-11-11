package com.jarvis.movil

import android.app.Activity
import android.app.KeyguardManager
import android.content.Intent
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal
import android.os.SystemClock
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/**
 * Huella (o PIN / patrón del teléfono) para abrir la app y para aprobar acciones de
 * riesgo alto. Usa el BiometricPrompt de Android (sin bibliotecas); en Android 8–9,
 * la pantalla de bloqueo del teléfono.
 *
 * La app se bloquea al abrirla y al volver después de ``GRACE_MS`` en segundo plano.
 * Pasar de una pantalla de la app a otra no cuenta como salir.
 */
object Lock {
    const val GRACE_MS = 2 * 60 * 1000L
    private const val REQUEST_CODE = 4711

    @Volatile
    private var started = 0
    private var hiddenAt = 0L
    private var unlocked = false
    private var pending: (() -> Unit)? = null
    private var pendingFail: (() -> Unit)? = null

    /** Si alguna pantalla de la app está a la vista (el servicio no notifica entonces). */
    val visible: Boolean
        get() = started > 0

    fun enabled(activity: Activity): Boolean =
        activity.getSharedPreferences("jarvis", Activity.MODE_PRIVATE).getBoolean("huella", true)

    /** Si el teléfono tiene bloqueo de pantalla (sin él no hay nada que pedir). */
    fun available(activity: Activity): Boolean =
        activity.getSystemService(KeyguardManager::class.java)?.isDeviceSecure == true

    /** Llamar en onStart de cada pantalla: tapa el contenido si hay que desbloquear. */
    fun onStart(activity: Activity, root: ViewGroup) {
        val first = started++ == 0
        if (first && unlocked && SystemClock.elapsedRealtime() - hiddenAt > GRACE_MS) unlocked = false
        if (!unlocked && enabled(activity) && available(activity)) showCover(activity, root)
    }

    fun onStop() {
        started = maxOf(0, started - 1)
        if (started == 0) hiddenAt = SystemClock.elapsedRealtime()
    }

    private fun showCover(activity: Activity, root: ViewGroup) {
        if (root.findViewWithTag<View>("jarvis-lock") != null) return
        val cover = LinearLayout(activity).apply {
            tag = "jarvis-lock"
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(activity.getColor(R.color.bg))
            isClickable = true  // no deja tocar lo de abajo
            elevation = 100f
        }
        cover.addView(TextView(activity).apply {
            text = "🔒  J.A.R.V.I.S."
            setTextColor(activity.getColor(R.color.cyan))
            textSize = 22f
            gravity = Gravity.CENTER
        })
        cover.addView(Button(activity).apply {
            text = "DESBLOQUEAR"
            setTextColor(activity.getColor(R.color.bg))
            backgroundTintList = activity.getColorStateList(R.color.cyan)
            setOnClickListener { unlock(activity, cover) }
        }, LinearLayout.LayoutParams(-2, -2).apply { topMargin = 48 })
        root.addView(cover, ViewGroup.LayoutParams(-1, -1))
        cover.post { unlock(activity, cover) }
    }

    private fun unlock(activity: Activity, cover: View) {
        authenticate(activity, "Desbloquear Jarvis", "Usa tu huella o el bloqueo del teléfono", onSuccess = {
            unlocked = true
            (cover.parent as? ViewGroup)?.removeView(cover)
        })
    }

    /** Pide huella; si el teléfono no tiene bloqueo, sigue sin pedir nada. */
    fun authenticate(
        activity: Activity,
        title: String,
        subtitle: String,
        onSuccess: () -> Unit,
        onFail: () -> Unit = {},
    ) {
        if (!available(activity)) {
            Toast.makeText(activity, "Tu celular no tiene bloqueo de pantalla: no puedo pedir huella.", Toast.LENGTH_LONG).show()
            onSuccess()
            return
        }
        if (Build.VERSION.SDK_INT >= 29) {
            val builder = BiometricPrompt.Builder(activity).setTitle(title).setSubtitle(subtitle)
            if (Build.VERSION.SDK_INT >= 30) {
                builder.setAllowedAuthenticators(
                    BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL
                )
            } else {
                @Suppress("DEPRECATION")
                builder.setDeviceCredentialAllowed(true)
            }
            builder.build().authenticate(CancellationSignal(), activity.mainExecutor,
                object : BiometricPrompt.AuthenticationCallback() {
                    override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = onSuccess()
                    override fun onAuthenticationError(errorCode: Int, errString: CharSequence) = onFail()
                })
            return
        }
        // Android 8–9: la pantalla de bloqueo del sistema (PIN, patrón o huella).
        val keyguard = activity.getSystemService(KeyguardManager::class.java)
        @Suppress("DEPRECATION")
        val intent: Intent? = keyguard?.createConfirmDeviceCredentialIntent(title, subtitle)
        if (intent == null) {
            onSuccess()
            return
        }
        pending = onSuccess
        pendingFail = onFail
        @Suppress("DEPRECATION")
        activity.startActivityForResult(intent, REQUEST_CODE)
    }

    /** Llamar desde onActivityResult de cada pantalla (solo se usa en Android 8–9). */
    fun onActivityResult(requestCode: Int, resultCode: Int): Boolean {
        if (requestCode != REQUEST_CODE) return false
        val ok = pending
        val fail = pendingFail
        pending = null
        pendingFail = null
        if (resultCode == Activity.RESULT_OK) ok?.invoke() else fail?.invoke()
        return true
    }
}
