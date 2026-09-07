# XR-App — eXtract-Report

Ringkasan arsitektur, fitur, input/output, dan rencana pengembangan aplikasi ekstraktor rekening koran.

> Dibuat: 2 September 2026 · Status: fokus stabilisasi BCA (BNI & Mandiri dinonaktifkan sementara)

---

## 1. Apa aplikasi ini

Aplikasi web (Flask) yang menerima upload PDF rekening koran, mengekstrak seluruh mutasi & saldo secara otomatis, lalu menghasilkan laporan Excel multi-sheet — lengkap dengan kategorisasi transaksi, analisis cashflow, konsentrasi nasabah (HHI Score), dan **deteksi otomatis indikasi kejanggalan rekening** (13 indikator, dari saldo tidak balance sampai jadwal biaya admin yang tidak sesuai ketentuan bank).

Tujuan jangka panjang: dipakai oleh seluruh tim (pusat & cabang) untuk mempercepat review rekening koran nasabah.

---

## 2. Arsitektur

### 2.1 Struktur folder

```
XR-APP/
├── app.py                       # Flask entrypoint — UI, routing upload, orkestrasi
├── extractors/                  # Lapisan parsing PDF (spesifik per bank)
│   ├── base.py                  #   Kontrak abstrak BaseExtractor
│   ├── registry.py              #   Daftar bank & status aktif/nonaktif
│   ├── bca.py                   #   Extractor BCA (satu-satunya yang aktif)
│   ├── bni.py                   #   Extractor BNI (nonaktif sementara)
│   ├── mandiri.py               #   Dispatcher format Mandiri (nonaktif sementara)
│   ├── mandiri_kopra.py         #   Sub-extractor Mandiri Kopra (nonaktif sementara)
│   └── pdf_utils.py             #   Deteksi PDF hasil scan/foto (bank-agnostic)
├── engine/                      # Lapisan pemrosesan (bank-agnostic)
│   ├── excel_builder.py         #   Generator Excel 9-sheet
│   ├── categorizer.py           #   Kategorisasi transaksi berbasis keyword
│   ├── anomaly_detector.py      #   13 pemeriksaan indikasi kejanggalan
│   └── multi_pdf_merger.py      #   Gabungkan hasil ekstraksi dari beberapa PDF
├── references/                  # PDF contoh + hasil Excel untuk validasi manual
└── parse_rekening.py            # Skrip CLI lama, tidak terhubung ke app.py (peninggalan awal)
```

**Total kode inti: ±4.900 baris Python** (per 2 Sep 2026): `app.py` 695 baris, extractor BCA 565 baris, `anomaly_detector.py` 825 baris, `excel_builder.py` 1.193 baris.

### 2.2 Prinsip desain kunci

- **Kontrak `BaseExtractor` yang ketat** (`extractors/base.py`): setiap extractor bank baru wajib mengimplementasikan `extract_saldo()` dan `extract_transaksi()` dengan struktur output yang sama persis, supaya `engine/` (Excel builder, kategorisasi, deteksi anomali) bisa bekerja **tanpa modifikasi apa pun**, apa pun bank-nya. Menambah bank baru = buat 1 file extractor + daftarkan di `registry.py`.
- **Pemisahan tegas parsing vs presentasi**: `extractors/` tidak tahu soal Excel/styling; `engine/` tidak tahu soal bank tertentu (kecuali `anomaly_detector.py` yang sebagian pemeriksaannya memang spesifik pola teks BCA, dijelaskan di §4).
- **`app.py` cuma orkestrasi** — terima upload, panggil extractor yang sesuai, panggil `excel_builder`, kirim file. Tidak ada logika parsing/styling di sana.
- **Semua keputusan besar divalidasi terhadap data riil**, bukan asumsi — setiap perbaikan bug/fitur baru diverifikasi ulang terhadap total MUTASI CR/DB yang tercetak resmi di footer PDF, sebelum dianggap selesai.

### 2.3 Alur request

