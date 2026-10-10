package com.wifiaudio.streamer.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.wifiaudio.streamer.AppTab
import com.wifiaudio.streamer.R

/** App shell: brand header, Audio / Files tabs, shared snackbar. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AirwireApp(
    tab: AppTab,
    onTabChange: (AppTab) -> Unit,
    snackbarHostState: SnackbarHostState,
    audio: @Composable () -> Unit,
    files: @Composable () -> Unit,
) {
    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        contentWindowInsets = WindowInsets.safeDrawing, // includes the keyboard
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            BrandHeader(Modifier.padding(start = 20.dp, end = 20.dp, top = 16.dp, bottom = 8.dp))
            PrimaryTabRow(selectedTabIndex = tab.ordinal, modifier = Modifier.fillMaxWidth()) {
                AppTab.entries.forEach { t ->
                    Tab(
                        selected = t == tab,
                        onClick = { onTabChange(t) },
                        text = { Text(t.label) },
                    )
                }
            }
            Box(Modifier.weight(1f)) {
                when (tab) {
                    AppTab.AUDIO -> audio()
                    AppTab.FILES -> files()
                }
            }
        }
    }
}

@Composable
private fun BrandHeader(modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Image(
            painter = painterResource(R.drawable.ic_airwire_logo),
            contentDescription = null,
            modifier = Modifier.size(44.dp),
        )
        Spacer(Modifier.width(12.dp))
        Column {
            Text("Airwire", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Text(
                "The audio cable, minus the cable.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
