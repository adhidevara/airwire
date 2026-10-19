<p align="center">
  <img src="assets/banner.svg" alt="Airwire: the audio cable, minus the cable" width="100%">
</p>

<p align="center">
  <b>Putar audio HP Android di PC lewat Wi-Fi sendiri.</b><br>
  Tanpa kabel, tanpa cloud, tanpa akun.<br>
  <a href="README.md">Read in English</a>
</p>

<p align="center">
  <a href="https://github.com/adhidevara/airwire/releases/latest"><img src="https://img.shields.io/github/v/release/adhidevara/airwire?label=Download%20APK&logo=android&color=2563EB" alt="Download APK"></a>
</p>

---

Airwire menangkap audio yang sedang diputar HP (atau dari mikrofon), mengompresnya dengan
Opus, lalu mengirimnya lewat UDP ke receiver kecil di laptop. Semua lalu lintas tetap di
jaringan lokal.

## Fitur

- **Capture audio internal** di Android 10+ (YouTube, Spotify, game). Mikrofon tersedia
  sebagai alternatif.
- **HP bisa di-mute, laptop tetap bunyi.** Audio diambil sebelum volume HP diterapkan,
  jadi volume media HP boleh 0. Sudah diuji di HyperOS / Android 13.
- **Tahan Wi-Fi yang kurang stabil.**
  - Setiap paket juga membawa salinan frame sebelumnya.
  - Frame yang hilang dipulihkan dari salinan itu dulu.
  - Kalau tidak ada salinan, dipakai FEC Opus, lalu PLC (penambalan suara).
- **Jitter buffer adaptif.**
  - Setiap frame mencatat berapa lama ia menunggu sebelum diputar.
  - Latency dipangkas begitu 10 detik terakhir menunjukkan buffer berlebih, dan ditahan
    selama Wi-Fi masih sering tersendat. Hasilnya delay rendah tanpa suara putus.
  - Perbedaan clock antara HP dan soundcard juga dikoreksi.
- **Auto Scan Receiver.** Laptop ditemukan otomatis, atau IP bisa diketik manual.
- **Tetap jalan saat layar terkunci.** App berjalan sebagai foreground service dengan
  notifikasi dan tombol Stop.
- **Ringan.** Receiver memakai sekitar 3% dari satu core CPU dan sekitar 50 MB RAM.
- **Bitrate 64–192 kbps**, bisa diubah langsung lewat slider.
- **Transfer file dua arah.**
  - HP → PC: kirim foto, video, atau dokumen, atau lewat *Share → Airwire* dari app mana pun.
  - PC → HP: kirim file dari window receiver.
  - Pairing cukup sekali dengan kode 6 digit, dan setiap file diverifikasi dengan SHA-256.
- **Audio PC bisa diputar di HP.** Pilih mode *PC → Phone* di app, maka HP memutar apa yang
  sedang diputar PC, seperti headphone wireless. Memakai pairing yang sama, dan hanya HP
  yang sudah paired yang bisa meminta audio PC. Untuk PC Windows dan Linux.

## Cara pakai singkat

### 1. Receiver (Python 3.10+)

**Windows**

```bash
cd receiver
pip install -r requirements.txt
pip install -e .
python -m airwire
```

Bisa juga double-click `receiver\Start Receiver.bat`. Window receiver menampilkan:
- IP laptop (ini yang diketik di HP),
- status koneksi,
- latency, jitter, dan packet loss,
- pilihan output device dan volume.

Volume dan device terakhir disimpan di `%APPDATA%\Airwire\settings.json`.

Opsi CLI: `--list-devices`, `--device "nama"`, `--volume 70`, `--headless`, `--port`, `--name`.

**Windows Firewall:** izinkan Python di jaringan **Private** saat diminta. Port yang
dipakai adalah 50005: UDP untuk audio dan TCP untuk file. Kalau profil
Wi-Fi Anda "Public", ubah ke Private atau buat rule dari PowerShell Administrator:

```bash
netsh advfirewall firewall add rule name="Airwire Receiver" dir=in action=allow protocol=UDP localport=50005 profile=private
netsh advfirewall firewall add rule name="Airwire Files" dir=in action=allow protocol=TCP localport=50005 profile=private
```

**macOS** (sudah dites di Ventura). Python dari Homebrew tidak membawa Tkinter dan tidak
mengizinkan `pip install` global, jadi pasang `python-tk` dan pakai virtual environment:

```bash
brew install python python-tk opus
cd receiver
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m airwire
```

Setelah itu cukup double-click `receiver/Start Receiver.command`, yang otomatis memakai
`receiver/.venv`. Saat macOS bertanya apakah Python boleh menerima koneksi masuk, pilih
**Allow**. Pengaturan disimpan di `~/Library/Application Support/Airwire/settings.json`.

**Linux** (sudah dites di Linux Mint 22.3; Ubuntu dan Debian caranya sama). `sounddevice`
butuh library PortAudio dari sistem, dan receiver butuh libopus:

