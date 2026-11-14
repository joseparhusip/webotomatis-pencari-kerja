# UI Guidelines — Anti AI Slop

> Panduan visual & interaksi untuk Webotomatis Pencari Kerja (Jinja2 + HTMX + CSS dengan design tokens).
> Tujuan: UI yang terasa seperti **alat kerja yang dibuat dengan sengaja**, bukan template landing page hasil generate AI.

---

## 0. Prinsip

1. **Ini alat, bukan brosur.** Pengguna membuka aplikasi untuk menyaring ratusan lowongan dan mengirim lamaran. Kepadatan informasi > dekorasi.
2. **Data asli yang jadi visualnya.** Nama perusahaan, posisi, gaji, skor. Bukan ilustrasi, ikon besar, atau gradient.
3. **Satu hal penting per layar.** Setiap halaman punya satu aksi utama (mis. "Siapkan lamaran"), yang lain sekunder.
4. **Jujur.** Tidak ada angka palsu, tidak ada status "berhasil" sebelum benar-benar berhasil, tidak ada AI yang pura-pura yakin.
5. **Cepat dipakai dengan keyboard.** Pengguna yang sama memakainya setiap hari.

---

## 1. Daftar Larangan (AI Slop)

Jika salah satu muncul di PR, tolak.

### Visual
| ❌ Jangan | ✅ Gantinya |
|---|---|
| Gradient ungu/biru/pink (`from-indigo-500 to-purple-600`) | Warna solid dari token (§2) |
| Teks gradient (`bg-clip-text`) | Teks warna `--ink` |
| Glassmorphism, `backdrop-blur`, blob/orb animasi | Permukaan solid + border 1px |
| `rounded-2xl` + `shadow-xl` di semua kartu | Radius maks 6px, border 1px, tanpa shadow (kecuali dropdown/modal) |
| Hero section, "Welcome back 👋", banner besar di dashboard | Langsung tampilkan daftar lowongan |
| Grid 3 kolom "fitur" dengan ikon bulat di atasnya | Tabel atau daftar |
| Ikon di setiap tombol & setiap judul | Ikon hanya bila mempercepat pengenalan (status, eksternal link) |
| Emoji di UI (🚀 ✨ 🎉 💼) | Tidak ada emoji sama sekali |
| Badge "✨ AI-Powered", ikon bintang/sparkle untuk fitur AI | Tulis apa yang dikerjakan: "Skor 82 · cocok di Go, PostgreSQL" |
| Kartu statistik besar berwarna-warni dengan ikon | Satu strip angka netral bergaris (§5) |
| Pill berwarna-warni untuk setiap tag | Teks mono kecil abu, warna hanya untuk status |
| Avatar/logo placeholder generik, ilustrasi | Logo asli perusahaan, atau inisial di kotak abu |
| Animasi masuk (fade/slide) pada setiap elemen | Tanpa animasi; transisi ≤150ms hanya untuk drawer/dropdown |
| Skeleton loader di mana-mana | Teks "Memuat…" inline di tempat data akan muncul |
| Font Inter default untuk semuanya | IBM Plex Sans + IBM Plex Mono (§3) |
| Teks abu-abu muda di atas abu-abu (kontras rendah) | Minimal kontras WCAG AA (4.5:1) |
| Layout serba di-tengah (`mx-auto text-center`) | Rata kiri, lebar penuh untuk tabel |

### Teks / copywriting
| ❌ Jangan | ✅ Gantinya |
|---|---|
| "Unlock your dream career!", "Supercharge your job hunt" | "Lowongan" |
| "Oops! Something went wrong 😢" | "Gagal mengambil data dari Glints (timeout 30 dtk). Coba lagi." |
| "Successfully submitted! 🎉" | "Terkirim ke hr@pt-abc.co.id · 14:32" |
| Tanda seru di mana-mana | Kalimat datar dan informatif |
| Title Case Di Semua Tombol | Huruf kapital hanya di awal: "Kirim lamaran" |
| "Click here", "Submit" | Kata kerja + objek: "Kirim 3 lamaran" |
| Campur Inggris–Indonesia tanpa alasan | Bahasa Indonesia; istilah teknis tetap (CV, remote, hybrid) |

