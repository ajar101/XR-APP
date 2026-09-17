# XR-App — eXtract-Report

Ringkasan arsitektur, fitur, input/output, dan rencana pengembangan aplikasi ekstraktor rekening koran.

> Dibuat: 2 September 2026 · Diperbarui: 16 September 2026
>
> **Bank aktif:** BCA, Mandiri (Kopra + e-Statement + Rekening Koran), BNI (Account Statement + Transaction Inquiry), BRI (Laporan Transaksi Finansial) — 4 bank, 7 format
>
> **Tahap:** pilot di localhost dengan satu pengguna. Deployment untuk tim ditunda; fokus sementara di perbaikan akurasi (§6.1, §6.2)

---

## 1. Apa aplikasi ini

Aplikasi web (Flask) yang menerima upload PDF rekening koran, mengekstrak seluruh mutasi & saldo secara otomatis, lalu menghasilkan laporan Excel multi-sheet — lengkap dengan kategorisasi transaksi, analisis cashflow, konsentrasi nasabah (HHI Score), dan **deteksi otomatis indikasi kejanggalan rekening** (17 indikator, dari saldo tidak balance sampai jadwal biaya admin yang tidak sesuai ketentuan bank).

Aksesnya memerlukan login, dengan dua peran (admin/pemakai) dan jejak audit yang mencatat siapa memproses rekening apa — lihat `auth.py`. Ekstraksi bisa dijalankan langsung di dalam request, atau dititipkan ke worker terpisah bila `XR_REDIS_URL` disetel (§6.2).

Tujuan jangka panjang: dipakai oleh seluruh tim (pusat & cabang) untuk mempercepat review rekening koran nasabah. Saat ini masih tahap pilot satu pengguna.

---

## 2. Arsitektur

### 2.1 Struktur folder

```
XR-APP/
├── app.py                       # Flask entrypoint — routing upload & orkestrasi (tanpa HTML)
├── wsgi.py                      # Pintu masuk server produksi (gunicorn)
├── gunicorn.conf.py             # Timeout & jumlah worker, diukur bukan ditebak
├── Dockerfile                   # Citra produksi
├── DEPLOY.md                    # Topologi, sizing, daftar periksa sebelum melayani pemakai
├── auth.py                      # Autentikasi, otorisasi per cabang, jejak audit
├── manage.py                    # CLI: buat admin pertama, kelola pengguna
├── tugas.py                     # Jalur ekstraksi — dipakai mode langsung DAN worker
├── antrean.py                   # Sambungan Redis/RQ & penentu mode kerja
├── worker.py                    # Proses pekerja mode antrean
├── templates/
│   ├── dasar.html               #   Kerangka halaman ber-login
│   ├── index.html               #   Halaman depan — daftar sheet & bank dari katalog
│   ├── login.html               #   Masuk
│   ├── riwayat.html             #   Jejak audit, disaring per cabang
│   ├── pengguna.html            #   Kelola pengguna (admin)
│   └── galat.html               #   403 & sejenisnya
├── static/
│   ├── css/app.css              #   Tampilan halaman depan
│   └── js/app.js                #   Pilih bank, drop berkas, kirim & unduh hasil
├── extractors/                  # Lapisan parsing PDF (spesifik per bank)
│   ├── base.py                  #   Kontrak abstrak BaseExtractor
│   ├── registry.py              #   Daftar bank & status aktif/nonaktif
│   ├── bca.py                   #   Extractor BCA (Giro & Tahapan)
│   ├── bni.py                   #   Dispatcher format BNI (auto-detect)
│   ├── bni_statement.py         #   Sub-extractor BNI ACCOUNT STATEMENT (tabel bergaris)
│   ├── bni_inquiry.py           #   Sub-extractor BNI TRANSACTION INQUIRY (tabel bergaris)
│   ├── bni_nama.py              #   Pipeline nama lawan transaksi, dipakai bersama kedua format BNI
│   ├── bri.py                   #   Dispatcher format BRI (auto-detect)
│   ├── bri_statement.py         #   Sub-extractor BRI LAPORAN TRANSAKSI FINANSIAL (tabel bergaris)
│   ├── mandiri.py               #   Dispatcher format Mandiri (auto-detect)
│   ├── mandiri_kopra.py         #   Sub-extractor Mandiri Kopra
│   ├── mandiri_statement.py     #   Sub-extractor Mandiri e-Statement (Livin'/Mandiri Online)
│   ├── mandiri_koran.py         #   Sub-extractor Mandiri Laporan Rekening Koran (tabel bergaris)
│   ├── mandiri_nama.py          #   Pipeline nama lawan transaksi, dipakai bersama Kopra & Rekening Koran
│   ├── peringatan.py            #   Pencatatan peringatan pembacaan dokumen (bank-agnostic)
│   └── pdf_utils.py             #   Deteksi PDF hasil scan/foto (bank-agnostic)
├── engine/                      # Lapisan pemrosesan (bank-agnostic)
│   ├── report_catalog.py        #   KATALOG: daftar sheet & indikator — dibaca engine DAN halaman depan
│   ├── penyatu_nama.py          #   Satukan varian penulisan lawan transaksi (Rekap & HHI)
│   ├── excel_builder.py         #   Generator Excel, satu builder per entri katalog
│   ├── categorizer.py           #   Kategorisasi transaksi berbasis keyword
│   ├── anomaly_detector.py      #   17 pemeriksaan indikasi kejanggalan
│   └── multi_pdf_merger.py      #   Gabungkan hasil ekstraksi dari beberapa PDF
├── tests/                       # Tes regresi
│   ├── regresi.py               #   Bandingkan hasil ekstraksi + temuan Indikasi Kejanggalan dgn snapshot
│   ├── katalog.py               #   Katalog = judul sheet di berkas Excel = janji halaman depan
│   ├── audit_nama.py            #   Bahan audit akurasi kolom Nama (sampel ber-seed tetap)
│   ├── penyatuan.py             #   Aturan penyatuan nama — termasuk apa yang HARUS tetap terpisah
│   └── snapshot/                #   Hasil yang direkam, satu JSON per PDF (1 transaksi/temuan = 1 baris)
├── references/                  # PDF contoh + hasil Excel untuk validasi manual
└── parse_rekening.py            # Skrip CLI lama, tidak terhubung ke app.py (peninggalan awal)
```

**Total kode inti: ±12.400 baris Python** (per 16 Sep 2026). Lapisan ekstraksi & laporan: extractor BCA 816 baris, `anomaly_detector.py` 963 baris, `excel_builder.py` 1.535 baris, `report_catalog.py` 218 baris. Lapisan aplikasi: `app.py` 358 baris, `auth.py` 400 baris, `tugas.py` 259 baris. Di luar itu halaman depan (`templates/` + `static/`) ±700 baris HTML/CSS/JS.

### 2.2 Prinsip desain kunci

- **Kontrak `BaseExtractor` yang ketat** (`extractors/base.py`): setiap extractor bank baru wajib mengimplementasikan `extract_saldo()` dan `extract_transaksi()` dengan struktur output yang sama persis, supaya `engine/` (Excel builder, kategorisasi, deteksi anomali) bisa bekerja **tanpa modifikasi apa pun**, apa pun bank-nya. Menambah bank baru = buat 1 file extractor + daftarkan di `registry.py`.
- **Pemisahan tegas parsing vs presentasi**: `extractors/` tidak tahu soal Excel/styling; `engine/` tidak tahu soal bank tertentu.
- **Ketentuan bank dimiliki extractor-nya, bukan engine.** Aturan yang berbeda antar bank (jadwal pendebetan biaya admin, cara mengenali baris bunga/pajak) dikirim extractor sebagai *metadata* lewat `extract_saldo()` — lihat kontraknya di `extractors/base.py`. Engine hanya membaca strukturnya. Extractor yang tidak mengirim metadata itu membuat pemeriksaan terkait **dilewati**, bukan ditebak dengan aturan bank lain.
- **`app.py` cuma orkestrasi** — terima upload, panggil extractor yang sesuai, panggil `excel_builder`, kirim file. Tidak ada logika parsing/styling di sana. HTML/CSS/JS tinggal di `templates/` dan `static/`; sebelumnya 525 dari 743 baris `app.py` adalah string HTML, sehingga berkas yang docstring-nya menjanjikan hal ini justru sebagian besar isinya styling.
- **Isi laporan didaftar sekali saja** (`engine/report_catalog.py`). Daftar sheet dan daftar indikator dibaca DUA pihak: `excel_builder` membangun sheet dengan menelusuri `SHEETS`, dan halaman depan merender daftar yang sama. Selama daftar itu ditulis dua kali, keduanya pasti berbeda cepat atau lambat — dan memang terjadi: engine sudah menghasilkan 12 sheet sementara halaman depan masih menjanjikan 8 dan tidak menyebut sheet "Indikasi Kejanggalan" sama sekali. Ketidakcocokan kini gagal saat itu juga: `create_excel()` membandingkan judul sheet yang benar-benar tertulis dengan katalog sebelum menyimpan berkas, dan `tests/katalog.py` memeriksa ketiganya (katalog, berkas Excel, halaman depan).
- **Semua keputusan besar divalidasi terhadap data riil**, bukan asumsi — setiap perbaikan bug/fitur baru diverifikasi ulang terhadap total MUTASI CR/DB yang tercetak resmi di footer PDF, sebelum dianggap selesai.

### 2.3 Alur request

```
Pemakai masuk (auth.py) ─── tanpa login, semua route di bawah menolak
        │
        ▼
Upload 1-N PDF (bank + berkas)
        │
        ▼
app.py /upload
  ├─ Validasi MURAH, dijawab seketika:
  │     ekstensi .pdf & bank dipilih  → 400 + dicatat di jejak audit
  ├─ Simpan PDF ke uploads/<job_id>/
  │
  ├─ MODE LANGSUNG (bawaan)            MODE ANTREAN (XR_REDIS_URL disetel)
  │   panggil tugas.proses_ekstraksi()   enqueue → jawab 202 {job_id}
  │   di dalam request                   worker.py mengerjakan di proses lain
  │        │                                  │
  │        └──────────────┬───────────────────┘
  │                       ▼
  │        tugas.proses_ekstraksi()   ← SATU jalur, dipakai kedua mode
  │          ├─ pdf_utils.is_probably_scanned() → tolak PDF hasil scan/foto
  │          ├─ Extractor per berkas → extract_saldo() + extract_transaksi()
  │          ├─ multi_pdf_merger.merge_extractions()
  │          │     → tolak kalau rekening beda / bulan bentrok / > 6 bulan
  │          ├─ excel_builder.create_excel() → menelusuri report_catalog.SHEETS
  │          │     └─ termasuk anomaly_detector.detect_anomalies() (§5.1)
  │          └─ hapus uploads/<job_id>/ di blok `finally`
  │
  └─ Kirim .xlsx dari MEMORI (mode langsung)
     atau simpan di Redis dengan TTL, pemakai mengunduh lewat
     /job/<id>/unduh (mode antrean)

Setiap hasil — berhasil MAUPUN gagal — tercatat di jejak audit.
Laporan Excel tidak pernah ditulis ke disk.
```

