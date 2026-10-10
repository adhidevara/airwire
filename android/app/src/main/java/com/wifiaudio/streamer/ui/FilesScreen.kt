package com.wifiaudio.streamer.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.wifiaudio.streamer.files.Direction
import com.wifiaudio.streamer.files.FileTransfer
import com.wifiaudio.streamer.files.PairState
import com.wifiaudio.streamer.files.TransferState
import java.util.Locale

/** The Files tab: pair with the PC, send files to it, and see what moved either way. */
@Composable
fun FilesScreen(
    pairState: PairState,
    transfers: List<FileTransfer>,
    onPair: (String) -> Unit,
    onUnpair: () -> Unit,
    onPickFiles: () -> Unit,
    onOpen: (FileTransfer) -> Unit,
    onClearFinished: () -> Unit,
    onGoToAudio: () -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            Section("PC") { PairingPanel(pairState, onPair, onUnpair, onGoToAudio) }
        }
        item {
            Column {
                Button(
                    onClick = onPickFiles,
                    enabled = pairState is PairState.Paired,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(56.dp),
                    shape = RoundedCornerShape(16.dp),
                ) {
                    Text("Send files to PC", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                }
                Text(
                    "Tip: share from any app (Gallery, Files…) and pick Airwire. " +
                        "Files sent from the PC arrive while Airwire is open or streaming.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        }
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Transfers",
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.weight(1f),
                )
                if (transfers.any { !it.active }) {
                    TextButton(onClick = onClearFinished) { Text("Clear finished") }
                }
            }
        }
        if (transfers.isEmpty()) {
            item {
                Text(
                    "Nothing yet. Received files are saved to Download/Airwire.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        items(transfers, key = { it.id }) { TransferRow(it, onOpen) }
    }
}

@Composable
private fun PairingPanel(
    state: PairState,
    onPair: (String) -> Unit,
    onUnpair: () -> Unit,
    onGoToAudio: () -> Unit,
) {
    when (state) {
        PairState.NoHost -> {
            Text("Set your PC's address first.", style = MaterialTheme.typography.bodyLarge)
            Text(
                "On the Audio tab, tap Auto Scan Receiver or type the IP shown in the Airwire window.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton(onClick = onGoToAudio) { Text("Go to Audio tab") }
        }
        is PairState.Paired -> {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Paired with ${state.pcName}", style = MaterialTheme.typography.bodyLarge)
                    Text(
                        state.host,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = onUnpair) { Text("Unpair") }
            }
        }
        is PairState.Unpaired, is PairState.Pairing -> {
            val busy = state is PairState.Pairing
            val host = if (state is PairState.Unpaired) state.host else (state as PairState.Pairing).host
            var code by rememberSaveable { mutableStateOf("") }
            Text("Pair with $host", style = MaterialTheme.typography.bodyLarge)
            Text(
                "Enter the 6-digit code shown in the Airwire window on your PC. You only do this once.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = code,
                    onValueChange = { code = it.filter(Char::isDigit).take(6) },
                    label = { Text("Pairing code") },
                    singleLine = true,
                    enabled = !busy,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { if (code.length == 6) onPair(code) }),
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(12.dp))
                Button(onClick = { onPair(code) }, enabled = !busy && code.length == 6) {
                    if (busy) {
                        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    } else {
                        Text("Pair")
                    }
                }
            }
            (state as? PairState.Unpaired)?.error?.let {
                Text(
                    it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        }
    }
}

@Composable
private fun TransferRow(t: FileTransfer, onOpen: (FileTransfer) -> Unit) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (t.direction == Direction.TO_PC) "↑" else "↓",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(t.name, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(
                        statusLine(t),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (t.state == TransferState.FAILED) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (t.direction == Direction.FROM_PC && t.state == TransferState.DONE && t.uri != null) {
                    TextButton(onClick = { onOpen(t) }) { Text("Open") }
                }
            }
            if (t.state == TransferState.RUNNING) {
                Spacer(Modifier.height(8.dp))
                LinearProgressIndicator(progress = { t.progress }, modifier = Modifier.fillMaxWidth())
            }
        }
    }
}

private fun statusLine(t: FileTransfer): String {
    val toPc = t.direction == Direction.TO_PC
    return when (t.state) {
        TransferState.QUEUED -> "${formatSize(t.size)} · waiting"
        TransferState.RUNNING -> "${formatSize(t.bytes)} of ${formatSize(t.size)} · ${(t.progress * 100).toInt()}%"
        TransferState.DONE -> "${formatSize(t.size)} · " + if (toPc) "sent to PC" else "saved to Download/Airwire"
        TransferState.FAILED -> "Failed: ${t.error ?: "unknown error"}"
    }
}

private fun formatSize(bytes: Long): String {
    if (bytes < 0) return "?"
    if (bytes < 1024) return "$bytes B"
    var value = bytes / 1024.0
    for (unit in listOf("KB", "MB", "GB")) {
        if (value < 1024 || unit == "GB") return String.format(Locale.US, "%.1f %s", value, unit)
        value /= 1024
    }
    return "$bytes B"
}