---

## 2. Warna (Design Tokens)

Palet "kertas & tinta": netral hangat + **satu** warna aksen hijau tua. Warna status hanya dipakai untuk status.

```css
:root {
  /* permukaan */
  --bg:        #F6F5F1;  /* latar halaman, kertas hangat */
  --surface:   #FFFFFF;  /* tabel, panel */
  --surface-2: #EFEDE7;  /* hover baris, header tabel */
  --line:      #DEDBD2;  /* border 1px */

  /* tinta */
  --ink:       #1B1B18;  /* teks utama */
  --ink-2:     #4F4D47;  /* teks sekunder */
  --ink-3:     #7A776E;  /* meta, placeholder (masih AA di --surface) */

  /* aksen: SATU saja */
  --accent:    #1E5B47;  /* tombol utama, link, fokus, skor tinggi */
  --accent-ink:#FFFFFF;

  /* status: hanya untuk status lamaran / hasil aksi */
  --ok:        #2F6B3A;
  --warn:      #8A5A00;
  --danger:    #A3322A;
  --info:      #2D5A88;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#141412; --surface:#1C1C19; --surface-2:#252521; --line:#34332E;
    --ink:#ECEAE3; --ink-2:#B8B5AC; --ink-3:#8D8A81;
    --accent:#6FBF9C; --accent-ink:#0F1F19;
    --ok:#7CC48A; --warn:#E0B055; --danger:#E5807A; --info:#84B2E0;
  }
}
:root[data-theme="dark"] { /* nilai sama dengan blok dark di atas */ }
```

Implementasi: token di atas ada di `backend/app/static/css/app.css`. Semua warna di CSS **wajib** memakai `var(--token)`; tidak boleh ada nilai hex/rgb baru di luar blok token.

### Pemakaian warna
- **Aksen:** tombol utama (maks 1 per layar), link, focus ring, skor ≥ 75.
- **Status lamaran:** titik 8px + teks, bukan pill penuh warna.

| Status | Tampilan |
|---|---|
| draft | `○ Draft` (ink-3) |
| queued | `◔ Antre` (info) |
| sent | `● Terkirim` (ink) |
| replied | `● Dibalas` (info) |
| interview | `● Interview` (ok) |
| offer | `● Offer` (ok, tebal) |
| rejected | `● Ditolak` (ink-3, teks dicoret tidak perlu) |
| failed | `▲ Gagal` (danger) |

---

## 3. Tipografi

| Peran | Font | Ukuran / line-height | Berat |
|---|---|---|---|
| Judul halaman | IBM Plex Sans | 20 / 28 | 600 |
| Judul seksi | IBM Plex Sans | 15 / 22 | 600 |
| Body / sel tabel | IBM Plex Sans | 14 / 20 | 400 |
| Meta (sumber, tanggal, tag) | IBM Plex Mono | 12 / 16 | 400 |
| Angka (gaji, skor, jumlah) | IBM Plex Mono, `font-variant-numeric: tabular-nums` | 13 / 18 | 500 |
| Isi surat lamaran (preview) | IBM Plex Serif | 15 / 24 | 400 |

- Font di-host sendiri di `static/fonts/` (tanpa request ke luar).
- Tidak ada teks < 12px. Tidak ada `uppercase tracking-widest` untuk label.
- Panjang baris paragraf maks ~72 karakter (`max-w-prose`).

---

## 4. Spasi, Radius, Garis

- Skala spasi kelipatan 4px: `4, 8, 12, 16, 24, 32`. Tidak ada `48+` di dalam aplikasi.
- Radius: input/tombol **4px**, panel/drawer **6px**, badge **2px**. Tidak ada `rounded-full` kecuali titik status.
- Pemisah = border 1px `--line`. Shadow hanya untuk elemen yang mengambang (dropdown, modal): `0 4px 16px rgb(0 0 0 / .08)`.
- Tinggi baris tabel: **40px** (mode padat 32px via toggle).

