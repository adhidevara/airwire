package com.wifiaudio.streamer.audio

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.projection.MediaProjection

/**
 * Builds the AudioRecord for each source. RECORD_AUDIO is checked by the activity and the
 * service before these are called.
 */
object AudioCapture {
    const val SAMPLE_RATE = 48_000
    const val FRAME_MS = 10
    const val FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS / 1000 // per channel

    /** Everything other apps play through media/game/unknown usages (Android 10+). */
    @SuppressLint("MissingPermission")
    fun internal(projection: MediaProjection): AudioRecord {
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()
        return AudioRecord.Builder()
            .setAudioFormat(format(AudioFormat.CHANNEL_IN_STEREO))
            .setBufferSizeInBytes(bufferBytes(AudioFormat.CHANNEL_IN_STEREO, channels = 2))
            .setAudioPlaybackCaptureConfig(config)
            .build()
    }

    @SuppressLint("MissingPermission")
    fun microphone(): AudioRecord =
        AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.MIC)
            .setAudioFormat(format(AudioFormat.CHANNEL_IN_MONO))
            .setBufferSizeInBytes(bufferBytes(AudioFormat.CHANNEL_IN_MONO, channels = 1))
            .build()

    private fun format(channelMask: Int): AudioFormat =
        AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(channelMask)
            .build()

    /** Room for four frames: small enough for latency, large enough to survive a hiccup. */
    private fun bufferBytes(channelMask: Int, channels: Int): Int {
        val min = AudioRecord.getMinBufferSize(SAMPLE_RATE, channelMask, AudioFormat.ENCODING_PCM_16BIT)
        return maxOf(min, FRAME_SAMPLES * channels * 2 * 4)
    }
}
