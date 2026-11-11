package com.jarvis.movil

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import java.io.File
import java.net.URLEncoder

/** Subir archivos a la PC (a Descargas) y bajar archivos de la PC al celular. */
object Transfers {

    fun readable(bytes: Long): String = when {
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> "%.0f KB".format(bytes / 1024.0)
        bytes < 1024L * 1024 * 1024 -> "%.1f MB".format(bytes / 1048576.0)
        else -> "%.2f GB".format(bytes / 1073741824.0)
    }

    /** Nombre y tamaño de un archivo compartido (el tamaño puede no saberse: -1). */
    fun describe(context: Context, uri: Uri): Pair<String, Long> {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "archivo"
        var size = -1L
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
            null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                cursor.getString(0)?.let { name = it }
                if (!cursor.isNull(1)) size = cursor.getLong(1)
            }
        }
        return name to size
    }

    /**
     * Sube ``uri`` a Descargas de la PC. Si el tamaño no se conoce, primero se copia
     * a la caché (el servidor necesita Content-Length).
     */
    fun upload(context: Context, api: Api, uri: Uri, progress: (Long, Long) -> Unit): String {
        val (name, knownSize) = describe(context, uri)
        var temporary: File? = null
        try {
            val size = if (knownSize >= 0) knownSize else {
                val copy = File(context.cacheDir, "subida.tmp")
                context.contentResolver.openInputStream(uri)!!.use { input ->
                    copy.outputStream().use { input.copyTo(it) }
                }
                temporary = copy
                copy.length()
            }
            val stream = temporary?.inputStream() ?: context.contentResolver.openInputStream(uri)
                ?: throw IllegalStateException("No pude leer «$name».")
            stream.use {
                val path = "/api/upload?name=" + URLEncoder.encode(name, "UTF-8")
                return api.postStream(path, it, size) { sent -> progress(sent, size) }.optString("name", name)
            }
        } finally {
            temporary?.delete()
        }
    }

    /**
     * Baja un archivo de la PC a «Descargas/Jarvis» del teléfono. Devuelve un Uri para
     * abrirlo (en Android 8–9 queda en la carpeta de la app y devuelve null).
     */
    fun download(context: Context, api: Api, root: String, path: String, name: String,
                 progress: (Long, Long) -> Unit): Uri? {
        val query = "/api/files/download?root=" + URLEncoder.encode(root, "UTF-8") + "&path=" +
            URLEncoder.encode(path, "UTF-8")
        if (Build.VERSION.SDK_INT >= 29) {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Jarvis")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val resolver = context.contentResolver
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IllegalStateException("No pude crear el archivo en Descargas.")
            try {
                resolver.openOutputStream(uri)!!.use { api.download(query, it, progress) }
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
                return uri
            } catch (e: Exception) {
                resolver.delete(uri, null, null)
                throw e
            }
        }
        val folder = File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "Jarvis").apply { mkdirs() }
        File(folder, name).outputStream().use { api.download(query, it, progress) }
        return null
    }

    fun openIntent(context: Context, uri: Uri): Intent =
        Intent(Intent.ACTION_VIEW).setDataAndType(uri, context.contentResolver.getType(uri) ?: "*/*")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
}