---

## 5. Layout

**Semua halaman lebar penuh (kiri–kanan).** Tidak ada kolom konten sempit dengan sisi kanan kosong. Rata kiri, padding halaman 24px (16px di HP).

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [W] Webotomatis  Lowongan 92  Lamaran 1  Profil  Pengaturan   ● 5 mnt  [JE]  │ ← topbar 56px
├──────────────────────────────────────────────────────────────────────────────┤
│ Judul halaman                                              [Aksi utama]      │ ← page-head
│ Satu kalimat penjelasan                                                      │
│ ┌────────────┬────────────┬────────────┬────────────┐                        │
│ │ angka      │ angka      │ angka      │ angka      │  ← strip angka (1 kotak)│
│ └────────────┴────────────┴────────────┴────────────┘                        │
│ konten: tabel / papan / grid kartu / dua kolom kotak                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Halaman | Susunan |
|---|---|
| Lowongan | filter 264px · tabel · drawer detail 440px (kolom Sumber & Diposting disembunyikan saat drawer terbuka) |
| Lamaran | strip angka · papan status (satu lajur per status, lajur Antre/Gagal hanya muncul bila ada isi) |
| Review | dua kolom 2:3 — lowongan (sticky) · editor surat |
| Profil | dua kolom kotak — Data diri + Kalimat surat · CV + Bukti CV |
| Pengaturan | strip status koneksi · grid sumber 3 kolom · riwayat |

**Topbar:** monogram W + nama, menu dengan angka (lowongan baru cocok profil, draft lamaran), chip status scraping (titik hijau/kuning/biru), inisial profil.

**Strip angka:** satu kotak bergaris dengan sel dipisah garis tipis, angka mono 24px. Bukan kartu warna-warni terpisah, tanpa ikon.

**Logo perusahaan:** logo asli dari sumber (36px, latar putih supaya terbaca di mode gelap); bila tidak ada/gagal dimuat → inisial perusahaan (tanpa "PT"/"CV").

- **< 1024px:** filter jadi panel lipat (tertutup default), dua kolom jadi satu, strip angka 2×2.
- **< 640px (HP):** baris lowongan jadi kartu: logo · judul · skor di kanan atas, lalu lokasi & gaji sebaris. Tidak ada scroll horizontal halaman.

---

## 6. Halaman

### 6.1 Lowongan (`/jobs`) — layar utama

```
FILTER                    │ ☐  Skor  Posisi / Perusahaan                Lokasi       Gaji        Sumber    Diposting
──────────────────────────│─────────────────────────────────────────────────────────────────────────────────────────
Kategori                  │ ☐   91   Backend Developer (Golang)          Jakarta Sel. Rp 8–12 jt  glints    2 hr
 ☑ Full Stack   ☑ Frontend│          PT Contoh Fintech · email HR ✓       Hybrid
 ☑ Backend      ☑ Mobile  │ ☐   84   Flutter Developer                   Remote       —           jobstreet 5 hr
                          │          Startup Logistik ABC · portal
Lokasi                    │ ☐   61   Full Stack Engineer                 Bekasi       Rp 6–9 jt   kitalulus 1 hr
 [Jabodetabek, Remote  ▾] │          CV Maju Jaya · email HR ✓
Mode kerja                │
 ☑ Remote ☑ Hybrid ☐ WFO  │ ───────────────────────────────────────────────────────────────────────────────────────
Gaji minimum              │ 3 dipilih   [Sembunyikan]  [Siapkan 3 lamaran]   ← action bar muncul saat ada pilihan
 [Rp 5.000.000      ]     │
Level  ☑ Entry ☑ Junior   │
Skor minimum  [60]        │
Diposting  [7 hari ▾]     │
Status ☑ Baru ☐ Dilamar   │
                          │
[Simpan filter]           │
```