```
User upload 1-N PDF (bank + file)
        │
        ▼
app.py /upload
  ├─ Validasi ekstensi .pdf & bank dipilih
  ├─ pdf_utils.is_probably_scanned()  → tolak kalau PDF hasil scan/foto
  ├─ Extractor per file → extract_saldo() + extract_transaksi()
  ├─ multi_pdf_merger.merge_extractions()
  │     → tolak kalau: rekening beda antar file / bulan bentrok / total > 6 bulan
  ├─ excel_builder.create_excel()
  │     ├─ Sheet 1-8: data keuangan (lihat §3)
  │     └─ Sheet 9: anomaly_detector.detect_anomalies() (lihat §4)
  └─ Kirim file .xlsx ke user, bersihkan file upload (finally-block)
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
| Sheet "Indikasi Kejanggalan" (13 indikator deteksi anomali) | ✅ Aktif (lihat §4) |
| Bank BNI | ⏸ Nonaktif sementara (kode masih ada, tinggal `enabled: True` di registry) |
| Bank Mandiri (Kopra/E-Banking/Statement) | ⏸ Nonaktif sementara — hanya format Kopra yang sempat diimplementasikan |
| Bank BRI | 🔜 "Coming soon" di UI, belum ada extractor |
| OCR / ekstraksi PDF hasil scan | ❌ Belum diimplementasikan (lihat §6) |

---

## 4. Input

### 4.1 Yang diterima

- **Format**: PDF rekening koran BCA (Giro & Tabungan/Tahapan), dengan **layer teks** (bukan hasil scan/foto kamera).
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
| Gagal ekstrak saldo (format tak dikenali) | Ditolak, per file |

Semua file yang sempat diupload ke server **selalu dibersihkan** setelah request selesai — baik sukses, gagal validasi, maupun exception (`finally` block di `app.py`).

---

## 5. Output

Satu file **Excel (.xlsx)** dengan **9 sheet**:

| # | Sheet | Isi |
|---|---|---|
| 1 | Saldo Harian | Saldo akhir per hari, per bulan (side-by-side kalau multi-bulan), rata-rata pengendapan |
| 2 | Detail Transaksi | Seluruh mutasi: tanggal, jenis, nominal, nama pengirim/penerima, keterangan |
| 3 | Rekap Kredit | Rekap kredit per pengirim |
| 4 | Rekap Debit | Rekap debit per penerima |
| 5 | Cashflow Harian | Net cashflow per hari per bulan |
| 6 | Kategori Debit | Auto-klasifikasi pengeluaran (gaji, operasional, angsuran, dll) |
| 7 | Kategori Kredit | Auto-klasifikasi pemasukan |
| 8 | Summary | Identitas rekening, ringkasan keuangan per bulan, konsentrasi kredit, **HHI Score** + interpretasi |
| 9 | **Indikasi Kejanggalan** | 13 pemeriksaan otomatis kewajaran rekening — dashboard skor risiko + tabel detail temuan (filterable) |

### 5.1 Sheet 9 — 13 indikator kejanggalan

Disusun sebagai dashboard ringkas (skor risiko + ringkasan per kategori) diikuti tabel detail temuan (bisa di-filter/sort di Excel), tiap temuan diberi tingkat **Tinggi / Sedang / Rendah**.

| # | Indikator | Cara deteksi |
|---|---|---|
| 1 | Saldo Tidak Balance | Saldo Awal + Kredit − Debit ≠ Saldo Akhir |
| 2 | Running Balance Tidak Konsisten | Saldo berjalan per baris transaksi ≠ saldo tercetak setelahnya |
| 3 | Mutasi Hilang / Gap Tidak Wajar | (a) jumlah transaksi hasil ekstraksi ≠ klaim resmi PDF; (b) gap ≥5 hari tanpa transaksi pada rekening aktif |
| 4 | Halaman/Periode Tidak Berurutan | Nomor halaman PDF meloncat / total halaman berubah di periode sama |
| 5 | Duplikasi Transaksi | Tanggal + nominal + deskripsi identik berulang |
| 6 | Format Nominal Tidak Konsisten | Format angka non-standar (artefak titik-ribuan/koma-desimal tertukar) |
| 7 | Template Halaman Berbeda | Header kolom standar hilang di halaman yang berisi transaksi |
| 8 | Metadata PDF Mencurigakan | Software pembuat/edit tidak lazim, tanggal modifikasi ≠ tanggal buat |
| 9 | Setoran Tunai di Hari Libur | Keyword persis "SETORAN TUNAI" jatuh di hari Minggu/libur tanggal-tetap |
| 10 | Transaksi RTGS di Hari Libur | Keyword "RTGS" jatuh di hari Minggu/libur (sistem BI-RTGS tidak beroperasi di luar hari kerja) |
| 11 | Nominal Bulat Berulang | Banyak transaksi bernilai sangat bulat (≥Rp50 juta, kelipatan Rp10 juta) |
| 12 | Indikasi Structuring | Transaksi tunai berulang mendekati ambang pelaporan LTKT Rp500 juta |
| 13 | Rasio Pajak Bunga Tidak Wajar | Pajak Bunga ÷ Bunga di luar rentang 0,195–0,205 (PPh Final 20%) |
| 14 | Jadwal Biaya Admin Tidak Wajar | Tanggal debet "BIAYA ADM" tidak sesuai jadwal resmi BCA (beda aturan GIRO/TAHAPAN, berubah per Juni 2026) |

> Catatan jujur soal keterbatasan: daftar hari libur nasional baru mencakup 4 tanggal tetap (Tahun Baru, Buruh, Kemerdekaan, Natal) — **belum** mencakup libur lunar/hijriah (Lebaran, Nyepi, Imlek, dst). Deteksi RTGS bergantung PDF mencetak kata "RTGS" secara eksplisit.

---

## 6. Rencana implementasi ke depan

Disusun berdasarkan diskusi sepanjang pengembangan, urut prioritas realistis (bukan urut "keren"):

### 6.1 Jangka pendek — masih di arsitektur Flask saat ini
- **Reaktivasi BNI & Mandiri** setelah proses stabilisasi pola BCA (regex, deteksi nama, dsb.) dianggap cukup matang untuk dijadikan acuan pola bank lain.
- **Perluas daftar hari libur nasional** (termasuk libur lunar/hijriah) — perlu referensi kalender resmi per tahun.
- **OCR / Claude Vision untuk PDF hasil scan** — saat ini hanya terdeteksi & ditolak. Rekomendasi: langsung ke pendekatan vision model (Claude API) ketimbang OCR tradisional + regex, karena data finansial butuh akurasi tinggi dan OCR rentan salah baca digit pada tabel rapat. *(Belum digarap — dinilai jarang terjadi untuk saat ini.)*

### 6.2 Jangka menengah — untuk pemakaian tim (pusat & cabang)
Ini yang **lebih mendesak daripada migrasi framework**, karena aplikasi saat ini masih single-user/single-page tanpa histori:

1. **Autentikasi & otorisasi** — siapa boleh upload, isolasi data antar cabang.
2. **Background job queue** (Celery/RQ/arq) — ekstraksi PDF besar (296 halaman ⇒ 60–90 detik) saat ini blocking request; tidak scalable untuk banyak user bersamaan.
3. **Database + audit log** — riwayat upload, hasil ekstraksi, dan terutama histori temuan Sheet 9 (nilainya justru di riwayat, bukan cuma unduhan sekali pakai).
4. **Kebijakan retensi & keamanan file** — PDF/Excel yang diupload harus punya jadwal hapus otomatis (data rekening koran = PII finansial sensitif).
5. **Topologi deployment aman** — VPN atau HTTPS+auth kuat untuk akses cabang, bukan exposed langsung ke internet.

### 6.3 Jangka panjang — migrasi arsitektur (opsional, bertahap)
**Rekomendasi: FastAPI (backend) + Vue 3/TypeScript (frontend)**, tapi *setelah* poin 6.2 tuntas secara konsep, bukan sebagai gerbang:
- `extractors/` dan `engine/` **portable tanpa perubahan** — sudah 100% terpisah dari Flask, jadi investasi parsing tidak hangal saat migrasi.
- FastAPI native mendukung async/background task + validasi request/response (Pydantic) — lebih rapi untuk auth & job queue dibanding Flask + banyak extension.
- Vue 3 SPA membuka peluang: render Sheet 9 langsung di browser (bukan cuma Excel) untuk triase cepat, histori per cabang, role-based access.
- **Fase realistis**: (1) bungkus engine yang ada dengan FastAPI + job queue + auth dasar → (2) bangun SPA Vue 3 dengan histori & tampilan indikasi kejanggalan interaktif → (3) role-based access pusat/cabang, kemungkinan integrasi SSO korporat.

---

## 7. Riwayat perbaikan signifikan (untuk konteks)

Sepanjang pengembangan, sebagian besar waktu dihabiskan memperbaiki **akurasi ekstraksi** berdasarkan pengujian terhadap PDF riil (bukan cuma pengujian sintetis):

- Klasifikasi Debit/Kredit yang sebelumnya salah baca nama nasabah ("DBS", "M-BCA") sebagai penanda transaksi.
- Regex nominal yang salah tangkap pecahan angka pada baris PDF dengan artefak format Eropa (titik ribuan/koma desimal) — sempat menyebabkan selisih ~301 juta rupiah pada satu bulan sebelum diperbaiki.
- Nama pengirim/penerima yang kepotong kode channel (`/KBB`, `M-BCA`, `MyBCA`), kode referensi VA, dan artefak page-break (`TANGGAL :dd/mm`).
- Modul `anomaly_detector.py` sendiri sempat punya bug serupa (klasifikasi tidak baca satu baris penuh, saldo berjalan tidak reset di batas bulan) yang menyebabkan false-positive besar — sudah diperbaiki dan divalidasi ulang.

Prinsip yang dipegang konsisten: **setiap klaim perbaikan diverifikasi terhadap angka resmi di PDF** (total MUTASI CR/DB tercetak di footer setiap bulan), bukan sekadar "kelihatannya sudah benar".
