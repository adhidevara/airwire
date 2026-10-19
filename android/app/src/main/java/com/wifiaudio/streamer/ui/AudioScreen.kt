package com.wifiaudio.streamer.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.wifiaudio.streamer.AudioMode
import com.wifiaudio.streamer.AudioSourceType
import com.wifiaudio.streamer.StreamStats
import com.wifiaudio.streamer.StreamStatus
import com.wifiaudio.streamer.StreamViewModel
import com.wifiaudio.streamer.UiState
import com.wifiaudio.streamer.isActive
import com.wifiaudio.streamer.listen.ListenStats
import com.wifiaudio.streamer.listen.ListenStatus
import com.wifiaudio.streamer.listen.isActive
import com.wifiaudio.streamer.net.DiscoveredReceiver
import java.util.Locale
import kotlin.math.roundToInt

/** The Audio tab: direction, source, PC address, start/stop and quality. */
@Composable
fun AudioScreen(
    ui: UiState,
    status: StreamStatus,
    stats: StreamStats,
    listenStatus: ListenStatus,
    listenStats: ListenStats,
    paired: Boolean,
    onModeChange: (AudioMode) -> Unit,
    onHostChange: (String) -> Unit,
    onPortChange: (String) -> Unit,
    onSourceChange: (AudioSourceType) -> Unit,
    onBitrateChange: (Int) -> Unit,
    onScan: () -> Unit,
    onPickReceiver: (DiscoveredReceiver) -> Unit,
    onDismissScan: () -> Unit,
    onToggle: () -> Unit,
    onGoToFiles: () -> Unit,
) {
    val active = status.isActive || listenStatus.isActive
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        ModeSelector(ui.mode, enabled = !active, onChange = onModeChange)
        when (ui.mode) {
            AudioMode.SEND -> {
                StatusCard(status, stats)
                Section("Audio source") {
                    SourceSelector(ui.source, enabled = !active, onChange = onSourceChange)
                }
                Section("Receiver") {
                    ConnectionPanel(ui, enabled = !active, onHostChange, onPortChange, onScan)
                }
                BigButton(if (active) "STOP STREAMING" else "START STREAMING", active, onToggle)
            }
            AudioMode.LISTEN -> {
                ListenStatusCard(listenStatus, listenStats)
                if (!paired) PairFirstCard(onGoToFiles)
                Section("PC") {
                    ConnectionPanel(ui, enabled = !active, onHostChange, onPortChange, onScan)
                }
                BigButton(if (active) "STOP LISTENING" else "START LISTENING", active, onToggle)
            }
        }
        Section("Quality") {
            BitrateSlider(ui.bitrateKbps, onBitrateChange)
        }
    }
    ui.found?.let { ScanResultsDialog(it, ui.port, onPickReceiver, onDismissScan) }
}

@Composable
private fun ModeSelector(mode: AudioMode, enabled: Boolean, onChange: (AudioMode) -> Unit) {
    val options = listOf(AudioMode.SEND to "Phone → PC", AudioMode.LISTEN to "PC → Phone")
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        options.forEachIndexed { i, (value, label) ->
            SegmentedButton(
                selected = mode == value,
                onClick = { onChange(value) },
                enabled = enabled,
                shape = SegmentedButtonDefaults.itemShape(i, options.size),
                label = { Text(label) },
            )
        }
    }
}