- Skor: angka mono; **≥75 warna aksen**, 50–74 `--ink`, <50 `--ink-3`. Tanpa progress bar/donut.
- Baris kedua sel posisi: nama perusahaan + mode apply (`email HR ✓` / `portal`).
- Gaji tidak diketahui ditulis `—`, bukan "Negotiable" atau "Kompetitif".
- Lowongan yang sudah dilamar: baris ber-opacity 60% + teks `Dilamar 3 Nov`.
- Lowongan curiga penipuan: `▲ cek dulu` warna `--warn` di samping nama perusahaan.

### 6.2 Detail lowongan (drawer kanan)

```
Backend Developer (Golang)                              [Buka di glints ↗]
PT Contoh Fintech · Jakarta Selatan · Hybrid · Rp 8–12 jt
Diposting 11 Nov 2026 · Sumber: glints, jobstreet

Skor 91
Cocok: Go (Gin), PostgreSQL, Docker, REST API, JWT
Kurang: Kubernetes
Kategori: backend · Level: junior (1–3 thn)

Kirim ke: recruitment@contohfintech.co.id
Format subject dari iklan: "BE_Nama_Posisi"

── Deskripsi ─────────────────────────────
(teks asli lowongan, tidak diringkas AI)

[Sembunyikan]                        [Siapkan lamaran]
```

- "Cocok/Kurang" adalah hasil AI; tampilkan sebagai daftar biasa. Teks asli lowongan selalu tersedia & tidak diubah.

### 6.3 Review lamaran (`/applications/review`) — layar paling penting

Dua kolom: kiri = konteks lowongan, kanan = surat yang bisa diedit. Satu lamaran per layar; navigasi `1 / 3` untuk batch.

```
Lamaran 1 / 3                                                   [‹ Sebelumnya] [Berikutnya ›]
───────────────────────────────────────────┬─────────────────────────────────────────────────
Backend Developer (Golang)                 │ Kepada   recruitment@contohfintech.co.id
PT Contoh Fintech                           │ Subject  [BE_Jose Elio Parhusip_Backend Developer ]
                                           │ CV       [CV Master (PDF, 212 KB)            ▾]
Persyaratan                                │ Bahasa   (•) Indonesia  ( ) English
• Go 1+ tahun                              │ ┌─────────────────────────────────────────────┐
• PostgreSQL, Docker                       │ │ Yth. Tim Rekrutmen PT Contoh Fintech,        │
• Paham REST & JWT                         │ │                                             │
                                           │ │ Saya Jose Elio Parhusip, ...                │  ← serif, editable
Bukti dari CV yang dipakai                 │ │                                             │
• MedCredix: 44 endpoint Go/Gin            │ └─────────────────────────────────────────────┘
• JWT + RBAC                               │ Pemeriksaan
• Deploy Docker Compose + Caddy            │ ✓ Nama perusahaan muncul (2×)
                                           │ ✓ Posisi muncul
                                           │ ✓ Tidak ada placeholder [ ... ]
                                           │ ✓ 247 kata (target 180–300)
                                           │ ▲ Frasa klise: "sangat antusias" → [Ganti]
                                           │
                                           │ [Buat ulang]   [Lewati]     [Kirim lamaran]
```

- Tombol **Kirim lamaran** non-aktif selama ada pemeriksaan ✗ (merah). Peringatan ▲ boleh tetap dikirim.
- Setelah klik kirim: tombol berubah jadi "Mengirim…" (disabled). Hasil ditulis di tempat: `Terkirim · 14:32` atau `Gagal: kuota harian habis (25/25)`.
- Mode portal: tombol utama jadi **"Salin surat & buka lowongan ↗"**, lalu muncul pertanyaan "Sudah apply di portal?" [Ya, tandai dilamar] [Belum].

### 6.4 Lamaran (`/applications`)

Default: **tabel** dikelompokkan per status (lebih padat daripada kanban). Kanban opsional via toggle.

