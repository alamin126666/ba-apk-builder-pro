package @@PACKAGE_NAME@@

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.ByteArrayInputStream
import java.util.Locale

internal object WebViewManager {
    private const val LOCAL_HOST = "appassets.androidplatform.net"
    private const val START_URL = "https://$LOCAL_HOST/index.html"

    @SuppressLint("SetJavaScriptEnabled")
    fun create(context: Context): WebView = WebView(context).apply {
        settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            loadsImagesAutomatically = true
            databaseEnabled = false
        }
        isSaveEnabled = false
        webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                val uri = request.url
                if (!uri.host.equals(LOCAL_HOST, ignoreCase = true)) return null
                val decoded = Uri.decode(uri.encodedPath ?: "/").trimStart('/').ifEmpty { "index.html" }
                if (decoded.split('/').any { it == ".." || it == "." } || '\\' in decoded) {
                    return response(400, "Bad Request", "text/plain", ByteArray(0))
                }
                val data = NativeLoader.loadResource(decoded)
                    ?: return response(404, "Not Found", "text/plain", ByteArray(0))
                val mime = mimeType(decoded)
                val charset = if (mime.startsWith("text/") || mime == "application/javascript" || mime == "application/json") "UTF-8" else null
                return response(200, "OK", mime, data, charset)
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                !request.url.host.equals(LOCAL_HOST, ignoreCase = true)

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                if (!url.startsWith("https://$LOCAL_HOST/")) view.stopLoading()
            }
        }
        loadUrl(START_URL)
    }

    private fun response(status: Int, reason: String, mime: String, data: ByteArray, charset: String? = null) =
        WebResourceResponse(mime, charset, status, reason, mapOf("Cache-Control" to "no-store"), ByteArrayInputStream(data))

    private fun mimeType(path: String): String = when (path.substringAfterLast('.', "").lowercase(Locale.ROOT)) {
        "html", "htm" -> "text/html"
        "css" -> "text/css"
        "js", "mjs" -> "application/javascript"
        "json", "webmanifest" -> "application/json"
        "svg" -> "image/svg+xml"
        "png" -> "image/png"
        "jpg", "jpeg" -> "image/jpeg"
        "webp" -> "image/webp"
        "gif" -> "image/gif"
        "ico" -> "image/x-icon"
        "avif" -> "image/avif"
        "woff" -> "font/woff"
        "woff2" -> "font/woff2"
        "ttf" -> "font/ttf"
        "otf" -> "font/otf"
        "eot" -> "application/vnd.ms-fontobject"
        "wasm" -> "application/wasm"
        "mp3" -> "audio/mpeg"
        "m4a", "aac" -> "audio/mp4"
        "ogg" -> "audio/ogg"
        "wav" -> "audio/wav"
        "mp4" -> "video/mp4"
        "m4v" -> "video/mp4"
        "mov" -> "video/quicktime"
        "mpg", "mpeg" -> "video/mpeg"
        "webm" -> "video/webm"
        "pdf" -> "application/pdf"
        "xml" -> "application/xml"
        "txt", "map" -> "text/plain"
        else -> "application/octet-stream"
    }
}