@Composable
private fun ListenStatusCard(status: ListenStatus, stats: ListenStats) {
    val scheme = MaterialTheme.colorScheme
    val (title, dot) = when (status) {
        ListenStatus.Idle -> "Not playing" to scheme.outline
        is ListenStatus.Connecting -> "Connecting…" to scheme.tertiary
        is ListenStatus.Playing ->
            (if (stats.receiving) "Playing PC audio" else "Waiting for audio") to scheme.primary
        is ListenStatus.Failed -> "Error" to scheme.error
    }
    val container = if (status is ListenStatus.Playing) scheme.primaryContainer else scheme.surfaceContainerHigh
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = container)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(12.dp)
                        .clip(CircleShape)
                        .background(dot),
                )
                Spacer(Modifier.width(10.dp))
                Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            }
            when (status) {
                ListenStatus.Idle -> Text(
                    "Play what your PC is playing on this phone, like wireless headphones.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                is ListenStatus.Connecting -> Text(
                    "Asking ${status.target} for its audio. The Airwire receiver on the PC must be " +
                        "v0.3 or newer.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                is ListenStatus.Playing -> {
                    Text("${status.pcName} · ${status.target}", style = MaterialTheme.typography.bodyMedium)
                    Text(listenLine(stats), style = MaterialTheme.typography.bodySmall)
                    Text(
                        "${stats.packets} packets · ${stats.kbps} kbps · ${stats.concealed} concealed",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                is ListenStatus.Failed -> Text(
                    status.message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = scheme.error,
                )
            }
        }
    }
}

private fun listenLine(stats: ListenStats): String {
    val latency = stats.latencyMs?.let { "~$it ms latency · " }.orEmpty()
    return String.format(
        Locale.US, "%s%d ms buffer · %.1f%% loss · %.1f ms jitter",
        latency, stats.bufferMs, stats.lossPercent, stats.jitterMs,
    )
}

@Composable
private fun PairFirstCard(onGoToFiles: () -> Unit) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                "Pair with your PC first. Pairing once in the Files tab lets this phone both " +
                    "transfer files and play the PC's audio.",
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedButton(onClick = onGoToFiles) { Text("Pair in Files") }
        }
    }
}

@Composable
private fun StatusCard(status: StreamStatus, stats: StreamStats) {
    val scheme = MaterialTheme.colorScheme
    val (title, dot) = when (status) {
        StreamStatus.Idle -> "Disconnected" to scheme.outline
        is StreamStatus.Starting -> "Connecting…" to scheme.tertiary
        is StreamStatus.Streaming -> "Streaming Active" to scheme.primary
        is StreamStatus.Failed -> "Error" to scheme.error
    }
    val container = if (status is StreamStatus.Streaming) scheme.primaryContainer else scheme.surfaceContainerHigh
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = container)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(12.dp)
                        .clip(CircleShape)
                        .background(dot),
                )
                Spacer(Modifier.width(10.dp))
                Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            }
            when (status) {
                StreamStatus.Idle -> Text(
                    "Choose a source and a receiver, then start streaming.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                is StreamStatus.Starting -> Text(
                    "${sourceLabel(status.source)} → ${status.target}",
                    style = MaterialTheme.typography.bodyMedium,
                )
                is StreamStatus.Streaming -> {
                    Text(
                        "${sourceLabel(status.source)} → ${status.target}",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(receiverLine(stats), style = MaterialTheme.typography.bodySmall)
                    Text(
                        "${stats.packetsSent} packets sent · ${stats.kbps} kbps",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                is StreamStatus.Failed -> Text(
                    status.message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = scheme.error,
                )
            }
        }
    }
}

private fun sourceLabel(source: AudioSourceType) = when (source) {
    AudioSourceType.INTERNAL -> "Internal audio"
    AudioSourceType.MICROPHONE -> "Microphone"
}

private fun receiverLine(stats: StreamStats): String =
    if (stats.receiverResponding) {
        String.format(
            Locale.US, "Receiver: %.1f%% loss · %.1f ms jitter · %d ms buffer",
            stats.receiverLossPercent, stats.receiverJitterMs, stats.receiverBufferMs,
        )
    } else {
        "Waiting for the receiver to answer. Check that the laptop app is running and its firewall allows UDP."
    }

@Composable
internal fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
        ) {
            Column(Modifier.padding(16.dp), content = content)
        }
    }
}