```
Interview (2)
  PT ABC        Frontend Developer       Terkirim 2 Nov   Dibalas 6 Nov   [ubah status ▾]
Dibalas (4)
  ...
Terkirim (31)
  ...
Minggu ini: 18 terkirim · 4 dibalas (22%) · 1 interview
```

### 6.5 Profil & CV, Pengaturan
Form biasa, satu kolom, label di atas input, lebar maks 640px. Simpan per seksi, bukan satu tombol raksasa di bawah.

---

## 7. Komponen

| Komponen | Spesifikasi |
|---|---|
| **Tombol utama** | `bg-accent text-accent-ink`, tinggi 32px, padding x 12px, radius 4px. Maks 1 per layar. |
| **Tombol sekunder** | `border border-line bg-surface text-ink`. |
| **Tombol teks** | Hanya teks `text-accent`, untuk aksi minor ("Ganti", "Buat ulang"). |
| **Tombol berbahaya** | Sekunder dengan teks `--danger`; selalu ada konfirmasi. |
| **Input / select** | Tinggi 32px, border `--line`, fokus: `outline 2px var(--accent)` offset 1px. Label selalu terlihat (bukan placeholder). |
| **Checkbox** | Native `accent-color: var(--accent)`; tidak di-custom berlebihan. |
| **Tabel** | Header sticky `bg-surface-2`, teks 12px mono `--ink-2`. Hover baris `--surface-2`. Baris terpilih: border kiri 2px aksen. |
| **Drawer** | Dari kanan, 420px, border kiri 1px, tanpa overlay gelap pada desktop. `Esc` menutup. |
| **Modal** | Hanya untuk konfirmasi yang tidak bisa dibatalkan (hapus, kirim batch > 5). |
| **Toast** | Hanya untuk hasil aksi di belakang layar (scraping selesai). Pojok kanan bawah, hilang 5 dtk, tanpa ikon besar. |
| **Tag/sumber** | Teks mono 12px `--ink-3`, tanpa latar. |
| **Ikon** | Lucide, 16px, stroke 1.5. Dipakai di: link eksternal ↗, status, peringatan, tutup. |

---

## 8. State (wajib ada di setiap list)

| State | Tampilan |
|---|---|
| **Memuat** | Teks `Memuat…` `--ink-3` di area data (HTMX `hx-indicator`). Bukan spinner layar penuh. |
| **Kosong (belum ada data)** | "Belum ada lowongan. Scraping pertama berjalan setiap 3 jam, atau [jalankan sekarang]." |
| **Kosong (karena filter)** | "Tidak ada lowongan yang cocok dengan filter ini. [Reset filter] · 412 lowongan disembunyikan oleh 'Skor minimum 60'." |
| **Error** | Sebut sumber + sebab + aksi: "Glints gagal (HTTP 429, terlalu banyak request). Dicoba lagi otomatis 12:30. [Coba sekarang]" |
| **Sebagian gagal** | Banner tipis di atas tabel: "2 dari 6 sumber gagal saat scraping terakhir. [Lihat log]" |

Tidak ada ilustrasi untuk empty state.

---

## 9. Interaksi & HTMX

- **Filter**: satu `<form>` dengan `hx-get="/jobs/table" hx-target="#job-table" hx-trigger="change, input changed delay:300ms from:input[type=search]" hx-push-url="true"`. URL selalu mencerminkan filter (bisa di-bookmark & back button jalan).
- **Pilih baris**: state pilihan di Alpine.js (`x-data`), action bar muncul saat `selected.length > 0`.
- **Aksi baris** (sembunyikan/simpan): `hx-patch` + swap baris itu saja (`hx-swap="outerHTML"`), bukan reload tabel.
- **Kirim lamaran**: `hx-post` + `hx-disabled-elt="this"` untuk cegah klik ganda.
- **Status scraping** di topbar: polling `hx-trigger="every 30s"` hanya saat scraping berjalan.

