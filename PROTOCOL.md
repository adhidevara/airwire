# Airwire Wire Protocol (v1)

Audio transport: UDP. One port (default **50005**) carries every audio packet type, in both directions. File transfer uses TCP on the same port number (see the last section).
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
| 1    | source      | 0 = internal audio, 1 = microphone, 2 = PC system audio |
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

In PC → phone mode the roles swap: the phone receives, so it sends PING and the PC
answers PONG.

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

### `0x40 LISTEN` (phone → PC, every 1 s while the phone wants the PC's audio)

| Size | Field       | Notes |
|-----:|-------------|-------|
| 2    | session     | random per listening session |
| 2    | bitrateKbps | bitrate the phone asks for; the PC clamps it to 32–256 |
| 8    | counter     | must grow with every LISTEN from this phone (the app uses Unix time in ms) |
| 16   | mac         | first 16 bytes of HMAC-SHA256(key, header + session + bitrateKbps + counter) |
| rest | name        | phone name |

`key` is SHA-256 of the file-transfer pairing token (UTF-8). The PC keeps exactly that hash
for every paired phone, so it can check the MAC without storing the token. A LISTEN whose
MAC matches no paired phone, or whose counter is not larger than the last one seen from
that phone, is refused. Captured packets therefore can't be replayed to redirect the
stream.

### `0x41 REFUSE` (PC → phone, reply to a LISTEN that won't be served)

| Size | Field   | Notes |
|-----:|---------|-------|
| 2    | session | from the LISTEN |
| 1    | reason  | 1 = not paired, 2 = turned off on the PC, 3 = the PC can't capture its audio, 4 = stopped from the PC |

## PC → phone

The phone plays what the PC is playing. The PC is the sender and the phone the receiver;
every other rule above applies unchanged.

1. The phone sends LISTEN to the PC's port (50005) every second.
2. The PC captures its system audio once (WASAPI loopback on Windows, the sink monitor on
   Linux) and sends HELLO (source 2) and AUDIO to every listening phone, from port 50005.
   Each phone gets its own session and Opus encoder at the bitrate it asked for.
3. The phone sends PING every 500 ms with its loss, jitter and buffer; the PC answers PONG.
4. The phone sends BYE with its LISTEN session when it stops. Without a LISTEN for 3 s the
   PC stops streaming to it. *Stop* on the PC answers the next LISTEN with REFUSE 4.

## Receiver session rules

- The first sender to send AUDIO or HELLO is locked by its `ip:port`. Other senders are ignored
  until the locked one sends BYE or stays silent for 2 s.
- A different `session` from the locked address resets the decoder and the jitter buffer.

## Loss recovery (receiver, per missing frame at play-out time)

1. The redundant copy carried by packet `seq+1`.
2. Opus in-band FEC (`decode_fec=1` on packet `seq+1`). This only helps when the encoder used SILK/hybrid.
3. Opus packet-loss concealment (`opus_decode(NULL)`).

## File transfer (HTTP over TCP)

The receiver also listens on **TCP** at the same port number (default 50005) and serves a
small HTTP/1.0 API under `/api/v1`. Bodies are JSON unless noted.

| Method & path | Auth | Body / query | Response |
|---|---|---|---|
| `POST /api/v1/pair` | – | `{"code": "123456", "device": "Pixel 8"}` | `200 {"token", "name"}`, `403` wrong code, `429` after 5 wrong codes in 60 s |
| `GET /api/v1/hello` | ✓ | – | `200 {"name", "device"}` |
| `PUT /api/v1/files/<url-encoded name>` | ✓ | raw bytes, `Content-Length` required | `201 {"name", "size", "sha256"}` (`name` = name saved on the PC) |
| `GET /api/v1/outbox?since=<v>&wait=<s>` | ✓ | long-poll, `wait` ≤ 30 | `200 {"version", "files": [{"id", "name", "size", "sha256"}]}` |
| `GET /api/v1/outbox/<id>` | ✓ | – | raw bytes; headers `X-Airwire-Name` (url-encoded), `X-Airwire-Sha256` |
| `DELETE /api/v1/outbox/<id>` | ✓ | – | `204`; the phone sends this after saving, which removes the file from the outbox |

- **Auth:** `Authorization: Bearer <token>`. A missing or revoked token returns `401`; the
  phone then forgets the pairing.
- **Pairing:** the 6-digit code is shown in the receiver window. The PC stores only SHA-256
  hashes of tokens. *Reset pairing* issues a new code and revokes every token.
- **Phone → PC:** the receiver sanitises the file name (no paths, no reserved Windows
  names), never overwrites (`name (1).ext`), streams to a temporary `.part` file, and
  deletes it if the upload is cut short. The phone compares the returned `sha256` with its
  own.
- **PC → phone:** files queued on the PC appear in the outbox. The phone long-polls
  `outbox` (returning as soon as `version` changes), downloads each file, verifies
  `X-Airwire-Sha256`, saves it to `Download/Airwire`, then sends `DELETE`. A file whose
  download failed stays in the outbox and is retried on the next poll.
- Traffic is not encrypted; it is meant for a trusted local network.
