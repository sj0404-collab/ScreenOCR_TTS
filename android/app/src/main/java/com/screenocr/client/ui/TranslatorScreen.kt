package com.screenocr.client.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.Scaffold
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.screenocr.client.core.TranslationResult

private val ScreenOcrColors = darkColorScheme(
    primary = Color(0xFF7B68EE),
    secondary = Color(0xFF58A6FF),
    background = Color(0xFF0D1117),
    surface = Color(0xFF161B22),
    onBackground = Color(0xFFE6EDF3),
    onSurface = Color(0xFFE6EDF3),
    onSurfaceVariant = Color(0xFF8B949E),
    error = Color(0xFFF85149),
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TranslatorScreen(state: TranslatorState) {
    MaterialTheme(colorScheme = ScreenOcrColors) {
        Scaffold(
            topBar = { TopAppBar(title = { Text("ScreenOCR · озвучка текста") }) },
        ) { padding ->
            Column(
                modifier = Modifier
                    .padding(padding)
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                OutlinedTextField(
                    value = state.input,
                    onValueChange = state::onInputChange,
                    label = { Text("Текст с экрана") },
                    placeholder = { Text("Вставь распознанную реплику") },
                    minLines = 3,
                    modifier = Modifier.fillMaxWidth(),
                )

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = state::speak,
                        enabled = state.canSpeak,
                    ) {
                        Text(if (state.speaking) "Озвучивает…" else "Озвучить")
                    }
                    OutlinedButton(onClick = state::stop, enabled = state.speaking) {
                        Text("Стоп")
                    }
                    OutlinedButton(
                        onClick = { state.translate() },
                        enabled = state.dictStatus == DictStatus.Ready,
                    ) {
                        Text("Перевести")
                    }
                }

                Text(state.ttsStatus, style = MaterialTheme.typography.bodyMedium)

                when (state.dictStatus) {
                    DictStatus.Loading -> LoadingRow()
                    DictStatus.Failed -> Text(
                        "Не удалось прочитать словари из assets. Собери APK заново: задача syncDictAssets копирует их в сборку.",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    DictStatus.Ready -> state.result?.let { ResultBlock(it) }
                }
            }
        }
    }
}

@Composable
private fun LoadingRow() {
    Row(
        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CircularProgressIndicator()
        Text("Загружаю словари…", style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun ResultBlock(result: TranslationResult) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Tag("вход: ${result.sourceLanguage}")
                Tag("озвучка: ${result.language}")
            }
            if (result.cleaned != result.input) {
                Labeled("После очистки", result.cleaned)
            }
            Labeled("Перевод", result.spoken.ifEmpty { "—" })
            if (result.needsBetterTranslation) {
                Text(
                    "Словарь не покрыл текст: часть слов осталась латиницей.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
    Spacer(Modifier.height(4.dp))
}

@Composable
private fun Labeled(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            label,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.labelSmall,
        )
        Text(value, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun Tag(text: String) {
    Surface(
        color = MaterialTheme.colorScheme.primary.copy(alpha = 0.18f),
        shape = RoundedCornerShape(6.dp),
    ) {
        Text(
            text,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelMedium,
        )
    }
}