### Shortcut keyboard (halaman Lowongan)
| Tombol | Aksi |
|---|---|
| `j` / `k` | Baris berikutnya / sebelumnya |
| `x` | Pilih / batal pilih baris |
| `Enter` | Buka detail |
| `a` | Siapkan lamaran untuk yang dipilih |
| `h` | Sembunyikan lowongan |
| `/` | Fokus ke kotak pencarian |
| `Esc` | Tutup drawer |

Tampilkan daftar shortcut lewat `?`.

---

## 10. Format Data

| Data | Format |
|---|---|
| Gaji | `Rp 8–12 jt` (tabel), `Rp 8.000.000 – Rp 12.000.000 / bulan` (detail). Estimasi: `~Rp 8 jt` |
| Tanggal | Relatif di tabel (`2 hr`, `5 jam`), absolut di detail (`11 Nov 2026`), absolut di `title` saat hover |
| Angka | Pemisah ribuan titik: `1.248` |
| Lokasi | Kota singkat: `Jakarta Sel.`, `Bekasi`, `Remote` |
| Skor | Bilangan bulat 0–100, tanpa `%` |

---

## 11. Anti AI Slop untuk Surat Lamaran

Surat lamaran adalah "UI" yang dilihat HR. Generator **wajib** menghindari pola tulisan AI generik. Daftar ini dipakai di prompt **dan** di pemeriksaan otomatis (§6.3).

**Frasa terlarang (ganti atau hapus):**
- "Saya sangat antusias / bersemangat untuk…"
- "Dengan penuh semangat dan dedikasi…"
- "Saya adalah individu yang pekerja keras, disiplin, dan mampu bekerja dalam tim maupun individu"
- "Perusahaan terkemuka / ternama yang Bapak/Ibu pimpin"
- "Memberikan kontribusi terbaik / nilai tambah yang signifikan"
- "Di era digital yang terus berkembang…"
- "passionate", "dynamic", "leverage", "synergy", "fast-paced environment"
- Kalimat pembuka yang mengulang judul email.

**Aturan isi:**
1. Setiap klaim didukung bukti konkret dari CV (nama proyek, teknologi, angka): "membangun 44 endpoint REST dengan Go (Gin)", bukan "berpengalaman di backend".
2. Maksimal 3 bukti, dipilih yang paling cocok dengan persyaratan lowongan.
3. Satu kalimat spesifik tentang perusahaan **hanya jika** ada informasinya di iklan; jika tidak ada, jangan mengarang.
4. Panjang 180–300 kata. Tanpa bullet point di surat email.
5. Tidak menyebut "AI", tidak ada placeholder, tidak ada tanda seru.
6. Nada: sopan, langsung, percaya diri, tidak memohon.

---

## 12. Aksesibilitas

- Kontras teks ≥ 4.5:1 (cek `--ink-3` di atas `--surface` & `--surface-2`).
- Semua aksi bisa lewat keyboard; focus ring selalu terlihat.
- Status tidak hanya dibedakan dengan warna (ada simbol + teks).
- `<label>` untuk setiap input; tabel memakai `<th scope="col">`.
- Hormati `prefers-reduced-motion` (matikan transisi drawer).
- Bahasa halaman `lang="id"`.

---

## 13. Checklist Review UI (sebelum merge)

- [ ] Tidak ada warna hex/rgb di luar blok token di `app.css`.
- [ ] Tidak ada gradient, blur, emoji, sparkle, shadow pada kartu biasa.
- [ ] Maksimal 1 tombol utama per layar.
- [ ] Ada state memuat, kosong, kosong-karena-filter, dan error.
- [ ] Pesan error menyebut sebab & aksi.
- [ ] Bisa dipakai di lebar 360px tanpa scroll horizontal.
- [ ] Mode gelap dicek.
- [ ] Copy dalam Bahasa Indonesia, kapital hanya di awal kalimat, tanpa tanda seru.
- [ ] Angka memakai font mono + tabular-nums.