```bash
sudo apt install python3-venv python3-tk libportaudio2 libopus0
cd receiver
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m airwire
```

Kalau ufw aktif, buka port-nya dengan `sudo ufw allow 50005`. Fitur memutar audio PC di HP
butuh PulseAudio atau PipeWire, yang sudah jadi bawaan Mint dan Ubuntu versi sekarang.

### 2. App Android

**Paling mudah:** unduh `Airwire-*.apk` dari [release terbaru](https://github.com/adhidevara/airwire/releases/latest),
buka file itu di HP, lalu izinkan "Install unknown apps".

**Dari source code:**

1. Buka folder `android/` di Android Studio (diuji dengan versi 2026.2.1).
   - Toolchain: AGP 9.4.1, Gradle 9.8.1, compileSdk 37.
   - NDK dan CMake dipasang otomatis oleh Gradle.
   - Saat build pertama, `opus-1.5.2.tar.gz` diunduh dari xiph.org dan SHA-256-nya diverifikasi.
2. Sambungkan HP dengan USB debugging aktif, lalu tekan **Run**.

Lewat terminal:

```bash
cd android
JAVA_HOME="C:/Program Files/Android/Android Studio/jbr" ./gradlew assembleDebug testDebugUnitTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### 3. Streaming

1. Pastikan HP dan laptop di Wi-Fi yang sama, lalu jalankan receiver.
2. Di app, pilih **Internal audio**, tap **Auto Scan Receiver**, lalu tap **START STREAMING**.
3. Setujui dialog rekam layar. Android memintanya di setiap sesi. Jika ditolak, app
   menawarkan pindah ke mikrofon.
4. Putar musik di HP; suaranya keluar di laptop. Volume HP boleh diturunkan sampai 0.

### 4. Kirim file

1. Di app, buka tab **Files** dan masukkan kode pairing 6 digit yang tampil di window
   receiver. Cukup sekali untuk setiap HP.
2. **HP → PC:** tap **Send files to PC**, atau share file dari app mana pun lalu pilih
   **Airwire**. File tersimpan di `Downloads\Airwire` di PC.
3. **PC → HP:** di window receiver klik **Send files to phone…**. HP mengunduhnya selama
   Airwire terbuka atau sedang streaming. File tersimpan di `Download/Airwire` di HP.
4. Transfer file memakai HTTP biasa (tidak terenkripsi) di jaringan lokal, dan hanya
   HP yang sudah paired yang bisa mengirim atau menerima file. Gunakan di jaringan yang
   Anda percaya. Tombol **Reset pairing** mencabut akses semua HP sekaligus.

### 5. Putar audio PC di HP

1. Pairing sekali di tab **Files** (lihat di atas).
2. Di tab **Audio** pilih **PC → Phone**, lalu tap **START LISTENING**.
3. Window receiver menampilkan HP yang sedang mendengarkan di bagian *Play this PC on your
   phone*. Tombol **Stop** menghentikannya. Hapus centang di kotak itu untuk mematikan
   fitur ini.

PC menangkap audio sistemnya lewat paket `soundcard` (WASAPI loopback di Windows,
PulseAudio atau PipeWire di Linux). macOS belum didukung untuk arah ini, karena butuh
perangkat virtual seperti BlackHole.

## Latency

Estimasi end-to-end di loopback: **sekitar 45 ms**, dengan buffer 20 ms.

Di jaringan sungguhan, buffer mengikuti kondisi Wi-Fi. Ia menyisakan cukup ruang untuk
gangguan yang terjadi dalam 10 detik terakhir, dan sisanya dilepas lagi.
- Wi-Fi 5 GHz yang sepi: mendekati angka loopback.
- Wi-Fi 2.4 GHz yang ramai: butuh buffer lebih besar.
- Paket ditandai sebagai trafik real-time (DSCP EF), yang didahulukan oleh router dengan WMM.
- Hindari Bluetooth di sisi laptop; Bluetooth sendiri menambah 100–200 ms.

## Test tanpa HP

```bash
cd receiver
python tools/fake_sender.py --discover
python tools/fake_sender.py --loss 5 --jitter 15 --seconds 30
python tools/fake_sender.py --wav lagu_48k.wav --loss 2 --burst 3
python tools/fake_sender.py --chunk 2 --stall 60 --stall-every 4   # HP & Wi-Fi tersendat
python tools/fake_listener.py --host 127.0.0.1 --code 123456       # dengarkan PC ini
python -m pytest
```

## Batasan

- **Sebagian app (umumnya dengan DRM) menolak di-capture**, sehingga hasilnya hening.
  Ini kebijakan Android.
- **Panggilan telepon/VoIP** tidak bisa ditangkap.
- **Speaker HP tidak bisa dimatikan total** oleh app biasa. Caranya: turunkan volume media
  ke 0.
- **Satu pengirim per receiver.**

## Kontributor

- [Muhammad Hasief](https://github.com/hantungoprek): dukungan receiver untuk macOS, pengujian di Linux.

## Lisensi

[MIT](LICENSE) © 2026 Ariel.