---

## 3. Fitur

| Fitur | Status |
|---|---|
| Ekstraksi PDF teks (bukan gambar) rekening BCA Giro & Tabungan | ✅ Aktif, sudah divalidasi total mutasi 100% cocok PDF sumber |
| Upload multi-PDF sekaligus (tanpa merge manual) | ✅ Aktif — maks 6 bulan gabungan, validasi rekening & bulan bentrok |
| Deteksi PDF hasil scan/foto | ✅ Aktif — ditolak dengan pesan jelas, bukan error generik |
| Kategorisasi transaksi debit/kredit otomatis (keyword-based) | ✅ Aktif |
| Analisis konsentrasi nasabah (HHI Score) | ✅ Aktif |
| Sheet "Indikasi Kejanggalan" (17 indikator deteksi anomali) | ✅ Aktif (lihat §4) |
| Bank BNI — format **ACCOUNT STATEMENT** (giro/CURRENT) | ✅ Aktif — divalidasi 100% terhadap 7 blok laporan dari 4 PDF riil (jumlah & total mutasi Debet/Kredit dan saldo akhir, dicocokkan dengan kaki ringkasan tiap blok) |
| Bank BNI — format **TRANSACTION INQUIRY** (hasil query BNI Direct) | ✅ Aktif — divalidasi terhadap 12 blok inquiry dari 8 PDF riil; 11 blok cocok 100% (Total Debit, Total Credit, saldo akhir), 1 blok **sengaja tidak cocok** karena PDF sumbernya memang tidak konsisten (lihat §7.1) |
| Bank BNI — format lain | ❌ Belum ada extractor — ditolak dengan pesan yang menyebut format yang didukung |
| Bank Mandiri — format **Kopra by Mandiri** | ✅ Aktif — divalidasi checksum terhadap ringkasan resmi PDF |
| Bank Mandiri — format **e-Statement** (Livin'/Mandiri Online) | ✅ Aktif — Tabungan, Tabungan Bisnis, Tabungan NOW & Giro; divalidasi 100% terhadap 14 periode dari 5 PDF riil |
| Bank Mandiri — format **Laporan Rekening Koran** (Account Statement Report) | ✅ Aktif — divalidasi 100% terhadap 13 periode laporan dari 9 PDF riil (jumlah & total mutasi, saldo akhir, plus rantai saldo berjalan per baris) |
| Bank Mandiri — format **E-Banking** | ❌ Belum ada extractor — ditolak dengan pesan yang menyebut formatnya |
| Bank BRI — format **Laporan Transaksi Finansial** (e-statement BRImo/Internet Banking) | ✅ Aktif — satu format untuk SEMUA jenis rekening (Giro Umum, BritAma, BritAma Bisnis/X, Simpedes, beserta varian SME-nya); divalidasi 100% terhadap 22 blok laporan (4.131 transaksi) dari 9 PDF riil (total mutasi Debet/Kredit & saldo akhir dicocokkan dengan kaki ringkasan tiap blok, plus rantai saldo berjalan per baris) |
| Bank BRI — format lain (mis. cetakan teller cabang) | ❌ Belum ada extractor — ditolak dengan pesan yang menyebut format yang didukung |
| OCR / ekstraksi PDF hasil scan | ❌ Belum diimplementasikan (lihat §6) |
| Autentikasi (login) & otorisasi dua peran (admin/pemakai) | ✅ Aktif — lihat `auth.py` |
| Jejak audit: siapa memproses rekening apa, kapan, hasilnya | ✅ Aktif — mencatat keberhasilan MAUPUN seluruh kegagalan |
| Isolasi per cabang pada halaman Riwayat | ✅ Aktif — disaring di kueri; admin melihat semua cabang |
| Job queue (ekstraksi tidak menahan koneksi) | ✅ Aktif, opsional — hanya bila `XR_REDIS_URL` disetel (§6.2) |
| Laporan Excel tidak pernah ditulis ke disk | ✅ Aktif — dibangun di memori, dikirim langsung |
| Penyatuan varian penulisan lawan transaksi (Rekap & HHI) | ✅ Aktif — 4 tahap: normalisasi → potongan sistem → kemiripan huruf → validasi konteks; lihat §5.2 |
| Penyatuan lewat daftar alias singkatan (WKS → Wira Karya Sakti) | ❌ Belum ada (lihat §6.1) |
| Histori hasil ekstraksi (bukan sekadar jejak audit) | ❌ Belum ada (lihat §6.2) |

---

## 4. Input

### 4.1 Yang diterima

- **Format**: PDF rekening koran dari **4 bank / 7 format** — BCA, Mandiri (Kopra, e-Statement, Rekening Koran), BNI (Account Statement, Transaction Inquiry), BRI (Laporan Transaksi Finansial) — dengan **layer teks** (bukan hasil scan/foto kamera). Daftar format yang aktif ada di `extractors/registry.py` (kunci `formats`), dan jumlahnya ikut ditampilkan di halaman depan.
- **Jumlah file**: 1 atau lebih PDF sekaligus dalam satu upload.
- **Total periode**: maksimum **6 bulan mutasi gabungan** dari seluruh file yang diupload.
- **Ukuran**: maks 64 MB per request.

### 4.2 Validasi yang ditegakkan (menolak upload, bukan diam-diam salah)

| Kondisi | Perilaku |
|---|---|
| File bukan `.pdf` | Ditolak, sebutkan nama file |
| PDF terdeteksi hasil scan/foto | Ditolak, pesan jelas + saran unduh ulang PDF asli |
| Nomor rekening beda antar file yang diupload | Ditolak, sebutkan file mana & rekening apa |
| Bulan yang sama muncul di >1 file | Ditolak, sebutkan bulan & 2 file yang bentrok |
| Total bulan gabungan > 6 | Ditolak, minta kurangi jumlah file |
| Format terdeteksi tapi extractor-nya belum ada (mis. Mandiri E-Banking) | Ditolak 400, menyebut format apa yang terdeteksi |
| PDF terbaca tapi tidak satu pun periode transaksi dikenali | Ditolak 400, per file — bukan HTTP 500 generik |

Semua file yang sempat diupload ke server **selalu dibersihkan** setelah request selesai — baik sukses, gagal validasi, maupun exception (`finally` block di `app.py`).

---

## 5. Output

Satu file **Excel (.xlsx)** dengan **12 sheet**. Daftar & urutannya didefinisikan di `engine/report_catalog.py` (`SHEETS`) — tabel di bawah mengikutinya, bukan sebaliknya:

| # | Sheet | Isi |
|---|---|---|
| 1 | Saldo Harian | Saldo akhir per hari, per bulan (side-by-side kalau multi-bulan), rata-rata pengendapan |
| 2 | Detail Transaksi | Seluruh mutasi: tanggal, jenis, nominal, nama pengirim/penerima, keterangan |
| 3 | Rekap Kredit | Rekap kredit per pengirim |
| 4 | Rekap Debit | Rekap debit per penerima |
| 5 | Summary Rekap Kredit | Versi ringkas Rekap Kredit — untuk dibaca cepat, tanpa rincian per bulan |
| 6 | Summary Rekap Debit | Versi ringkas Rekap Debit |
| 7 | Cashflow Harian | Net cashflow per hari per bulan |
| 8 | Kategori Debit | Auto-klasifikasi pengeluaran (gaji, operasional, angsuran, dll) |
| 9 | Kategori Kredit | Auto-klasifikasi pemasukan |
| 10 | **Daftar Indikator** | Penjelasan tiap indikator kejanggalan & cara deteksinya — supaya pemeriksa tahu apa yang diperiksa, bukan cuma melihat hasilnya |
| 11 | **Indikasi Kejanggalan** | 17 pemeriksaan otomatis kewajaran rekening — dashboard skor risiko + tabel detail temuan (filterable) |
| 12 | Summary | Identitas rekening, ringkasan keuangan per bulan, konsentrasi kredit, **HHI Score** + interpretasi |

### 5.1 Sheet Indikasi Kejanggalan — 17 indikator

Disusun sebagai dashboard ringkas (skor risiko + ringkasan per kategori) diikuti tabel detail temuan (bisa di-filter/sort di Excel), tiap temuan diberi tingkat **Tinggi / Sedang / Rendah**.

| # | Indikator | Cara deteksi |
|---|---|---|
| 1 | Saldo Tidak Balance | Saldo Awal + Kredit − Debit ≠ Saldo Akhir |
| 2 | Running Balance Tidak Konsisten | Saldo berjalan per baris transaksi ≠ saldo tercetak setelahnya |
| 3 | Mutasi Hilang / Gap Tidak Wajar | (a) jumlah transaksi hasil ekstraksi ≠ klaim resmi PDF; (b) gap ≥5 hari tanpa transaksi pada rekening aktif |
| 4 | Halaman/Periode Tidak Berurutan | Nomor halaman PDF meloncat / total halaman berubah di periode sama |
| 5 | Duplikasi Transaksi | Tanggal + nominal + deskripsi identik berulang. Baris kategori **Biaya Bank** dikecualikan — biaya per-transaksi (mis. biaya BI Fast Rp2.500) memang wajib berulang identik, jadi bukan sinyal apa pun |
| 6 | Format Nominal Tidak Konsisten | Format angka non-standar (artefak titik-ribuan/koma-desimal tertukar) |
| 7 | Template Halaman Berbeda | Header kolom standar hilang di halaman yang berisi transaksi |
| 8 | Metadata PDF Mencurigakan | Software pembuat/edit tidak lazim, tanggal modifikasi ≠ tanggal buat |
| 9 | Setoran Tunai di Hari Libur | Keyword persis "SETORAN TUNAI" jatuh di hari Minggu/libur tanggal-tetap |
| 10 | Transaksi RTGS di Hari Libur | Keyword "RTGS" jatuh di hari Minggu/libur (sistem BI-RTGS tidak beroperasi di luar hari kerja) |
| 11 | Nominal Bulat Berulang | Banyak transaksi bernilai sangat bulat (≥Rp50 juta, kelipatan Rp10 juta) |
| 12 | Indikasi Structuring | Transaksi tunai berulang mendekati ambang pelaporan LTKT Rp500 juta |
| 13 | Rasio Pajak Bunga Tidak Wajar | Pajak Bunga ÷ Bunga di luar rentang 0,195–0,205 (PPh Final 20%). Baris bunga & pajak dikenali lewat metadata `_bunga_pajak` (BCA lewat kolom Keterangan, Mandiri & BRI lewat kolom Nama) |
| 14 | Jadwal Biaya Admin Tidak Wajar | Tanggal debet biaya admin tidak sesuai jadwal resmi bank ybs. Jadwalnya dikirim extractor lewat metadata `_biaya_admin` — BCA: GIRO akhir bulan / TAHAPAN Jumat ke-3, seragam tanggal 1 sejak Juni 2026; Mandiri: akhir bulan; BNI: hari terakhir periode laporan; BRI: tanggal 20 untuk rekening BritAma — Giro & Simpedes sengaja TIDAK dikirim karena jadwalnya belum terbukti di data referensi, jadi pemeriksaannya dilewati, bukan ditebak. Baris dicocokkan **persis** lewat kolom Nama, jadi "Biaya administrasi kartu debit" Mandiri (ikut tanggal ulang tahun kartu) tidak ikut diperiksa |
| 15 | Selisih dengan Ringkasan PDF | Jumlah transaksi & total nominal hasil ekstraksi ≠ angka ringkasan resmi yang tercetak di PDF itu sendiri. Dikirim extractor lewat metadata `_checksum` |
| 16 | Urutan Tanggal Tidak Wajar | Tanggal transaksi mundur dari baris sebelumnya — rekening koran dicetak kronologis, jadi urutan 01, 02, 03, 01, 04 menandakan baris disisipkan atau dokumen disusun ulang |
| 17 | Peringatan Pembacaan Dokumen | Kondisi dokumen yang hanya diketahui extractor saat membaca PDF: halaman yang bukan bagian rekening yang diperiksa (mis. PDF rekening lain ikut ter-merge), rentang tanggal yang tidak dicakup laporan mana pun, rantai saldo berjalan yang putus, periode tumpang tindih, baris bertanggal tidak terbaca. Dikirim extractor lewat metadata `_peringatan` — saat ini oleh ketiga extractor Mandiri, BNI, dan BRI; BCA belum, jadi untuk BCA bagian ini selalu kosong |

> Catatan jujur soal keterbatasan: daftar hari libur nasional baru mencakup 4 tanggal tetap (Tahun Baru, Buruh, Kemerdekaan, Natal) — **belum** mencakup libur lunar/hijriah (Lebaran, Nyepi, Imlek, dst). Deteksi RTGS bergantung PDF mencetak kata "RTGS" secara eksplisit.

---

### 5.2 Penyatuan varian nama di Rekap & HHI

Satu lawan transaksi kerap muncul dengan beberapa penulisan di rekening yang
sama — `PT BARASENTOSA LESTARI` · `BARASENTOSA LESTARI` · `BARASENTOSA LES`.
Tanpa disatukan, satu pihak pecah jadi beberapa baris di Rekap.

Akibatnya bukan sekadar tidak rapi: **HHI Score dihitung dari pangsa tiap
nama**, jadi memecah satu pihak besar jadi tiga baris menurunkan HHI dan
membuat rekening yang sebenarnya terpusat tampak terdiversifikasi. Pada
simulasi dengan pola di atas, HHI turun dari 3350 (*Concentrated*) ke 1708
(*Moderate*) — salah ke arah yang justru berbahaya.

`engine/penyatu_nama.py` mengerjakannya dalam **empat tahap, dari yang
paling pasti ke yang paling ragu**. Urutannya bukan selera: tiap tahap hanya
menerima apa yang tidak tuntas di tahap sebelumnya, sehingga kasus yang punya
aturan pasti tidak pernah diserahkan pada tebakan.

| Tahap | Aturan | Risiko |
|---|---|---|
| 1 | Normalisasi: bentuk badan usaha di depan **maupun di belakang** (`PT X` = `X PT` = `X, PT`), sapaan (`Sdr`/`Sdri`/`Bpk`/`Ibu`), spasi, tanda baca | Nol — kuncinya identik |
| 2 | Awalan yang **terpotong di tengah kata** (potongan mesin) | Rendah — lihat di bawah |
| 3 | Kemiripan huruf (`engine/kemiripan_entitas.py`) | Tinggi — lihat di bawah |
| 4 | **Validasi konteks**: bukan menggabungkan, melainkan membatalkan | Menurunkan risiko tahap 2 & 3 |

**Nama orang tidak dibandingkan dengan nama badan usaha.** `PT HINO FINANCE
INDONESIA` dan `Sdr HENDRI` tidak pernah bertemu di perhitungan kemiripan —
bukan karena nilainya rendah, tapi karena pertanyaannya salah. Golongan
`UNKNOWN` tetap boleh bertemu keduanya: potongan mesin justru sering membuang
bagian yang menandai golongan (`AEROTRANS SERVICES I` sudah kehilangan
`PT`-nya).

**Sapaan dibuang hanya untuk MEMBANDINGKAN.** `Sdr ROLAND GAROS HUTABARAT`
dan `ROLAND GAROS HUTABARAT` jadi satu baris, tapi uraian yang ditampilkan
tetap apa adanya — yang tercetak di dokumen adalah bukti.

#### Apa yang sebenarnya diberikan kemiripan huruf — diukur, bukan ditebak

Tahap 3 memakai enam ukuran sekaligus (jarak edit Levenshtein, Jaro-Winkler,
kemiripan token, n-gram karakter, tumpang tindih token, selisih panjang
ternormalisasi) lewat satu fungsi:

```python
calculate_entity_similarity(a, b)
#  → overall_score · character_score · token_score
#    prefix_score  · length_score
```

Tingkat keyakinannya bisa disetel tanpa menyentuh algoritmanya
(`AmbangKemiripan`), dan **angka bawaannya hasil pengukuran, bukan
selera** — titik awalnya `0.95 / 0.90 / 0.80`, disetel jadi `0.90 / 0.85 /
0.80` setelah disapu dengan `python tests/ambang.py`: `>= 0.90` gabung ·
`0.85` gabung bila konteks mendukung · `0.80` tinjau · di bawah itu pisah.

Lalu diukur pada 44 PDF referensi (3.523 nama unik, ±300 ribu pasangan), dan
hasilnya menentukan seluruh rancangan di atas:

| Rentang | Pasangan ditemukan | Isinya |
|---|---:|---|
| `>= 0.95` | **1** | `ANI ROHIMAH` vs `Ibu ANI ROHIMAH` — sudah tuntas di tahap 1 |
| `0.90 – 0.949` | **0** | — |
| `0.80 – 0.899` | 75 | campuran: sebagian varian penulisan, sebagian pihak yang memang berbeda |

Dan yang paling menentukan: **peringkatnya terbalik terhadap kebenaran.**

```
FLAZZ BCA TOPUP08111441280 200,000.00      0.912   ← beda nominal, BEDA pihak
FLAZZ BCA TOPUP08111441280 300,000.00

DEXTRATAMA NITYA SANJAYA PT - HT002        0.883   ← beda nomor kontrak
DEXTRATAMA NITYA SANJAYA PT - HT003

BARASENTOSA LES                            0.738   ← potongan mesin, SATU pihak
PT BARASENTOSA LESTARI
```

Pasangan yang harus tetap terpisah bernilai **lebih tinggi** daripada
potongan mesin yang benar-benar satu pihak. Jadi tahap 3 tidak bisa dipakai
untuk memutuskan sendiri: pada ambang yang terbukti aman ia menggabungkan **3
pasangan dari 44 laporan**. Manfaat utamanya bukan menggabungkan, melainkan
**menunjukkan**: dari 175 kandidat yang dilaporkan atas 44 PDF referensi, **57
berasal dari tahap ini** — pasangan mirip yang sebelumnya tidak terlihat sama
sekali karena tidak punya relasi awalan. Ambangnya tetap dibiarkan bisa
disetel, dan `tests/kemiripan.py` menjaga temuan "peringkat terbalik" ini
supaya kalau suatu hari keadaannya berubah, itu ketahuan sebelum ambangnya
diturunkan.

#### Efek menyetel ambang — disapu, bukan ditebak

`tests/ambang.py` menjalankan penyatuan atas 44 laporan pada berbagai
setelan. Dua pertanyaannya berbeda sifat, jadi disapu terpisah.

**Ambang GABUNG** — pertanyaannya benar/salah, dan tiap penggabungan baru
diperiksa satu per satu:

| tinggi/mungkin | Menyatu dari tahap 3 | Penilaian |
|---|---:|---|
| 0.95 / 0.90 | 0 | tidak ada yang tersentuh |
| **0.90 / 0.85** | **3** | **ketiganya benar** — dipakai sekarang |
| 0.85 / 0.80 | 11 | 8 tambahan; tebakan mulai masuk |
| 0.80 / 0.75 | 30 | meleburkan dua badan usaha berbeda |
| 0.70 / 0.65 | 114 | tidak bisa dipertanggungjawabkan |

Tiga penggabungan pada 0.90/0.85 itu semuanya hal yang **aturan struktural
memang tidak bisa jangkau**:

```
DUDUNG MULYADI                ↔ DUDUNG MULYADI, M.        0.887  gelar terpotong
Lili Muniri S                 ↔ Lili Muniri S Si          0.879  gelar terpotong
INDOMOBIL FINANCE INDONE/BCA  ↔ PT INDOMOBIL FINANCE
                                INDONESIA/BCA             0.853  potongan di TENGAH
```

Yang terakhir itu kuncinya: karena ada `/BCA` menempel, kunci potongannya
bukan awalan dari kunci nama penuhnya — jadi tahap 2 tidak mungkin
melihatnya. Inilah satu-satunya hal yang terbukti hanya bisa diberikan
kemiripan huruf.

Di 0.85/0.80, yang mulai ikut tergabung justru pola yang tahap 2 sengaja
tolak: `Depari Mujeham Naska` ke `...Naska Pratama`, dan `MIRNA HASANAH` ke
`MIRNA HASANAH KOTO` — dua-duanya berhenti di batas kata. Di 0.80/0.75 ia
meleburkan `TIGA BERSAMA LOGISTIK PT` dengan `TIGA PERMATA LOGISTIK PT`.
Ketiga pasangan itu kini dijaga sebagai tes.

**Ambang TINJAU** — pertanyaannya bukan benar/salah, tapi apakah daftarnya
masih terbaca manusia:

| tinjau | Kandidat tahap 3 (44 laporan) | Terbanyak dalam satu laporan | Laporan yang melewati batas cetak 25 |
|---|---:|---:|---:|
| 0.90 | 9 | 5 | 0 |
| 0.85 | 33 | 15 | 0 |
| **0.80** | **60** | **25** | **0** |
| 0.75 | 743 | 377 | 4 |
| 0.70 | 3.872 | 2.148 | 4 |
| 0.60 | 20.700 | — | 4 |

0.80 tepat di lekuk kurva itu — dan tepat di batas cetak. Pada 0.75 ada satu
laporan dengan 377 kandidat, yang berarti 352 di antaranya dipotong diam-diam
oleh `MAKS_KANDIDAT_FUZZY`; daftar yang dipotong diam-diam lebih buruk
daripada tidak punya daftar. Biayanya juga naik: penyatuan 44 laporan dari
1,1 detik (0.80) jadi 33 detik (0.60), karena saringan batas-atas ikut
melonggar.

**Karena itu ada tahap 4.** Nilai kemiripan hanya tahu SEBERAPA BANYAK dua
nama berbeda, bukan APA yang membedakannya — dan justru jenis perbedaannya
yang memutuskan. `validasi_konteks` menolak empat hal yang semuanya bernilai
tinggi: angka yang berbeda (nominal, nomor kontrak, nomor rekening),
keterangan transaksi yang menempel di ekor nama (`THR`, `DP`, `PINBUK`),
inisial satu huruf (`Sdr M ABDINTA TARIGAN` vs `Sdr ABDINTA TARIGAN` — bisa
orang yang sama, bisa dua orang), dan golongan yang bertentangan. Semua
penolakan membawa alasan, dan alasan itu ikut tercetak di daftar kandidat.

**Apa yang membedakan potongan dari nama yang memang lebih pendek.** Relasi
awalan saja tidak cukup — `CITRA PERISAI` adalah awalan `CITRA PERISAI
LINTASINDO`, tapi menggabungkannya berarti menebak. Tanda khas pemotongan
mesin adalah potongannya jatuh **di tengah kata**:

```
BARASENTOSA LES|TARI       ← terpotong di tengah "LESTARI"  → digabung
CITRA PERISAI  |LINTASINDO ← berhenti di batas kata         → TIDAK digabung
```

Aturan yang sama sekarang dipakai untuk memilih induk ketika satu awalan
cocok ke beberapa nama, dan itu memperbaiki satu pihak yang sebelumnya tetap
pecah jadi enam baris. `PT AEROTRANS SERVICES IND` · `...INDON` ·
`...INDONESIA` adalah satu rantai potongan, tapi di berkas yang sama ada
saudara keempat: `AEROTRANS SERVICES INDON PINBUK KE BNI OPS`. Dulu
kehadirannya membatalkan seluruh keluarga itu ("cocok ke beberapa nama yang
berbeda"). Sekarang ia disingkirkan lebih dulu — ekornya menyambung di
**batas kata** (`INDON| PINBUK`), bukan menyelesaikan kata yang terpotong
(`INDON|ESIA`) — lalu sisanya menyatu, dan si saudara keempat dilaporkan
sebagai kandidat.

Yang tetap dibatalkan adalah percabangan yang dua-duanya menyelesaikan kata
terpotong: `PT GARUDA INDON` cocok ke `...INDONESIA` dan `...INDONESIA
CARGO`, dan tidak ada dasar memilih salah satunya.

> Percobaan pertama menebak batas potong dari sebaran panjang nama, dan
> **dibuang setelah diuji pada data nyata**: pada satu berkas Mandiri dengan
> 341 nama ia "mendeteksi" 16 batas berbeda padahal Mandiri tidak memotong
> sama sekali (nama terpanjangnya 110 karakter), lalu menggabungkan 26 nama
> tanpa dasar. Aturan "potong di tengah kata" menurunkannya jadi 3, dan
> ketiganya memang terpotong.

> **Daftar bentuk badan usaha di EKOR nama jauh lebih pendek** daripada di
> depan (`PT`, `CV`, `PERSERO` saja), dan itu bukan kelalaian: di ekor nama,
> singkatan yang sama berarti lain. `Armanto S Pd` dan `IIN RAJUDIN,S.PD`
> memakai `PD` sebagai gelar Sarjana Pendidikan, `MUHAMMAD ILHAM FA` memakai
> `FA` sebagai bagian nama. Membuangnya berarti memotong nama orang. `TBK`
> pun tidak masuk walau sah secara hukum — ia tidak pernah muncul di data,
> dan membuangnya membuat `PT GARUDA INDONESIA TBK` berkunci sama dengan
> `GARUDA INDONESIA`, lalu potongan `PT GARUDA INDON` jadi punya dua induk
> yang sama-sama masuk akal.

> **Satu laporan bisa memuat 771 nama unik — 297 ribu pasangan.** Menghitung
> keenam ukuran untuk semuanya makan 33 detik, dan penyatuan berjalan di
> dalam pembuatan laporan. Yang dipakai bukan sekadar "ambang murah"
> melainkan **batas atas yang terbukti**: rasio jarak edit tidak mungkin
> melebihi perbandingan panjang, jadi pasangan yang batas atasnya sudah di
> bawah ambang tinjau dilewati tanpa kehilangan satu pasangan pun. Hasilnya
> 0,51 detik (65× lebih cepat) dan 99,3% pasangan tersaring. Bahwa
> saringannya tidak menghilangkan apa pun diuji dengan cara paling kasar:
> satu berkas 305 nama dihitung SELURUH 46.360 pasangannya tanpa saringan,
> lalu dibandingkan — 0 pasangan hilang.

**Yang ragu tidak digabung, tapi juga tidak disembunyikan.** Penggabungan
keliru membuat HHI salah ke arah sebaliknya tanpa meninggalkan jejak. Karena
itu yang tidak memenuhi syarat muncul sebagai **kandidat** di bawah tabel
Rekap, lengkap dengan alasannya, supaya pemeriksa yang memutuskan. Kolom
**Varian Nama Digabung** menampilkan penulisan lain yang dilebur ke tiap
baris — penggabungan tidak pernah terjadi diam-diam.

**Diuji atas SELURUH 44 PDF referensi**, bukan sampel: Excel dibangun penuh
lewat `create_excel` untuk tiap berkas, lalu jumlah baris tiap sheet Rekap
dicocokkan dengan baris TOTAL-nya.

| | Nama unik | Menyatu | Kandidat |
|---|---:|---:|---:|
| BCA | 1.103 | 23 | 47 |
| BNI | 1.021 | 28 | 20 |
| BRI | 831 | 46 | 19 |
| Mandiri | 1.143 | 6 | 89 |
| **Total** | **4.098** | **103** | **175** |

Sebelum tahap 1 diperluas (bentuk badan usaha di ekor + sapaan) dan tahap 2
diperbaiki: 91 menyatu, 109 kandidat. Dari tambahan 12 baris yang menyatu, 9
dari tahap 1 dan 2, dan 3 dari tahap 3 pada ambang hasil pengukuran. Pengaruhnya ke HHI
terlihat pada dua berkas: `BNI_inquiry (4)` dari 2819 ke 2895 (61 pihak jadi
57) dan `BCA_8180999800` dari 774 ke 775. Keduanya bergerak ke arah yang
benar — konsentrasi yang sebelumnya dilaporkan lebih rendah daripada
kenyataannya.

Hasilnya: **44/44 berhasil dibangun, 44/44 menghasilkan 12 sheet, 0 gagal,
dan 0 selisih total.** Penggabungan tidak mengubah satu rupiah pun di mana
pun.

Perhatikan sebaran per bank: Mandiri paling banyak namanya tapi paling
sedikit menyatu (5) dan paling banyak kandidat (54) — konsisten dengan
temuan bahwa Mandiri tidak memotong nama, sehingga hampir semua relasi
awalannya berhenti di batas kata dan memang tidak boleh diputuskan sistem.

Label kategori (`Biaya Administrasi`, `Tidak Teridentifikasi`, dst) dikecualikan
— itu bukan lawan transaksi, dan meleburnya membuang informasi.

> Catatan dari sapuan itu: ada lawan transaksi bernama **"TOTAL LINTAS
> SAMUDERA"**. Skrip pemeriksa pertama mengenali baris TOTAL dari awalan
> katanya, jadi baris itu terhitung sebagai baris total dan nominalnya hilang
> dari penjumlahan — selisih Rp5.400.000 yang sempat terlihat seperti cacat
> penggabungan padahal berkas itu nol penggabungan. Kode produksi tidak punya
> kelemahan yang sama (diperiksa: tidak ada yang mencocokkan baris total lewat
> awalan), tapi ini pengingat bahwa nama nasabah bisa menyerupai kata kunci
> apa pun.

---

## 6. Status pekerjaan & rencana ke depan

### 6.0 Yang masih menggantung — ringkasan (per 16 September 2026)

Satu tabel supaya tidak perlu membaca seluruh §6 untuk tahu apa yang belum beres. Kolom "Dampak sekarang" sengaja diisi apa adanya: sebagian butir di bawah **tidak** mengganggu hasil laporan sama sekali, dan itu perlu kelihatan supaya prioritasnya tidak salah.

| # | Yang menggantung | Dampak sekarang | Prioritas | Detail |
|---|---|---|---|---|
| 1 | Bunga & pajak bunga dipasangkan per tanggal persis | 2 temuan **palsu** tingkat Rendah pada BRI (dari 9 PDF referensi) | Sedang | §6.1 |
| 2 | Jadwal biaya admin BRI Giro & Simpedes belum ada | Pemeriksaannya **dilewati** untuk kedua produk itu — bukan salah, tapi juga bukan lolos | Rendah | §6.1 |
| 3 | Daftar hari libur baru 4 tanggal tetap | Indikator hari libur hanya menangkap Minggu + 4 tanggal | Rendah | §6.1 |
| 4 | Ekor nama lawan transaksi (Mandiri 32, BCA 18 baris) | <0,6% per format; keterangan lengkap tetap ada di Detail Transaksi | Rendah | §6.1 |
| 5 | Pemisahan nama vs berita pada BNI e-channel | Berita ber-huruf besar semua masih ikut terbawa ke kolom Nama | Rendah | §6.1 |
| 6 | Akurasi kolom nama BCA, Mandiri, BNI belum diaudit ulang | Angka §7.3 dari 12 Sep belum memakai metode dua lapis seperti §7.4 | Rendah | §6.1 |
| 7 | OCR / vision untuk PDF hasil scan | Belum ada — PDF scan ditolak dengan pesan jelas, bukan salah baca | Rendah | §6.1 |
| 8 | Format tanpa extractor (Mandiri E-Banking, BNI & BRI format lain) | Ditolak 400 dengan pesan yang menyebut format terdeteksi | Rendah | §3 |
| 9 | Singkatan belum disatukan (`WKS`, `PT BAP`, `PT BMH`) | Satu pihak masih pecah di Rekap bila dokumen memakai singkatan; HHI ikut terbaca lebih rendah | Sedang | §6.1 |
| 10 | 109 kandidat penggabungan menunggu keputusan manusia | Tidak salah, tapi belum ada cara mencatat keputusannya supaya tidak ditanya ulang tiap laporan | Rendah | §6.1 |
| 11 | `XR_UPLOAD_DIR` dibaca `worker.py` tapi diabaikan `app.py` | Belum merusak apa pun (keduanya kebetulan sama), tapi menyetel variabel itu akan membuat worker menyapu folder yang salah | Sedang | §6.1 |
| 12 | Mode antrean menuntut `uploads/` dibagi antara web & worker | Belum jadi masalah karena keduanya masih satu proses/mesin; akan menggagalkan **seluruh** ekstraksi kalau dipisah container tanpa volume bersama | Sedang | §6.2 |

Butir 11 dan 12 baru ketahuan saat merancang Docker Compose, bukan dari pemakaian — keduanya laten dan tidak mempengaruhi hasil hari ini.

**Urutan yang disarankan** (per 16 September 2026, selaras dengan keputusan
pilot di §6.2):

| Tahap | Kerjakan | Kenapa sekarang |
|---|---|---|
| **Sedang berjalan** | Pilot di localhost, satu pengguna | Yang diuji akurasi ekstraksi, bukan ketahanan layanan |
| **Berikutnya** | Butir 1 — pemasangan bunga & pajak lintas hari | Satu-satunya butir terbuka yang menghasilkan temuan **palsu** |
| | Butir 6 — audit ulang nama BCA/Mandiri/BNI dua lapis | Audit BRI membuktikan sampel saja melewatkan 2 dari 3 kelas cacat |
| | Butir 2, 3 — jadwal biaya admin BRI & hari libur | Menunggu data dari luar (ketentuan BRI, kalender resmi) |
| **Saat pilot naik ke tim** | Butir 9, 10 + Docker Compose, lalu §6.2 | Butir 10 akan menggagalkan seluruh ekstraksi kalau terlewat |
| **Nanti, kalau perlu** | §6.3 migrasi | Prasyaratnya sudah terpenuhi; yang menahan tinggal nilainya |

**Tidak ada butir terbuka yang membuat angka laporan salah tanpa diketahui.** Satu-satunya yang menghasilkan temuan keliru adalah butir 1, dan temuannya bertingkat Rendah. Butir 4–6 dan 9–10 menyentuh kolom Nama, bukan nominal; butir 11–12 laten dan baru berdampak pada susunan deployment tertentu. Total mutasi dan saldo akhir seluruh format tetap dijaga checksum extractor terhadap angka resmi yang tercetak di PDF-nya sendiri.

Butir keamanan & operasional yang dulu ada di sini (debug mode menyala, tidak ada autentikasi, ekstraksi menahan koneksi, laporan menumpuk di disk) **sudah selesai** — lihat §6.4 dan `DEPLOY.md`.

---

### 6.1 Jangka pendek — masih di arsitektur Flask saat ini

Urut dari yang paling berdampak:

- **Pemasangan bunga & pajak bunga masih per tanggal persis.** `_check_rasio_pajak_bunga` (`engine/anomaly_detector.py`) mengelompokkan bunga dan pajaknya berdasarkan kolom `Tanggal` yang sama. BRI untuk sebagian bulan mendebet "PAJAK BUNGA SIMPANAN" H+1 dari bunganya (bunga 20/11, pajak 21/11), sehingga muncul **dua temuan palsu bertingkat Rendah** ("bunga tanpa pasangan pajak" dan sebaliknya) — 2 kejadian dari 9 PDF referensi BRI. Tanggalnya sengaja **tidak** digeser extractor supaya laporan tetap sama dengan dokumennya; pemasangan lintas-hari harus diputuskan di engine dan menyentuh semua bank. Ini satu-satunya butir terbuka yang menghasilkan temuan palsu, jadi paling layak dikerjakan duluan.

- **Daftar alias untuk singkatan.** Keempat tahap penyatuan nama (§5.2) tidak bisa menyentuh singkatan: `WKS` tidak punya kemiripan huruf apa pun dengan "Wira Karya Sakti", begitu pula `PT BAP KU`/`PT BAP AP152` dengan "PT Bumi Andalas Permai" dan `PT BMH MH175`. Tidak ada aturan yang bisa menyimpulkannya — ini pengetahuan yang harus diisi manusia, seperti `_biaya_admin`. Rancangannya: satu berkas daftar alias yang dipelihara pemakai, dibaca `engine/penyatu_nama.py` sebagai tahap tersendiri (tahap 1 sampai 4 sekarang sudah terpakai, lihat §5.2). Sekalian bisa menampung keputusan atas **kandidat** yang sekarang dilaporkan tiap laporan (butir 10) supaya tidak perlu diputuskan berulang.

- **Audit ulang akurasi kolom nama BCA, Mandiri, dan BNI.** Angka §7.3 (12 September) memakai sampel 18–24 baris per format tanpa pemindaian populasi penuh. Audit BRI di §7.4 menunjukkan metode dua lapis — pemindaian seluruh populasi untuk cacat berpola, ditambah sampel manual untuk cacat yang tidak berpola — menemukan hal yang tidak tertangkap sampel saja: dua dari tiga kelas cacat BRI (`ATMSTRPRM`, `;`) luput dari sampel 40 baris. Ketiga bank lain belum diperiksa dengan cara itu, jadi kemungkinan ada kelas cacat serupa yang belum ketahuan. Skripnya sudah siap: `python tests/audit_nama.py <bank>`.

- **Jadwal pendebetan biaya administrasi BRI Giro & Simpedes.** Satu-satunya jadwal yang belum ada; BCA, Mandiri, BNI, dan BRI BritAma sudah lengkap (lihat sheet **Daftar Indikator**). Sengaja tidak ditebak: tidak satu pun rekening Giro di referensi punya baris biaya administrasi rekening, dan Simpedes hanya punya satu contoh yang tanggalnya berbeda dari bunga/pajaknya (16 vs 15). Karena pemeriksaan ini **sepenuhnya digerakkan metadata `_biaya_admin`** — extractor yang tidak mengirimnya membuat pemeriksaan dilewati, bukan ditebak — menundanya tidak menimbulkan temuan palsu, dan menambahkannya nanti hanya berupa penambahan data di satu extractor tanpa perubahan engine. Butuh konfirmasi ketentuan resmi BRI.

- **Seragamkan sumber folder unggahan.** `worker.py` membaca `XR_UPLOAD_DIR`, sedangkan `app.py` memakunya ke direktori aplikasi. Pada tata letak bawaan keduanya menunjuk tempat yang sama sehingga tidak ada yang rusak sekarang — tapi begitu variabel itu disetel, web menulis ke satu folder sementara worker menyapu folder lain, dan folder yatim tidak pernah terbersihkan. Perbaikannya kecil (satu sumber nilai yang dibaca keduanya) dan sebaiknya dikerjakan bersamaan dengan Docker Compose, karena di sanalah variabel itu mulai dipakai.

- **Perluas daftar hari libur nasional** (termasuk libur lunar/hijriah dan cuti bersama) — perlu referensi kalender resmi per tahun. Selama belum ada, indikator "Setoran Tunai di Hari Libur" dan "Transaksi RTGS di Hari Libur" hanya menangkap hari Minggu + 4 tanggal tetap (1 Januari, 1 Mei, 17 Agustus, 25 Desember).

- **Nama lawan transaksi pada BNI e-channel.** Untuk transfer keluar lewat e-channel, dokumen BNI memang tidak mencetak nama penerima sama sekali — yang ada hanya nomor rekening tujuan, dan nomor itulah yang dipakai sebagai identitas di kolom Nama. Untuk transfer masuk, nama pengirim dicetak menyatu dengan berita transaksi tanpa pemisah apa pun (tidak ada gap kolom — sudah diperiksa sampai ke koordinat glif), jadi pemisahannya bertumpu pada bentuk huruf: nama dicetak sistem dalam huruf besar, berita diketik nasabah. Berita yang kebetulan ditulis huruf besar semua masih ikut terbawa. Sebagian baris e-channel juga tidak memuat nama sama sekali, hanya kode terminal/agen yang berganti tiap transaksi (`S1ACIR9510 4095`) — kode semacam itu dikenali dan digantikan nomor rekening lawan, supaya satu pengirim tidak pecah jadi puluhan baris di Rekap. Label kanal yang ikut tercetak di ekor nama ("… BI FAST") dibuang, dari daftar label yang benar-benar terlihat di PDF referensi saja. Butuh lebih banyak sampel sebelum aturannya diperketat.

- **Sisa ekor nama lawan transaksi** — tiap polanya hanya muncul 1–2 kali, jadi aturan untuknya akan lahir dari terlalu sedikit contoh. Dibiarkan apa adanya sampai ada lebih banyak sampel; keterangan lengkapnya tetap ada di Sheet Detail Transaksi.
  - **Mandiri: 32 baris dari 6.892 (0,46%).**
  - **BCA: 18 baris.** Pipeline namanya terpisah (`extractors/bca.py`, bukan `mandiri_nama.py`). Kelompok terbesarnya top-up Flazz (16 baris) yang namanya masih berupa nomor kartu. Belum digarap.
  - **BRI: 22 baris dari 4.131 (0,53%).** Ini yang sudah ditandai `Tidak Teridentifikasi` — uraiannya memang tidak memuat nama siapa pun, hanya nomor referensi kanal yang berganti tiap transaksi (`456022#818808360513#9360000212470040874`). Dibiarkan sebagai tidak teridentifikasi, bukan diisi tebakan. **Bukan cacat** — lihat §7.4.

- **OCR / Claude Vision untuk PDF hasil scan** — saat ini hanya terdeteksi & ditolak. Rekomendasi: langsung ke pendekatan vision model ketimbang OCR tradisional + regex, karena data finansial butuh akurasi tinggi dan OCR rentan salah baca digit pada tabel rapat. *(Belum digarap — dinilai jarang terjadi untuk saat ini.)*

---

### 6.2 Jangka menengah — deployment & pemakaian tim

Tiga dari lima butir di bagian ini **sudah dikerjakan** (lihat §6.4): retensi
berkas, autentikasi & otorisasi, dan job queue.

#### Status sekarang: pilot di localhost, satu pengguna

Keputusan per 16 September 2026: **piloting dijalankan di localhost dengan satu
pengguna saja** (pemilik mesin), bukan di server bersama. Deployment untuk tim
ditunda; fokus sementara kembali ke perbaikan akurasi di §6.1.

Ini pilihan yang tepat untuk tahap sekarang, dan alasannya bukan sekadar
"lebih gampang":

- Trafiknya tidak pernah meninggalkan mesin, jadi ketiadaan TLS tidak menjadi
  paparan. Begitu ada pengguna kedua lewat jaringan, kredensial dan berkas
  `.xlsx` mulai melintas terbuka dan TLS jadi wajib.
- Tidak ada pertanyaan "siapa boleh melihat data cabang siapa" yang perlu
  dijawab dulu, karena penggunanya satu.
- Yang sedang diuji pada tahap pilot adalah **akurasi ekstraksi**, bukan
  ketahanan layanan. Menunda deployment berarti menunda hal yang memang belum
  perlu dibuktikan.

Menjalankannya (mode langsung, tanpa Redis):

```bash
XR_SECRET_KEY=... XR_BIND=127.0.0.1:5000 gunicorn -c gunicorn.conf.py wsgi:app
```

Satu hal yang tetap berlaku walau penggunanya sendiri: kalau pilot memakai
rekening koran nasabah **sungguhan**, folder `data/` dan `uploads/` sebaiknya
dikecualikan dari sinkronisasi backup mesin (iCloud, OneDrive, Time Machine).
Sinkronisasi semacam itu menyalin data nasabah keluar mesin tanpa pernah ada
yang memutuskannya — dan itu jenis kebocoran yang paling senyap.

#### Rencana deployment: Docker Compose

Belum dikerjakan. Saat dilanjutkan, **Docker Compose lebih tepat daripada
`docker run`** yang sekarang ada di `DEPLOY.md`: mode antrean butuh tiga bagian
yang harus saling kenal (web, worker, Redis), dan itu persis yang Compose
tangani.

Yang harus benar di berkas compose-nya:

1. **Volume `uploads/` DIBAGI antara web dan worker.** Ini butir 10 di §6.0 dan
   satu-satunya yang bisa menggagalkan seluruh ekstraksi. Di mode antrean, web
   menyimpan PDF ke `uploads/<job_id>/` lalu mengirim **path absolutnya** ke
   worker (`app.py`). Kalau keduanya container terpisah tanpa volume bersama,
   worker mendapat `FileNotFoundError` — dan itu tidak akan terlihat sampai
   unggahan pertama.
2. **Volume `data/` juga dibagi** — worker menulis jejak audit ke basis data
   yang sama dengan web.
3. **Redis tanpa persistensi dan tanpa port terekspos.** Laporan berisi data
   rekening singgah di sana, jadi ia perlu perlakuan yang sama dengan basis
   data: tidak terjangkau dari luar mesin, dan tidak menulis dump ke disk.
4. **Dua profil dalam satu berkas** (`profiles` Compose): mode langsung (hanya
   `web`) untuk memulai, mode antrean (`web` + `worker` + `redis`) saat sudah
   terasa perlu. Satu berkas, bukan dua, supaya keduanya tidak berbeda diam-diam
   — prinsip yang sama dengan `tugas.py` yang dipakai bersama kedua mode.

Catatan khusus **Docker Desktop di Mac/Windows**: ia menjalankan VM dengan
batas memori sendiri, terpisah dari RAM mesin. Kalau batas VM-nya lebih kecil
dari `XR_WORKERS` × ~1 GB, container akan dimatikan di tengah ekstraksi PDF
besar dan gejalanya terlihat seperti aplikasi yang rusak, bukan seperti
kehabisan memori.

#### Sisa butir untuk pemakaian tim

1. **Histori hasil ekstraksi.** Jejak audit sudah mencatat *bahwa* sebuah
   rekening diproses — siapa, kapan, bank apa, nomor rekening, dan hasilnya —
   tapi hasil ekstraksinya sendiri tidak disimpan. Nilainya justru di riwayat:
   pola lintas waktu per nasabah, dan terutama histori temuan Indikasi
   Kejanggalan, bukan unduhan sekali pakai. Begitu disimpan, isolasi antar
   cabang yang sekarang hanya berlaku untuk jejak audit tinggal dipakai ulang
   untuk data hasilnya — batasnya sudah ada di `auth.py`.

2. **Topologi deployment aman untuk cabang** — VPN atau HTTPS + auth kuat,
   bukan diekspos langsung ke internet. Sudah diuraikan lengkap di `DEPLOY.md`
   (topologi, sizing terukur, konfigurasi nginx, daftar periksa), tinggal
   dijalankan saat pilot naik ke tahap tim. Yang harus dituntaskan lebih dulu
   **di luar kode**: konfirmasi tim kepatuhan soal penempatan data — lihat
   `DEPLOY.md` §1. Itu menentukan pilihan deployment, jadi sebaiknya jalan
   duluan dan tidak menunggu kodenya siap.

---

### 6.3 Jangka panjang — migrasi arsitektur (opsional, bertahap)

**Rekomendasi: FastAPI (backend) + Vue 3/TypeScript (frontend)** — tetap
opsional, dan tetap bukan gerbang untuk apa pun.

**Prasyaratnya kini sudah terpenuhi.** Dokumen ini dulu menulis "auth & queue
dulu di Flask, baru migrasi" — keduanya sudah selesai (§6.4), jadi urutan itu
tidak lagi menghalangi. Yang menahan sekarang tinggal pertimbangan nilai:
migrasi tidak menambah satu pun kemampuan yang belum ada, sementara §6.1 masih
memuat cacat yang mempengaruhi isi laporan.

Alasan menundanya juga tetap sama, dan sekarang bisa diukur: **investasinya
tidak sedang terancam**. `engine/` dan `extractors/` tidak mengimpor Flask sama
sekali (nol kecocokan pada pencarian) — ±10.000 baris logika parsing & analisis
yang portable apa adanya. Yang benar-benar perlu ditulis ulang saat migrasi
hanya `app.py` (358 baris), `auth.py` (400 baris), dan `templates/`. Pemisahan
`tugas.py` dari lapisan web pada pekerjaan job queue justru memperkecil lagi
bagian yang terikat Flask: jalur ekstraksinya sudah tidak menyentuh `request`
maupun `current_user` sama sekali.

Kapan migrasi jadi masuk akal — salah satu dari ini, bukan karena jadwal:

- **Histori hasil ekstraksi (§6.2) mulai dibangun.** Menampilkan riwayat
  temuan secara interaktif adalah pekerjaan frontend sungguhan, dan di situlah
  SPA mulai membayar dirinya sendiri. Membangunnya dengan Jinja lalu
  memindahkannya ke Vue berarti mengerjakannya dua kali.
- **Pemakainya tumbuh melewati satu tim** sehingga role-based access dan SSO
  korporat jadi kebutuhan, bukan tambahan.

Kalau salah satunya tiba:

- FastAPI native mendukung async/background task + validasi request/response
  (Pydantic) — lebih rapi untuk auth & job queue dibanding Flask + banyak
  extension.
- Vue 3 SPA membuka peluang: render Indikasi Kejanggalan langsung di browser
  (bukan cuma Excel) untuk triase cepat, histori per cabang, role-based access.
- **Fase realistis**: (1) bungkus `engine/`, `extractors/`, dan `tugas.py` yang
  ada dengan FastAPI, pindahkan auth & queue → (2) bangun SPA Vue 3 dengan
  histori & tampilan indikasi kejanggalan interaktif → (3) role-based access
  pusat/cabang, kemungkinan integrasi SSO korporat.
- **Jangan mencampur migrasi dengan perubahan besar lain.** Nasihat lama
  ("jangan migrasi sambil menambah auth") tetap berlaku bentuknya: satu
  perubahan besar pada satu waktu, supaya penyebab kerusakan bisa dipisahkan.

---

### 6.4 Baru selesai — supaya tidak dikerjakan dua kali

- **Pemeriksaan jejak cetak jadi bank-agnostik** (sebelumnya tercatat di §6.1 sebagai "belum dikerjakan"). Gerbang `BANK_POLA_MENTAH` dan `_check_mutasi_hilang` sudah tidak ada di kode. Keempat pemeriksaan berbasis jejak cetak (running balance, nomor halaman, template halaman, format nominal) kini digerakkan metadata `_provenance` berupa *fakta*, bukan pola regex per bank. **Ketujuh extractor** mengirim `_provenance` dan `_checksum`; enam di antaranya (semua kecuali BCA) juga mengirim `_peringatan`. Cakupan tiap pemeriksaan kini mengikuti apa yang memang dicetak dokumennya: nomor halaman hanya ada di BCA, Mandiri Kopra, BNI Account Statement, dan BRI; header kolom per halaman ada di semua kecuali Mandiri Rekening Koran; `teks_mentah` hanya dikirim BCA. Penjelasan per indikator ada di sheet **Daftar Indikator** di dalam laporan Excel-nya sendiri.
- **Katalog isi laporan jadi satu sumber kebenaran** (`engine/report_catalog.py`) — daftar sheet & indikator tidak lagi ditulis ulang di engine, UI, dan dokumen ini. Dijaga `tests/katalog.py`. Lihat §2.2.
- **HTML/CSS/JS keluar dari `app.py`** (743 → 229 baris) ke `templates/` dan `static/`.
- **Debug mode dimatikan.** `app.run(debug=True, host='0.0.0.0')` dulu berarti
  halaman error menampilkan potongan kode sumber dan isi variabel lokal — yang
  di aplikasi ini berisi nama pemilik rekening dan baris mutasinya. Kini mati
  secara bawaan, dan hanya mengikat localhost bila dinyalakan eksplisit.
- **Server produksi.** `wsgi.py` + `gunicorn.conf.py` + `Dockerfile` +
  `DEPLOY.md`. Timeout 180 detik (bukan bawaan 30) dan jumlah worker disetel
  menurut RAM — keduanya berdasar pengukuran, bukan tebakan: PDF 257 halaman =
  26,8 detik dan 867 MB puncak.
- **Autentikasi & otorisasi.** Login wajib, dua peran (admin/pemakai), dan
  isolasi per cabang pada halaman Riwayat yang disaring di kueri. Jejak audit
  mencatat siapa memproses rekening apa — termasuk seluruh kegagalan, karena
  semua jalan keluar `upload_file()` lewat satu helper.
- **Job queue (RQ).** Request web tidak lagi menahan koneksi selama ekstraksi:
  unggahan dijawab dalam ~0,04 detik, worker terpisah yang mengerjakan. Mode
  antrean hanya aktif kalau `XR_REDIS_URL` disetel; tanpa itu aplikasi tetap
  berjalan seperti semula. Keduanya memakai jalur ekstraksi yang sama
  (`tugas.py`) supaya isinya tidak pernah berbeda. Umur hasil di Redis
  (`XR_TTL_HASIL`, bawaan 1 jam) sekaligus jadi kebijakan retensinya.
- **Penyatuan varian penulisan lawan transaksi** (§5.2) — Rekap Kredit/Debit, Summary Rekap, dan HHI kini mengelompokkan lewat nama yang sudah disatukan, dihitung **sekali** di `create_excel` supaya keempatnya tidak bisa berbeda. Dua aturan deterministik (normalisasi + potongan di tengah kata), bukan fuzzy. 91 baris menyatu atas 44 PDF referensi, total rupiah tidak berubah satu pun. Dijaga `tests/penyatuan.py`, yang menguji **apa yang harus tetap terpisah** juga — bukan hanya apa yang digabung.
- **Kemiripan entitas & empat tahap penyatuan** (§5.2) — `engine/kemiripan_entitas.py` baru: enam ukuran kemiripan dalam satu fungsi (`calculate_entity_similarity`), penggolongan COMPANY/PERSON/UNKNOWN yang mencegah nama orang dibandingkan dengan nama badan usaha, ambang keyakinan yang bisa disetel tanpa menyentuh algoritma, dan `validasi_konteks` yang membatalkan nilai tinggi tanpa bukti. Ambangnya disapu dengan `tests/ambang.py` (baru) dan disetel dari titik awal 0.95/0.90/0.80 ke **0.90/0.85/0.80** berdasarkan hasilnya: pada 0.95/0.90 tahap kemiripan huruf menggabungkan 0 pasangan, pada 0.90/0.85 muncul 3 dan ketiganya diperiksa satu per satu dan benar, pada 0.85/0.80 tebakan mulai masuk. Atas 44 PDF referensi: 91 → 103 baris menyatu (9 dari perluasan tahap 1 & perbaikan tahap 2, 3 dari tahap 3) dan 109 → 175 kandidat, 57 di antaranya dari tahap 3. Dijaga `tests/kemiripan.py` (termasuk pagar atas temuan "peringkat kemiripan terbalik terhadap kebenaran") dan `tests/penyatuan.py`.
- **Audit akurasi kolom nama BRI** — 4.131 baris, lihat §7.4. Skripnya (`tests/audit_nama.py`) bank-agnostik dan bisa dipakai untuk audit ulang format lain.
- **Laporan Excel tidak lagi ditulis ke disk.** Dulu tiap laporan disimpan di `exports/` dan tidak pernah dihapus, sehingga nama pemilik, nomor rekening, dan seluruh mutasi menumpuk di server tanpa kedaluwarsa. Kini dibangun di `io.BytesIO` lalu dikirim langsung: tidak ada berkas yang perlu dijadwalkan hapus karena tidak ada berkas yang dibuat. Folder `exports/` tidak dibuat lagi. PDF yang diunggah tetap mendarat di disk (extractor membacanya lewat path) dan tetap dihapus di blok `finally`. Lihat catatan di §6.2 poin 2: begitu ada job queue, kebijakan retensi jadi perlu lagi.
- **Tiga kelas cacat kolom nama BRI diperbaiki** (§7.4): kode kanal `ATMSTRPRM` kini digantikan nomor rekening tujuan yang tercetak di uraiannya — 40 baris yang tadinya menggumpal jadi satu entri Rekap kini terurai jadi 14 lawan transaksi berbeda; token `0` (12 baris) dan `;` (7 baris) kini ditandai `Tidak Teridentifikasi`. Penyaringan token sampah ditaruh di satu pagar (`_bermakna`) yang dilewati SEMUA cabang penguraian nama, bukan ditambal per cabang, supaya bentuk uraian baru tidak lolos lagi.

---

## 7. Riwayat perbaikan signifikan (untuk konteks)

Sepanjang pengembangan, sebagian besar waktu dihabiskan memperbaiki **akurasi ekstraksi** berdasarkan pengujian terhadap PDF riil (bukan cuma pengujian sintetis):

- Klasifikasi Debit/Kredit yang sebelumnya salah baca nama nasabah ("DBS", "M-BCA") sebagai penanda transaksi.
- Regex nominal yang salah tangkap pecahan angka pada baris PDF dengan artefak format Eropa (titik ribuan/koma desimal) — sempat menyebabkan selisih ~301 juta rupiah pada satu bulan sebelum diperbaiki.
- Nama pengirim/penerima yang kepotong kode channel (`/KBB`, `M-BCA`, `MyBCA`), kode referensi VA, dan artefak page-break (`TANGGAL :dd/mm`).
- Modul `anomaly_detector.py` sendiri sempat punya bug serupa (klasifikasi tidak baca satu baris penuh, saldo berjalan tidak reset di batas bulan) yang menyebabkan false-positive besar — sudah diperbaiki dan divalidasi ulang.

Prinsip yang dipegang konsisten: **setiap klaim perbaikan diverifikasi terhadap angka resmi di PDF** (total MUTASI CR/DB tercetak di footer setiap bulan), bukan sekadar "kelihatannya sudah benar".

### 7.1 Catatan format BNI TRANSACTION INQUIRY

Format kedua BNI ini (hasil query di BNI Direct, bukan e-statement bulanan) punya empat sifat yang menentukan cara membacanya — rinciannya ada di docstring `extractors/bni_inquiry.py`:

1. **Tabelnya bergaris dan baris header-nya menyebut nama kolomnya.** Batas baris & kolom diambil dari kotak sel yang dicetak dokumen, dengan baris header sebagai jangkar letak tabel. Jangkar itu bukan hiasan: di atas tabel ada blok "Account Information" yang juga digambar sebagai kotak-kotak dengan lebar kolom yang berbeda, dan pada halaman yang transaksinya sedikit kotak blok itu lebih banyak daripada kotak tabelnya.
2. **Kolom Amount dicetak bersih** (tidak seperti ACCOUNT STATEMENT yang nominalnya rusak karena efek tebal), jadi nominal diambil dari sana dan kolom Balance dipakai sebagai **pemeriksanya**. Rantai saldo itu diserahkan ke engine lewat `_provenance`, sehingga baris yang nominal dan saldonya tidak sejalan muncul sebagai temuan — bukan diam-diam diperbaiki extractor.
3. **Satu PDF bisa memuat beberapa hasil inquiry, dan urutannya tidak kronologis** (satu PDF referensi memuat blok Desember 2024, lalu Februari 2025, baru Januari 2025 — wajar, tiap blok adalah query terpisah yang digabung). Karena itu sambungan antar periode diperiksa setelah blok **diurutkan kronologis**; kalau diperiksa menurut urutan cetak, tiap berkas seperti itu akan melaporkan "ada rentang tanggal yang tidak dicakup" yang sebenarnya tidak ada.
4. **Penomoran baris ("No.") berurut dari 1 di tiap blok** — jaminan kelengkapan yang tidak dimiliki format BNI lain. Satu baris yang dihapus dari dokumen meninggalkan lubang di penomorannya, dan itu terlihat bahkan ketika seluruh angka ringkasannya ikut disesuaikan. Pemeriksaannya ada di `validate()`.

Dokumen ini tidak mencetak nomor halaman dan tidak mencetak Ending Balance. Saldo akhir yang diharapkan karena itu dihitung dari tiga angka yang memang tercetak di kepala tiap blok (Beginning Balance − Total Debit + Total Credit) lalu dibandingkan dengan saldo baris terakhir — tetap angka dokumen, bukan angka kita sendiri.

**Satu PDF referensi (`BNI_inquiry (1).pdf`) sengaja dibiarkan gagal checksum.** Dokumen itu memang tidak konsisten dengan dirinya sendiri: nominal biaya admin tercetak `25,0000.00` (bukan `25,000.00`), satu tanggal tercetak `05/01/206`, total debit resminya meleset Rp225.000 dari jumlah baris yang tercetak, dan saldo akhirnya meleset Rp1.000.000. Metadata PDF-nya menyebut *airSlate* — perangkat penyunting PDF, bukan pencetak rekening. Semua itu **muncul sebagai temuan di sheet Indikasi Kejanggalan**, persis seperti yang diharapkan dari dokumen yang disunting. Baris yang tanggalnya tidak terbaca tidak ditebak tanggalnya: ia tidak masuk Detail Transaksi (tidak ada bulan yang bisa jadi tempatnya) tapi tetap ikut dihitung di checksum, sehingga selisihnya terlihat di dua tempat sekaligus.

> Catatan untuk pengembangan berikutnya: `SOFTWARE_EDITOR_MENCURIGAKAN` di `engine/anomaly_detector.py` belum memuat "airSlate", sehingga PDF di atas hanya masuk kategori "Metadata PDF" (Rendah), bukan "Metadata PDF Mencurigakan" (Sedang). Temuan numeriknya sudah cukup untuk menandai dokumen itu, tapi daftar produsen PDF layak ditambah.

**Pipeline nama lawan transaksi dipakai bersama.** Kolom Description format ini bertata bahasa sama dengan ACCOUNT STATEMENT (segmen dipisah `|`, klausa `PEMINDAHAN KE <rekening> <nama>`, kode cabang 3 digit di depan nama pengirim antarbank), jadi aturannya dipindahkan ke `extractors/bni_nama.py` dan dipakai kedua extractor — setara `mandiri_nama.py` untuk keluarga dokumen Mandiri. Pemindahan itu diverifikasi bebas-regresi terhadap snapshot 4 PDF ACCOUNT STATEMENT yang sudah ada.

### 7.2 Nama lawan transaksi: segmen tengah didahulukan

Setelah extractor TRANSACTION INQUIRY jalan, kolom nama diaudit menyeluruh (lihat §7.3). Hasilnya menemukan satu cacat pada aturan yang sudah lama ada di pipeline BNI — tidak pernah muncul di ACCOUNT STATEMENT, tapi terpicu oleh bentuk keterangan yang lazim di dokumen inquiry:

```
TRANSFER KE | PEMINDAHAN KE 760360200001004 | PT KINI TEKNOLOGI INDON SIA | PEMBAYARAN KINI
                                               ^^^ nama pihak              ^^^ berita nasabah
```

Ketika klausa `PEMINDAHAN` hanya memuat nomor, pipeline langsung melompat ke segmen TERAKHIR — yang di bentuk ini berisi berita yang diketik nasabah. Akibatnya satu pihak pecah jadi beberapa nama mengikuti beritanya (`PEMBAYARAN KINI`, `PEMBAYARAN PT KINI`, `PEMBAYARAN KE PT KINI`) — persis yang mau disatukan sheet Rekap.

Sekarang segmen **di antara** klausa pemindahan dan segmen terakhir diperiksa lebih dulu, dengan dua pagar:

- **Segmen isian kosong (`0000000000000000`) dilewati, bukan menghentikan pencarian.** Pada transaksi masuk lewat e-channel, segmen tengahnya memang nol dan namanya justru ada di segmen terakhir — 225 baris ACCOUNT STATEMENT berbentuk begitu dan tidak boleh ikut berubah.
- **Hanya segmen yang murni nama (tanpa satu angka pun) yang diterima.** Segmen yang mencampur nama dengan nomor/periode adalah field gabungan, bukan field nama (`TAFS PERIODE 22 - 10022025`, `DAIHATSU FINANCE PERIODE 4 - 30072026`). Tanpa pagar ini, memakainya memecah satu pihak jadi sebanyak periodenya — penyakit yang sama, cuma pindah tempat. Untuk bentuk itu nomor rekening tetap yang dipakai, dan pihaknya tetap terkumpul jadi satu.

Sekalian diperketat: segmen terakhir yang dibuka **nomor referensi berdigit banyak** (`^0*\d{6,}`) tidak lagi ditambang namanya. Sebelumnya hanya nomor rekening lawan yang dikecualikan, sehingga nomor rekening SENDIRI diikuti berita masih lolos jadi "nama" (`0019692615 08 FEE RTGS PT AEROTRANS`).

Dampaknya diperiksa baris per baris sebelum snapshot direkam ulang: **108 baris berubah, seluruhnya di TRANSACTION INQUIRY, 8 pola unik, semuanya perbaikan; ACCOUNT STATEMENT nol baris berubah.** Contoh nyata: 96 transaksi ke PT Kini Teknologi yang tadinya terpecah tiga kini terkumpul jadi satu baris Rekap.

**Yang sengaja dibiarkan:** 4 baris (0,09% dari seluruh baris BNI) yang segmen terakhirnya berisi teks warkat (`CEK 1 BUKU 25 LBR CF 174226-174250`, `BY CEK NO CF351626-351650`). Mengenalinya butuh daftar kata kunci berita, dan daftar seperti itu berisiko memotong nama pihak yang kebetulan mirip — harga yang tidak sebanding untuk 4 baris. Keempatnya tetap terkumpul konsisten, hanya kurang informatif.

### 7.3 Akurasi kolom nama (audit 12 September 2026)

Audit sampel acak **proporsional** atas 120 baris dari seluruh 6 format, dinilai manual terhadap kolom keterangan. "Benar" = sama dengan yang akan dibaca pemeriksa manusia, ATAU label/nomor rekening yang tepat ketika dokumen memang tidak mencetak nama.

| Format | Populasi | Sampel | Benar | Akurasi |
|---|---:|---:|---:|---:|
| BCA | 4.244 | 18 | 17 | 94,4% |
| BNI Transaction Inquiry | 2.091 | 24 | 22 | 91,7% |
| BNI Account Statement | 2.198 | 24 | 24 | 100% |
| Mandiri e-Statement | 1.604 | 18 | 18 | 100% |
| Mandiri Kopra | 2.964 | 18 | 18 | 100% |
| Mandiri Rekening Koran | 2.324 | 18 | 18 | 100% |
| **Gabungan** | **15.425** | **120** | **117** | **97,5%** (95% CI 92,9–99,1%) |

Tabel di atas disusun sebelum extractor BRI ada. **BRI diaudit terpisah pada 16 September 2026** — lihat §7.4.

Angka per-format JANGAN dipakai sendiri — sampel 18–24 baris terlalu kecil (CI-nya selebar 74–100%). Yang bisa dipertanggungjawabkan hanya angka gabungan, ~97% ±3. Dua dari tiga kesalahan pada sampel BNI inquiry adalah cacat yang sudah diperbaiki di §7.2, jadi angka sebenarnya kini lebih tinggi dari tabel ini — tabelnya sengaja tidak diperbarui tanpa audit ulang.

Yang **eksak** (dihitung atas seluruh populasi, bukan sampel): nama kosong hanya **1 dari 15.425 baris (0,006%)**, dan itu pun di PDF yang memang rusak.

**Penting jangan salah baca:** pada BNI, ~28–33% baris memakai nomor rekening sebagai nama dan ~26–31% memakai label bank. Itu **bukan kegagalan ekstraksi** — untuk transfer e-channel dokumennya memang tidak mencetak nama (teks setelah nomor rekening adalah berita nasabah: "PINBUK KE BCA OPS", "SEWA KENDARAAN BUGGY CAR"), dan untuk biaya admin/jasa giro/PPh memang tidak ada lawan transaksi. BCA & Mandiri mencapai 98–100% "nama betulan" karena formatnya mencetak nama di kolom tersendiri.

**Batasan:** tidak ada label ground-truth independen; penilaian benar/salah adalah pembacaan atas kolom keterangan oleh satu penilai. Untuk angka yang lebih keras perlu sampel berlabel oleh tim. Audit ulang keenam format ini bisa dijalankan dengan `python tests/audit_nama.py <bank>` — skrip yang sama yang dipakai untuk §7.4.


### 7.4 Akurasi kolom nama — BRI (audit 16 September 2026)

Dilakukan terpisah karena extractor BRI baru ada setelah audit §7.3. Populasi: **4.131 baris dari 9 PDF referensi, 9 rekening, 7 jenis produk** (Giro Umum SME, Giro Umum, BritAma Bisnis, BritAma Bisnis SME, BritAma X SME, Britama Digital, Simpedes Umum).

Diaudit dua lapis, karena keduanya menangkap hal berbeda:

**(a) Pemindaian seluruh populasi** — eksak, bukan sampel. Hanya menangkap cacat yang bisa dikenali dari *bentuknya*:

| Temuan | Baris | % populasi |
|---|---:|---:|
| Nama kosong | 0 | 0,00% |
| Kode kanal dipakai sebagai nama (`ATMSTRPRM`) | 40 | 0,97% |
| Token sampah dipakai sebagai nama (`0`) | 12 | 0,29% |
| Token sampah dipakai sebagai nama (`;`) | 7 | 0,17% |
| **Total cacat terpola** | **59** | **1,43%** |
| **Baris yang membawa identitas terpakai** | **4.072** | **98,57%** |

> Kelas `;` baru ketahuan saat perbaikan dikerjakan, bukan pada audit awal — audit awal mencatat 52 baris (1,26%). Uraiannya `- ; ESB:INDS:…`: tanda hubung di depan dibuang perapian nama, menyisakan titik koma yang lolos jadi "nama". Angka di tabel ini sudah dikoreksi.

Ditandai `Tidak Teridentifikasi` — 22 baris (0,53%) — **tidak** dihitung cacat: dokumennya memang tidak memuat nama siapa pun, hanya nomor referensi kanal yang berganti tiap transaksi, dan extractor sengaja tidak menebak.

Diperiksa juga arah pengambilan nama pada baris berpola `FROM … TO …` (47 baris): untuk mutasi Kredit harus mengambil sisi `FROM`, untuk Debit sisi `TO`. **0 kesalahan arah.**

**(b) Sampel acak 40 baris, dinilai manual** terhadap kolom keterangan — menangkap cacat yang bentuknya wajar tapi isinya keliru, yang tidak mungkin ditemukan pemindaian pola:

| Sampel | Benar | Akurasi |
|---:|---:|---|
| 40 | 38 | **95,0%** (95% CI Wilson 83,5–98,6%) |

Kedua kesalahannya adalah baris `ATMSTRPRM` yang sama — artinya **tidak ditemukan satu pun cacat di luar kelas yang bisa dikenali pemindaian (a)**. Tidak ada nama yang terbaca rapi tapi ternyata bukan lawan transaksinya.

**Cara membaca kedua angka ini.** `ATMSTRPRM` hanya 0,97% populasi, jadi harapan kemunculannya dalam 40 baris adalah ±0,4 baris — yang tertarik 2, sekitar 5× lipat. Sampelnya kebetulan melebihkan cacat itu, sehingga **95,0% understate**. Angka yang lebih bisa dipertanggungjawabkan adalah **98,57%** dari pemindaian populasi penuh, dengan syarat yang harus disebut jujur: angka itu hanya mencakup cacat yang berpola.

Dan justru di situ letak pelajarannya: **dua dari tiga kelas cacat tidak tertangkap sampel manual** — `;` sama sekali tidak muncul di 40 baris itu (0,17% populasi), dan `ATMSTRPRM` muncul hanya karena kebetulan. Pemindaian populasi penuh yang menemukan keduanya. Sebaliknya, sampel (b) yang membuktikan tidak ada cacat DI LUAR pola: pada 38 baris sisanya, setiap nama cocok dengan keterangannya. Keduanya saling menutupi lubang masing-masing, dan tidak satu pun cukup sendirian. Itulah alasan §6.1 mencantumkan audit ulang BCA/Mandiri/BNI dengan metode yang sama.

**Ketiga kelas cacat di atas sudah diperbaiki** (lihat §6.4). Sesudah perbaikan, atas populasi yang sama:

| | Sebelum | Sesudah |
|---|---:|---:|
| Cacat terpola | 59 (1,43%) | **0 (0,00%)** |
| Ditandai `Tidak Teridentifikasi` | 22 (0,53%) | 41 (0,99%) |
| Membawa identitas terpakai | 4.072 (98,57%) | **4.090 (99,01%)** |

Yang berubah persis 59 baris, dan **hanya kolom Nama** — diperiksa terhadap snapshot regresi: dari 59 baris JSON yang berubah, satu-satunya indeks kolom yang berbeda adalah indeks 4 (Nama Pengirim/Penerima). Tanggal, jenis mutasi, nominal, dan keterangan tidak tersentuh, begitu pula seluruh temuan Indikasi Kejanggalan.

Audit ini bisa diulang: `python tests/audit_nama.py bri` (seed tetap 20260916, jadi baris sampelnya sama persis). Skripnya bank-agnostik dan siap dipakai untuk audit ulang format lain.

**Batasan yang sama dengan §7.3:** tidak ada label ground-truth independen; penilaian benar/salah adalah pembacaan kolom keterangan oleh satu penilai.

**Semua PDF referensi BNI Transaction Inquiry masuk tes regresi** (`tests/regresi.py`); awalan `BNI_` didaftarkan di `AWALAN_BANK` supaya berkasnya tidak dilewati diam-diam.
