package com.screenocr.client

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View.GONE
import android.view.View.VISIBLE
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var serverView: WebView
    private lateinit var connectLayout: LinearLayout
    private lateinit var urlInput: EditText
    private lateinit var loading: ProgressBar
    private lateinit var prefs: android.content.SharedPreferences

    companion object {
        private const val PREF_URL = "server_url"
        private const val DEFAULT_URL = "https://screenocr-pro-server.trycloudflare.com"
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("screenocr", MODE_PRIVATE)

        serverView = WebView(this)
        serverView.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.MATCH_PARENT
        )
        serverView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            useWideViewPort = true
            loadWithOverviewMode = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }
        serverView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, progress: Int) {
                if (progress < 100) {
                    if (loading.visibility != VISIBLE) loading.visibility = VISIBLE
                } else {
                    loading.visibility = GONE
                }
            }
        }
        serverView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    // внешние ссылки (напр. Google translate) — тоже внутри WebView
                    view?.loadUrl(url)
                    return true
                }
                return false
            }
        }

        connectLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(50, 120, 50, 50)
            setBackgroundColor(0xFF0D1117.toInt())
        }
        val title = TextView(this).apply {
            text = "🔍 ScreenOCR Client"
            textSize = 24f
            setTextColor(0xFF7B68EE.toInt())
            gravity = Gravity.CENTER
        }
        val hintText = TextView(this).apply {
            text = "Вставь URL проф-сервера из workflow GitHub\nи нажми «Подключиться»"
            textSize = 14f
            setTextColor(0xFF8B949E.toInt())
            gravity = Gravity.CENTER
        }
        urlInput = EditText(this).apply {
            setText(prefs.getString(PREF_URL, DEFAULT_URL))
            textSize = 14f
            hint = "https://….trycloudflare.com"
            setBackgroundColor(0xFF161B22.toInt())
            setTextColor(0xFFE6EDF3.toInt())
        }
        val connectBtn = Button(this).apply {
            text = "🔌 Подключиться"
            setOnClickListener { openUrl(urlInput.text.toString().trim()) }
        }
        val clearBtn = Button(this).apply {
            text = "Ввести другой адрес"
            setOnClickListener {
                prefs.edit().remove(PREF_URL).apply()
                serverView.visibility = GONE
                connectLayout.visibility = VISIBLE
                urlInput.setText(DEFAULT_URL)
            }
        }
        connectBtn.setOnClickListener { openUrl(urlInput.text.toString().trim()) }

        connectLayout.addView(title, lp())
        connectLayout.addView(hintText, lp(marginTop = 12))
        connectLayout.addView(urlInput, lp(marginTop = 24))
        connectLayout.addView(connectBtn, lp(marginTop = 18))
        connectLayout.addView(clearBtn, lp(marginTop = 10))

        loading = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
        }

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(serverView)
        root.addView(loading, lp())
        root.addView(connectLayout)

        serverView.visibility = GONE
        loading.visibility = GONE
        setContentView(root)

        val saved = prefs.getString(PREF_URL, "")
        if (saved?.isNotBlank() == true && (saved.startsWith("http://") || saved.startsWith("https://"))) {
            openUrl(saved)
        }
    }

    private fun lp(marginTop: Int = 0) = LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.MATCH_PARENT,
        LinearLayout.LayoutParams.WRAP_CONTENT
    ).apply { topMargin = marginTop }

    private fun openUrl(url: String) {
        if (url.isBlank()) return
        val fixed = if (!url.startsWith("http")) "http://$url" else url
        prefs.edit().putString(PREF_URL, fixed).apply()
        connectLayout.visibility = GONE
        serverView.visibility = VISIBLE
        serverView.loadUrl(fixed)
    }

    override fun onBackPressed() {
        if (serverView.visibility == VISIBLE && serverView.canGoBack()) {
            serverView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        serverView.destroy()
        super.onDestroy()
    }
}