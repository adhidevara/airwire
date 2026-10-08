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
- **Jitter buffer adaptif.** Buffer membesar saat terjadi underrun, mengecil lagi saat
  jaringan stabil, dan mengoreksi perbedaan clock antara HP dan soundcard.
- **Auto Scan Receiver.** Laptop ditemukan otomatis, atau IP bisa diketik manual.
- **Tetap jalan saat layar terkunci.** App berjalan sebagai foreground service dengan
  notifikasi dan tombol Stop.
- **Ringan.** Receiver memakai sekitar 3% dari satu core CPU dan sekitar 50 MB RAM.
- **Bitrate 64–192 kbps**, bisa diubah langsung lewat slider.

## Cara pakai singkat

### 1. Receiver (Windows, Python 3.10+)

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

**Windows Firewall:** izinkan Python di jaringan **Private** saat diminta. Kalau profil
Wi-Fi Anda "Public", ubah ke Private atau buat rule dari PowerShell Administrator:

```bash
netsh advfirewall firewall add rule name="Airwire Receiver" dir=in action=allow protocol=UDP localport=50005 profile=private
```

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

## Latency

Hasil uji di loopback: **42 ms**.

Di jaringan sungguhan, buffer menyesuaikan kondisi Wi-Fi:
- Wi-Fi 5 GHz yang sepi: mendekati angka loopback.
- Wi-Fi 2.4 GHz yang ramai: buffer bisa melebihi 100 ms.
- Hindari Bluetooth di sisi laptop; Bluetooth sendiri menambah 100–200 ms.

## Test tanpa HP

```bash
cd receiver
python tools/fake_sender.py --discover
python tools/fake_sender.py --loss 5 --jitter 15 --seconds 30
python tools/fake_sender.py --wav lagu_48k.wav --loss 2 --burst 3
python -m pytest
```

## Batasan

- **Sebagian app (umumnya dengan DRM) menolak di-capture**, sehingga hasilnya hening.
  Ini kebijakan Android.
- **Panggilan telepon/VoIP** tidak bisa ditangkap.
- **Speaker HP tidak bisa dimatikan total** oleh app biasa. Caranya: turunkan volume media
  ke 0.
- **Satu pengirim per receiver.**

## Lisensi

[MIT](LICENSE) © 2026 Ariel.
