package com.wifiaudio.streamer.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Airwire brand: electric blue on slate (see assets/logo.svg).
private val ElectricBlue = Color(0xFF2563EB)
private val SkyBlue = Color(0xFF60A5FA)
private val Cyan = Color(0xFF22D3EE)

private val LightColors = lightColorScheme(
    primary = ElectricBlue,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDBEAFE),
    onPrimaryContainer = Color(0xFF1E3A8A),
    secondary = Color(0xFF475569),
    tertiary = Color(0xFF0891B2),
    background = Color(0xFFF8FAFC),
    onBackground = Color(0xFF0F172A),
    surface = Color(0xFFF8FAFC),
    onSurface = Color(0xFF0F172A),
    onSurfaceVariant = Color(0xFF475569),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFF1F5F9),
    surfaceContainer = Color(0xFFEEF2F7),
    surfaceContainerHigh = Color(0xFFE2E8F0),
    surfaceContainerHighest = Color(0xFFCBD5E1),
    outline = Color(0xFF94A3B8),
    outlineVariant = Color(0xFFCBD5E1),
)

private val DarkColors = darkColorScheme(
    primary = SkyBlue,
    onPrimary = Color(0xFF0B1120),
    primaryContainer = Color(0xFF1E3A8A),
    onPrimaryContainer = Color(0xFFDBEAFE),
    secondary = Color(0xFF94A3B8),
    tertiary = Cyan,
    background = Color(0xFF0B1120),
    onBackground = Color(0xFFE2E8F0),
    surface = Color(0xFF0B1120),
    onSurface = Color(0xFFE2E8F0),
    onSurfaceVariant = Color(0xFF94A3B8),
    surfaceContainerLowest = Color(0xFF070B16),
    surfaceContainerLow = Color(0xFF111A2E),
    surfaceContainer = Color(0xFF15203A),
    surfaceContainerHigh = Color(0xFF1E293B),
    surfaceContainerHighest = Color(0xFF273449),
    outline = Color(0xFF475569),
    outlineVariant = Color(0xFF334155),
)

@Composable
fun AirwireTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors, content = content)
}
