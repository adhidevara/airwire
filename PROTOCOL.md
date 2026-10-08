# Wi-Fi Audio Wire Protocol (v1)

Transport: UDP. One port (default **50005**) carries every packet type, in both directions.
All integers are **big-endian**. Strings are UTF-8 and fill the rest of the datagram (no length prefix, max 64 bytes).

## Common header (4 bytes)

| Offset | Size | Field   | Value                 |
|-------:|-----:|---------|-----------------------|
| 0      | 2    | magic   | ASCII `"WA"` (0x57 0x41) |
| 2      | 1    | version | `1`                   |
| 3      | 1    | type    | see below             |

Receivers drop datagrams with a wrong magic or version, or a truncated body.

## Packet types

`A` = Android sender, `L` = laptop receiver.

### `0x01 AUDIO` (A → L, every frame)

| Size | Field      | Notes |
|-----:|------------|-------|
| 2    | session    | random per streaming session; a new value resets the receiver |
| 4    | seq        | frame counter, starts at 0, +1 per Opus frame |
| 1    | flags      | bit0 = `REDUNDANT`, the datagram carries frame `seq-1` after the primary |
| 2    | primaryLen | length of the primary Opus packet |
| n    | primary    | Opus packet for frame `seq` |
| rest | redundant  | Opus packet for frame `seq-1` (only when `REDUNDANT` is set) |

Audio is always 48 kHz. Stereo sources encode 2 channels and microphone may encode 1; the
receiver decodes everything to stereo. Default frame duration is 10 ms (480 samples per channel).

### `0x02 HELLO` (A → L, at start and every 1 s)

| Size | Field       | Notes |
|-----:|-------------|-------|
| 2    | session     | |
| 4    | sampleRate  | 48000 |
| 1    | channels    | 1 or 2 (encoder channels) |
| 1    | frameMs     | 10 or 20 |
| 2    | bitrateKbps | current encoder bitrate |
| 1    | source      | 0 = internal audio, 1 = microphone |
| 2    | captureMs   | sender-side latency estimate: capture buffer + Opus lookahead (excludes the frame itself) |
| rest | name        | device name, e.g. `"Pixel 8"` |

### `0x10 DISCOVER` (A → broadcast)

| Size | Field | Notes |
|-----:|-------|-------|
| 4    | nonce | echoed in OFFER so the sender can match replies |

The sender broadcasts to the Wi-Fi subnet broadcast address and to `255.255.255.255`, port 50005.

### `0x11 OFFER` (L → A, unicast reply to DISCOVER)

| Size | Field     | Notes |
|-----:|-----------|-------|
| 4    | nonce     | copied from DISCOVER |
| 2    | audioPort | port the receiver listens on |
| rest | name      | receiver host name |

### `0x20 PING` (L → A, every 500 ms while a sender is locked)

| Size | Field        | Notes |
|-----:|--------------|-------|
| 8    | tNs          | receiver monotonic clock, nanoseconds (opaque to sender) |
| 2    | lossPermille | packet loss over the last interval, 0–1000 |
| 4    | jitterUs     | RFC 3550 inter-arrival jitter, microseconds |
| 2    | bufferMs     | current jitter-buffer depth |

The sender should show the stats it receives in PING.

### `0x21 PONG` (A → L, immediate reply to PING)

| Size | Field | Notes |
|-----:|-------|-------|
| 8    | tNs   | copied from PING; receiver computes RTT |

### `0x30 BYE` (A → L, when streaming stops)

| Size | Field   |
|-----:|---------|
| 2    | session |

## Receiver session rules

- The first sender to send AUDIO or HELLO is locked by its `ip:port`. Other senders are ignored
  until the locked one sends BYE or stays silent for 2 s.
- A different `session` from the locked address resets the decoder and the jitter buffer.

## Loss recovery (receiver, per missing frame at play-out time)

1. The redundant copy carried by packet `seq+1`.
2. Opus in-band FEC (`decode_fec=1` on packet `seq+1`). This only helps when the encoder used SILK/hybrid.
3. Opus packet-loss concealment (`opus_decode(NULL)`).