@Composable
private fun SourceSelector(selected: AudioSourceType, enabled: Boolean, onChange: (AudioSourceType) -> Unit) {
    Column(Modifier.selectableGroup()) {
        SourceOption(
            title = "Internal audio",
            subtitle = "What apps on this phone play. Some apps block capture.",
            selected = selected == AudioSourceType.INTERNAL,
            enabled = enabled,
        ) { onChange(AudioSourceType.INTERNAL) }
        SourceOption(
            title = "Microphone",
            subtitle = "The phone's microphone, mono.",
            selected = selected == AudioSourceType.MICROPHONE,
            enabled = enabled,
        ) { onChange(AudioSourceType.MICROPHONE) }
    }
}

@Composable
private fun SourceOption(title: String, subtitle: String, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick)
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null, enabled = enabled)
        Spacer(Modifier.width(12.dp))
        Column {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(
                subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun ConnectionPanel(
    ui: UiState,
    enabled: Boolean,
    onHostChange: (String) -> Unit,
    onPortChange: (String) -> Unit,
    onScan: () -> Unit,
) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = ui.host,
            onValueChange = onHostChange,
            label = { Text("Laptop IP address") },
            placeholder = { Text("192.168.1.15") },
            singleLine = true,
            enabled = enabled,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
            modifier = Modifier.weight(1f),
        )
        OutlinedTextField(
            value = ui.port,
            onValueChange = onPortChange,
            label = { Text("Port") },
            singleLine = true,
            enabled = enabled,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Done),
            modifier = Modifier.width(104.dp),
        )
    }
    Spacer(Modifier.height(12.dp))
    OutlinedButton(onClick = onScan, enabled = enabled && !ui.scanning, modifier = Modifier.fillMaxWidth()) {
        if (ui.scanning) {
            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(10.dp))
            Text("Scanning…")
        } else {
            Text("Auto Scan Receiver")
        }
    }
}

@Composable
private fun BigButton(label: String, active: Boolean, onToggle: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    Button(
        onClick = onToggle,
        modifier = Modifier
            .fillMaxWidth()
            .height(64.dp),
        shape = RoundedCornerShape(20.dp),
        colors = if (active) {
            ButtonDefaults.buttonColors(containerColor = scheme.error, contentColor = scheme.onError)
        } else {
            ButtonDefaults.buttonColors()
        },
    ) {
        Text(
            label,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            letterSpacing = 1.sp,
        )
    }
}

@Composable
private fun BitrateSlider(kbps: Int, onChange: (Int) -> Unit) {
    val min = StreamViewModel.MIN_KBPS
    val max = StreamViewModel.MAX_KBPS
    val step = StreamViewModel.STEP_KBPS
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text("Opus bitrate", style = MaterialTheme.typography.bodyLarge)
        Text("$kbps kbps", style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
    }
    Slider(
        value = kbps.toFloat(),
        onValueChange = { v -> onChange(((v - min) / step).roundToInt() * step + min) },
        valueRange = min.toFloat()..max.toFloat(),
        steps = (max - min) / step - 1,
    )
    Text(
        "Higher sounds better on music; lower copes better with a busy network. Applies immediately.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun ScanResultsDialog(
    found: List<DiscoveredReceiver>,
    port: String,
    onPick: (DiscoveredReceiver) -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
        title = { Text(if (found.isEmpty()) "No receiver found" else "Receivers on this network") },
        text = {
            if (found.isEmpty()) {
                Text(
                    "Check that the laptop app is running, both devices are on the same Wi-Fi, " +
                        "and the laptop firewall allows UDP port $port.",
                )
            } else {
                Column {
                    found.forEach { receiver ->
                        ListItem(
                            headlineContent = { Text(receiver.name) },
                            supportingContent = { Text("${receiver.host}:${receiver.port}") },
                            colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                            modifier = Modifier.clickable { onPick(receiver) },
                        )
                    }
                }
            }
        },
    )
}
