# 🎬 LambadaClips.app

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Open Source](https://badges.frapsoft.com/os/v1/open-source.svg?v=103)](https://opensource.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![GitHub stars](https://img.shields.io/github/stars/tarisrizki/lambadaclips?style=social)](https://github.com/tarisrizki/lambadaclips)

Selamat datang di **LambadaClips** — platform video AI *all-in-one* yang bikin proses ngonten kamu jadi super cepat dan otomatis! Nggak perlu lagi sewa editor mahal atau bayar langganan bulanan. LambadaClips bisa kamu jalankan sendiri (*self-hosted*), **100% tanpa watermark dan tanpa batasan**.

---

## 🚀 3 Senjata Utama LambadaClips

### 1. ✂️ Pemotong Video Otomatis (Clip Generator)
Ubah video panjang kamu (seperti *podcast*, *webinar*, *live stream*, atau wawancara) jadi puluhan video pendek (*Shorts/Reels/TikTok*) berformat vertikal 9:16 yang siap viral!
* **AI Pencari Momen:** Menggunakan Google Gemini 3.0 Flash untuk mencari bagian video paling seru secara otomatis.
* **Smart Cropping:** AI akan mengikuti wajah pembicara (Tracking) agar selalu berada di tengah frame.
* **Subtitle Otomatis bergaya TikTok:** Langsung ada teksnya, per-kata, siap pakai!

### 2. 🤖 Pembuat Video UGC AI (AI Shorts)
Mau bikin video promosi produk tapi nggak punya kamera, studio, atau *budget* sewa *influencer*? Tenang aja!
* Tinggal ketik deskripsi produk atau masukin *link* website.
* AI akan membuat **aktor virtual** lengkap dengan *voiceover* (suara) yang *lip-sync*, ditambah *b-roll* (video ilustrasi) yang keren.
* Cocok banget buat bisnis lokal, jualan di TikTok Shop, atau bikin demo produk *SaaS*.

### 3. 📺 Asisten YouTube Studio
Bikin channel YouTube kamu makin profesional dengan bantuan AI gratis:
* **Thumbnail Generator:** Bikin *thumbnail* clickbait yang keren dengan wajah kamu.
* **Ide Judul Viral:** AI akan memberikan 10 rekomendasi judul terbaik yang memancing *views*.
* **Deskripsi & Timestamp:** Otomatis bikin deskripsi video lengkap dengan *chapter/timestamp* dari hasil transkrip video.

---

## 💡 Kenapa Harus Pakai LambadaClips?

Jika dibandingkan dengan aplikasi berbayar lain (seperti Opus Clip atau Kapwing yang memungut biaya $15 - $200 per bulan), LambadaClips punya keunggulan telak:
- **GRATIS:** Kamu hanya perlu bayar *cost* API sesuai pemakaian (bahkan API Gemini dan Upload-Post punya tier gratis yang sangat besar!).
- **Privasi Terjamin:** Berjalan di server atau komputermu sendiri lewat Docker. Data kamu aman, nggak diunggah ke *cloud* pihak ketiga.
- **Tanpa Batasan Kuota:** Mau proses 100 video sehari? Bebas!
- **Auto-Publishing:** Langsung *upload* otomatis ke TikTok, Instagram Reels, dan YouTube Shorts sekaligus lewat integrasi [Upload-Post](https://upload-post.com).

---

## 🛠️ Cara Install & Pakai

Syarat utama: Komputer/Server kamu harus sudah ter-install **Docker** dan **Docker Compose**, atau gunakan **GitHub Codespaces**.

**Langkah 1: Download Source Code**
```bash
git clone https://github.com/tarisrizki/lambadaclips.git
cd lambadaclips
```

**Langkah 2: Konfigurasi (Opsional)**
Kamu bisa menyalin file contoh konfigurasi jika butuh integrasi AWS S3.
```bash
cp .env.example .env
```

**Langkah 3: Jalankan Mesin Backend (Docker)**
Buka terminal dan ketik perintah berikut untuk menyalakan API & Mesin Render:
```bash
mkdir -p output/thumbnails
sudo chmod -R 777 output
docker compose up -d
```

**Langkah 4: Jalankan Dashboard Frontend**
Buka terminal baru, lalu jalankan server dashboard:
```bash
cd dashboard
npm install
npm run dev
```

**Langkah 5: Buka di Browser**
Buka **`http://localhost:5173`** di browser kesayanganmu.
1. Masuk ke menu **Settings** dan masukkan API Key kamu (Gemini, fal.ai, ElevenLabs, dll).
2. Langsung mulai *upload* video panjangmu atau bikin video UGC AI dalam hitungan menit!

---

## ⚙️ Teknologi di Balik Layar (Tech Stack)

Aplikasi ini dibangun menggunakan teknologi modern yang *powerful*:
- **Backend:** Python 3.11, FastAPI, FFmpeg, OpenCV, YOLOv8, MediaPipe, Faster-Whisper.
- **Frontend:** React 18, Vite, Tailwind CSS.
- **AI Engine:** Google Gemini, fal.ai, ElevenLabs.
- **Infrastruktur:** Docker & AWS S3.

---

## 🤝 Kontribusi

Proyek ini bersifat *Open Source* (Lisensi MIT). Kami sangat terbuka untuk menerima kontribusi! Kalau kamu punya ide fitur baru, perbaikan *bug*, atau ingin menambahkan integrasi AI model terbaru, silakan buat *Pull Request* (PR).

*Mari jadikan LambadaClips sebagai standar baru pembuatan konten AI di seluruh dunia!* 🌍
