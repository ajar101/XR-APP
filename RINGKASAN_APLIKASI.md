# XR-App — eXtract-Report

Ringkasan arsitektur, fitur, input/output, dan rencana pengembangan aplikasi ekstraktor rekening koran.

> Dibuat: 2 September 2026 · Diperbarui: 12 September 2026 · Status: BCA, Mandiri (Kopra + e-Statement + Rekening Koran), dan BNI (Account Statement + Transaction Inquiry) aktif

---

## 1. Apa aplikasi ini

Aplikasi web (Flask) yang menerima upload PDF rekening koran, mengekstrak seluruh mutasi & saldo secara otomatis, lalu menghasilkan laporan Excel multi-sheet — lengkap dengan kategorisasi transaksi, analisis cashflow, konsentrasi nasabah (HHI Score), dan **deteksi otomatis indikasi kejanggalan rekening** (17 indikator, dari saldo tidak balance sampai jadwal biaya admin yang tidak sesuai ketentuan bank).

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
│   ├── bca.py                   #   Extractor BCA (Giro & Tahapan)
│   ├── bni.py                   #   Dispatcher format BNI (auto-detect)
│   ├── bni_statement.py         #   Sub-extractor BNI ACCOUNT STATEMENT (tabel bergaris)
│   ├── bni_inquiry.py           #   Sub-extractor BNI TRANSACTION INQUIRY (tabel bergaris)
│   ├── bni_nama.py              #   Pipeline nama lawan transaksi, dipakai bersama kedua format BNI
│   ├── mandiri.py               #   Dispatcher format Mandiri (auto-detect)
│   ├── mandiri_kopra.py         #   Sub-extractor Mandiri Kopra
│   ├── mandiri_statement.py     #   Sub-extractor Mandiri e-Statement (Livin'/Mandiri Online)
│   ├── mandiri_koran.py         #   Sub-extractor Mandiri Laporan Rekening Koran (tabel bergaris)
│   ├── mandiri_nama.py          #   Pipeline nama lawan transaksi, dipakai bersama Kopra & Rekening Koran
│   ├── peringatan.py            #   Pencatatan peringatan pembacaan dokumen (bank-agnostic)
│   └── pdf_utils.py             #   Deteksi PDF hasil scan/foto (bank-agnostic)
├── engine/                      # Lapisan pemrosesan (bank-agnostic)
│   ├── excel_builder.py         #   Generator Excel 9-sheet
│   ├── categorizer.py           #   Kategorisasi transaksi berbasis keyword
│   ├── anomaly_detector.py      #   17 pemeriksaan indikasi kejanggalan
│   └── multi_pdf_merger.py      #   Gabungkan hasil ekstraksi dari beberapa PDF
├── tests/                       # Tes regresi ekstraksi
│   ├── regresi.py               #   Bandingkan hasil ekstraksi + temuan Sheet 9 dengan snapshot
│   └── snapshot/                #   Hasil yang direkam, satu JSON per PDF (1 transaksi/temuan = 1 baris)
├── references/                  # PDF contoh + hasil Excel untuk validasi manual
└── parse_rekening.py            # Skrip CLI lama, tidak terhubung ke app.py (peninggalan awal)
```

**Total kode inti: ±4.900 baris Python** (per 2 Sep 2026): `app.py` 695 baris, extractor BCA 565 baris, `anomaly_detector.py` 825 baris, `excel_builder.py` 1.193 baris.

### 2.2 Prinsip desain kunci

- **Kontrak `BaseExtractor` yang ketat** (`extractors/base.py`): setiap extractor bank baru wajib mengimplementasikan `extract_saldo()` dan `extract_transaksi()` dengan struktur output yang sama persis, supaya `engine/` (Excel builder, kategorisasi, deteksi anomali) bisa bekerja **tanpa modifikasi apa pun**, apa pun bank-nya. Menambah bank baru = buat 1 file extractor + daftarkan di `registry.py`.
- **Pemisahan tegas parsing vs presentasi**: `extractors/` tidak tahu soal Excel/styling; `engine/` tidak tahu soal bank tertentu.
- **Ketentuan bank dimiliki extractor-nya, bukan engine.** Aturan yang berbeda antar bank (jadwal pendebetan biaya admin, cara mengenali baris bunga/pajak) dikirim extractor sebagai *metadata* lewat `extract_saldo()` — lihat kontraknya di `extractors/base.py`. Engine hanya membaca strukturnya. Extractor yang tidak mengirim metadata itu membuat pemeriksaan terkait **dilewati**, bukan ditebak dengan aturan bank lain.
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
| Sheet "Indikasi Kejanggalan" (17 indikator deteksi anomali) | ✅ Aktif (lihat §4) |
| Bank BNI — format **ACCOUNT STATEMENT** (giro/CURRENT) | ✅ Aktif — divalidasi 100% terhadap 7 blok laporan dari 4 PDF riil (jumlah & total mutasi Debet/Kredit dan saldo akhir, dicocokkan dengan kaki ringkasan tiap blok) |
| Bank BNI — format **TRANSACTION INQUIRY** (hasil query BNI Direct) | ✅ Aktif — divalidasi terhadap 12 blok inquiry dari 8 PDF riil; 11 blok cocok 100% (Total Debit, Total Credit, saldo akhir), 1 blok **sengaja tidak cocok** karena PDF sumbernya memang tidak konsisten (lihat §7.1) |
| Bank BNI — format lain | ❌ Belum ada extractor — ditolak dengan pesan yang menyebut format yang didukung |
| Bank Mandiri — format **Kopra by Mandiri** | ✅ Aktif — divalidasi checksum terhadap ringkasan resmi PDF |
| Bank Mandiri — format **e-Statement** (Livin'/Mandiri Online) | ✅ Aktif — Tabungan, Tabungan Bisnis, Tabungan NOW & Giro; divalidasi 100% terhadap 14 periode dari 5 PDF riil |
| Bank Mandiri — format **Laporan Rekening Koran** (Account Statement Report) | ✅ Aktif — divalidasi 100% terhadap 13 periode laporan dari 9 PDF riil (jumlah & total mutasi, saldo akhir, plus rantai saldo berjalan per baris) |
| Bank Mandiri — format **E-Banking** | ❌ Belum ada extractor — ditolak dengan pesan yang menyebut formatnya |
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
| Format terdeteksi tapi extractor-nya belum ada (mis. Mandiri E-Banking) | Ditolak 400, menyebut format apa yang terdeteksi |
| PDF terbaca tapi tidak satu pun periode transaksi dikenali | Ditolak 400, per file — bukan HTTP 500 generik |

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
| 9 | **Indikasi Kejanggalan** | 17 pemeriksaan otomatis kewajaran rekening — dashboard skor risiko + tabel detail temuan (filterable) |

### 5.1 Sheet 9 — 17 indikator kejanggalan

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
| 13 | Rasio Pajak Bunga Tidak Wajar | Pajak Bunga ÷ Bunga di luar rentang 0,195–0,205 (PPh Final 20%). Baris bunga & pajak dikenali lewat metadata `_bunga_pajak` (BCA lewat kolom Keterangan, Mandiri lewat kolom Nama) |
| 14 | Jadwal Biaya Admin Tidak Wajar | Tanggal debet biaya admin tidak sesuai jadwal resmi bank ybs. Jadwalnya dikirim extractor lewat metadata `_biaya_admin` — BCA: GIRO akhir bulan / TAHAPAN Jumat ke-3, seragam tanggal 1 sejak Juni 2026; Mandiri: akhir bulan. Baris dicocokkan **persis** lewat kolom Nama, jadi "Biaya administrasi kartu debit" Mandiri (ikut tanggal ulang tahun kartu) tidak ikut diperiksa |
| 15 | Selisih dengan Ringkasan PDF | Jumlah transaksi & total nominal hasil ekstraksi ≠ angka ringkasan resmi yang tercetak di PDF itu sendiri. Dikirim extractor lewat metadata `_checksum` |
| 16 | Urutan Tanggal Tidak Wajar | Tanggal transaksi mundur dari baris sebelumnya — rekening koran dicetak kronologis, jadi urutan 01, 02, 03, 01, 04 menandakan baris disisipkan atau dokumen disusun ulang |
| 17 | Peringatan Pembacaan Dokumen | Kondisi dokumen yang hanya diketahui extractor saat membaca PDF: halaman yang bukan bagian rekening yang diperiksa (mis. PDF rekening lain ikut ter-merge), rentang tanggal yang tidak dicakup laporan mana pun, rantai saldo berjalan yang putus, periode tumpang tindih, baris bertanggal tidak terbaca. Dikirim extractor lewat metadata `_peringatan` — saat ini oleh ketiga extractor Mandiri; BCA belum, jadi untuk BCA bagian ini selalu kosong |

> Catatan jujur soal keterbatasan: daftar hari libur nasional baru mencakup 4 tanggal tetap (Tahun Baru, Buruh, Kemerdekaan, Natal) — **belum** mencakup libur lunar/hijriah (Lebaran, Nyepi, Imlek, dst). Deteksi RTGS bergantung PDF mencetak kata "RTGS" secara eksplisit.

---

## 6. Rencana implementasi ke depan

Disusun berdasarkan diskusi sepanjang pengembangan, urut prioritas realistis (bukan urut "keren"):

### 6.1 Jangka pendek — masih di arsitektur Flask saat ini
- **Nama lawan transaksi pada BNI e-channel.** Untuk transfer keluar lewat e-channel, dokumen BNI memang tidak mencetak nama penerima sama sekali — yang ada hanya nomor rekening tujuan, dan nomor itulah yang dipakai sebagai identitas di kolom Nama. Untuk transfer masuk, nama pengirim dicetak menyatu dengan berita transaksi tanpa pemisah apa pun (tidak ada gap kolom — sudah diperiksa sampai ke koordinat glif), jadi pemisahannya bertumpu pada bentuk huruf: nama dicetak sistem dalam huruf besar, berita diketik nasabah. Berita yang kebetulan ditulis huruf besar semua masih ikut terbawa. Sebagian baris e-channel juga tidak memuat nama sama sekali, hanya kode terminal/agen yang berganti tiap transaksi (`S1ACIR9510 4095`) — kode semacam itu dikenali dan digantikan nomor rekening lawan, supaya satu pengirim tidak pecah jadi puluhan baris di Rekap. Label kanal yang ikut tercetak di ekor nama ("… BI FAST") dibuang, dari daftar label yang benar-benar terlihat di PDF referensi saja. Butuh lebih banyak sampel sebelum aturannya diperketat.
- **Sisa nama lawan transaksi (Mandiri: 32 baris dari 6.892 = 0,46%)** — ekor panjang yang tiap polanya hanya muncul 1–2 kali, jadi aturan untuknya akan lahir dari terlalu sedikit contoh. Dibiarkan apa adanya sampai ada lebih banyak sampel. Keterangan lengkapnya tetap ada di Sheet Detail Transaksi.
- **Nama pada PDF BCA (18 baris)** — pipeline namanya terpisah (`extractors/bca.py`, bukan `mandiri_nama.py`). Kelompok terbesarnya top-up Flazz (16 baris) yang namanya masih berupa nomor kartu. Belum digarap.
- **Pemeriksaan Sheet 9 yang masih spesifik pola teks BCA** — empat indikator yang membaca ulang PDF mentah (running balance, nomor halaman, template halaman, format nominal) plus pencocokan "mutasi hilang" mencocokkan tata letak khas BCA (`HALAMAN :`, baris `dd/mm`, `MUTASI CR :`). Sejak dipasang gerbang `BANK_POLA_MENTAH`, kelimanya **hanya dijalankan untuk rekening BCA**.

  Gerbang itu dipasang setelah terbukti bukan sekadar "cakupan lebih sempit": pada PDF Mandiri yang ikut memuat halaman rekening BCA milik rekening lain, pemindai membaca ringkasan BCA itu lalu membandingkannya dengan data Mandiri, dan menghasilkan **empat temuan "RISIKO TINGGI" yang seluruhnya keliru**. Untuk bank selain BCA, pemeriksaan setara sudah dilakukan extractor-nya sendiri lewat checksum internal (`validate()` → metadata `_checksum`) plus nomor urut transaksi dan rantai saldo.

  **Rencana (belum dikerjakan):** hapus parser kedua itu sepenuhnya. Extractor sudah tahu setiap fakta yang dibutuhkan saat parsing (baris ini di halaman berapa, saldo tercetak di sebelahnya, nomor halaman, ada tidaknya header kolom) lalu membuangnya; serahkan sebagai metadata `_provenance` — bentuknya *fakta*, bukan *pola regex per bank*, karena mengirim pola berarti melembagakan parser kedua yang justru jadi sumber temuan palsu tadi. Setelah itu keempat pemeriksaan bisa bank-agnostik, dan `_check_mutasi_hilang` bisa dihapus karena pekerjaannya sudah dilakukan `_checksum` dengan benar. Kuncinya **bank + format** (Kopra/Rekening Koran/e-Statement/BCA), bukan bank + jenis rekening — jenis rekening baru relevan untuk aturan berjadwal seperti `_biaya_admin`.
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

### 7.1 Catatan format BNI TRANSACTION INQUIRY

Format kedua BNI ini (hasil query di BNI Direct, bukan e-statement bulanan) punya empat sifat yang menentukan cara membacanya — rinciannya ada di docstring `extractors/bni_inquiry.py`:

1. **Tabelnya bergaris dan baris header-nya menyebut nama kolomnya.** Batas baris & kolom diambil dari kotak sel yang dicetak dokumen, dengan baris header sebagai jangkar letak tabel. Jangkar itu bukan hiasan: di atas tabel ada blok "Account Information" yang juga digambar sebagai kotak-kotak dengan lebar kolom yang berbeda, dan pada halaman yang transaksinya sedikit kotak blok itu lebih banyak daripada kotak tabelnya.
2. **Kolom Amount dicetak bersih** (tidak seperti ACCOUNT STATEMENT yang nominalnya rusak karena efek tebal), jadi nominal diambil dari sana dan kolom Balance dipakai sebagai **pemeriksanya**. Rantai saldo itu diserahkan ke engine lewat `_provenance`, sehingga baris yang nominal dan saldonya tidak sejalan muncul sebagai temuan — bukan diam-diam diperbaiki extractor.
3. **Satu PDF bisa memuat beberapa hasil inquiry, dan urutannya tidak kronologis** (satu PDF referensi memuat blok Desember 2024, lalu Februari 2025, baru Januari 2025 — wajar, tiap blok adalah query terpisah yang digabung). Karena itu sambungan antar periode diperiksa setelah blok **diurutkan kronologis**; kalau diperiksa menurut urutan cetak, tiap berkas seperti itu akan melaporkan "ada rentang tanggal yang tidak dicakup" yang sebenarnya tidak ada.
4. **Penomoran baris ("No.") berurut dari 1 di tiap blok** — jaminan kelengkapan yang tidak dimiliki format BNI lain. Satu baris yang dihapus dari dokumen meninggalkan lubang di penomorannya, dan itu terlihat bahkan ketika seluruh angka ringkasannya ikut disesuaikan. Pemeriksaannya ada di `validate()`.

Dokumen ini tidak mencetak nomor halaman dan tidak mencetak Ending Balance. Saldo akhir yang diharapkan karena itu dihitung dari tiga angka yang memang tercetak di kepala tiap blok (Beginning Balance − Total Debit + Total Credit) lalu dibandingkan dengan saldo baris terakhir — tetap angka dokumen, bukan angka kita sendiri.

**Satu PDF referensi (`BNI_inquiry (1).pdf`) sengaja dibiarkan gagal checksum.** Dokumen itu memang tidak konsisten dengan dirinya sendiri: nominal biaya admin tercetak `25,0000.00` (bukan `25,000.00`), satu tanggal tercetak `05/01/206`, total debit resminya meleset Rp225.000 dari jumlah baris yang tercetak, dan saldo akhirnya meleset Rp1.000.000. Metadata PDF-nya menyebut *airSlate* — perangkat penyunting PDF, bukan pencetak rekening. Semua itu **muncul sebagai temuan di Sheet 9**, persis seperti yang diharapkan dari dokumen yang disunting. Baris yang tanggalnya tidak terbaca tidak ditebak tanggalnya: ia tidak masuk Detail Transaksi (tidak ada bulan yang bisa jadi tempatnya) tapi tetap ikut dihitung di checksum, sehingga selisihnya terlihat di dua tempat sekaligus.

> Catatan untuk pengembangan berikutnya: `SOFTWARE_EDITOR_MENCURIGAKAN` di `engine/anomaly_detector.py` belum memuat "airSlate", sehingga PDF di atas hanya masuk kategori "Metadata PDF" (Rendah), bukan "Metadata PDF Mencurigakan" (Sedang). Temuan numeriknya sudah cukup untuk menandai dokumen itu, tapi daftar produsen PDF layak ditambah.

**Pipeline nama lawan transaksi dipakai bersama.** Kolom Description format ini bertata bahasa sama dengan ACCOUNT STATEMENT (segmen dipisah `|`, klausa `PEMINDAHAN KE <rekening> <nama>`, kode cabang 3 digit di depan nama pengirim antarbank), jadi aturannya dipindahkan ke `extractors/bni_nama.py` dan dipakai kedua extractor — setara `mandiri_nama.py` untuk keluarga dokumen Mandiri. Pemindahan itu diverifikasi bebas-regresi terhadap snapshot 4 PDF ACCOUNT STATEMENT yang sudah ada.

**Semua PDF referensi BNI Transaction Inquiry masuk tes regresi** (`tests/regresi.py`); awalan `BNI_` didaftarkan di `AWALAN_BANK` supaya berkasnya tidak dilewati diam-diam.
