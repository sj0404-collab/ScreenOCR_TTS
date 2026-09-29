package com.screenocr.client

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import com.screenocr.client.ui.TranslatorScreen
import com.screenocr.client.ui.TranslatorState

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val state = remember { TranslatorState(applicationContext) }
            LaunchedEffect(state) { state.loadDictionaries() }
            DisposableEffect(state) { onDispose { state.engine.shutdown() } }
            TranslatorScreen(state)
        }
    }
}
