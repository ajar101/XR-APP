# PRD — XR-App (eXtract-Report)

**Status:** Fokus stabilisasi BCA · **Update terakhir:** 2 September 2026

---

## 1. Latar belakang & tujuan

Tim (pusat & cabang) perlu mereview rekening koran nasabah secara manual — proses ini lambat dan rawan human error, terutama untuk deteksi kejanggalan (saldo tidak balance, mutasi hilang, pola transaksi mencurigakan).

**Tujuan produk:** mempercepat proses review rekening koran dengan ekstraksi otomatis + laporan Excel siap pakai, termasuk deteksi dini indikasi kejanggalan yang selama ini bergantung pada ketelitian manual reviewer.

**Tujuan jangka panjang:** dipakai oleh seluruh tim pusat & cabang (bukan cuma single-user), dengan histori dan audit trail.

---

## 2. Target pengguna

- Tim internal (pusat & cabang) yang melakukan review rekening koran nasabah.
- Saat ini: single-user, belum ada role/otorisasi per cabang (lihat §7 Out of Scope).

---

## 3. Masalah yang diselesaikan

| Masalah manual | Solusi XR-App |
|---|---|
| Input ulang data mutasi dari PDF ke Excel secara manual | Ekstraksi otomatis dari PDF teks BCA Giro/Tabungan |
| Cek saldo balance manual per transaksi | Validasi otomatis Saldo Awal + Kredit − Debit = Saldo Akhir |
| Deteksi kejanggalan bergantung pengalaman reviewer | 13 indikator otomatis dengan skor risiko & evidence |
| Rekap kategori pemasukan/pengeluaran manual | Kategorisasi keyword-based otomatis |
| Analisis konsentrasi nasabah manual | HHI Score otomatis + interpretasi |

---

## 4. Fitur & status

| Fitur | Status |
|---|---|
| Ekstraksi PDF teks BCA Giro & Tabungan/Tahapan | ✅ Aktif, tervalidasi 100% cocok total mutasi PDF sumber |
| Upload multi-PDF sekaligus (auto-merge, tanpa merge manual) | ✅ Aktif — maks 6 bulan gabungan |
| Deteksi PDF hasil scan/foto (tolak dengan pesan jelas) | ✅ Aktif |
| Kategorisasi transaksi debit/kredit otomatis | ✅ Aktif |
| Analisis konsentrasi nasabah (HHI Score) | ✅ Aktif |
| Sheet Indikasi Kejanggalan (13 indikator) | ✅ Aktif |
| Bank BNI | ⏸ Nonaktif sementara (kode siap, tinggal diaktifkan di registry) |
| Bank Mandiri (Kopra/E-Banking/Statement) | ⏸ Nonaktif sementara (hanya Kopra yang sempat diimplementasikan) |
| Bank BRI | 🔜 Belum ada extractor |
| OCR / ekstraksi PDF hasil scan | ❌ Belum diimplementasikan |
| Autentikasi & role per cabang | ❌ Belum diimplementasikan |
| Histori/audit log persisten | ❌ Belum diimplementasikan |

---

## 5. Spesifikasi input

- **Format:** PDF rekening koran BCA (Giro & Tabungan/Tahapan), dengan layer teks — bukan hasil scan/foto.
- **Jumlah file:** 1 atau lebih per upload.
- **Total periode:** maksimum 6 bulan mutasi gabungan.
- **Ukuran:** maks 64 MB per request.

### Validasi input (menolak, bukan diam-diam salah)

| Kondisi | Perilaku sistem |
|---|---|
| File bukan `.pdf` | Ditolak, sebutkan nama file |
| PDF terdeteksi hasil scan/foto | Ditolak, pesan jelas + saran unduh ulang PDF asli |
| Nomor rekening beda antar file | Ditolak, sebutkan file & rekening yang beda |
| Bulan sama muncul di >1 file | Ditolak, sebutkan bulan & file yang bentrok |
| Total bulan gabungan > 6 | Ditolak, minta kurangi file |
| Gagal ekstrak saldo (format tak dikenali) | Ditolak per file |

Semua file yang diupload dibersihkan setelah request selesai, apa pun hasilnya (sukses/gagal/exception).

---

## 6. Spesifikasi output

Satu file Excel (`.xlsx`), 9 sheet:

1. **Saldo Harian** — saldo akhir per hari/bulan, rata-rata pengendapan
2. **Detail Transaksi** — seluruh mutasi lengkap
3. **Rekap Kredit** — per pengirim
4. **Rekap Debit** — per penerima
5. **Cashflow Harian** — net cashflow per hari/bulan
6. **Kategori Debit** — auto-klasifikasi pengeluaran
7. **Kategori Kredit** — auto-klasifikasi pemasukan
8. **Summary** — identitas rekening, ringkasan keuangan, HHI Score + interpretasi
9. **Indikasi Kejanggalan** — dashboard skor risiko + tabel detail temuan (filterable), tiap temuan berlevel Tinggi/Sedang/Rendah

### 6.1 13 indikator kejanggalan (Sheet 9)

| # | Indikator | Cara deteksi |
|---|---|---|
| 1 | Saldo Tidak Balance | Saldo Awal + Kredit − Debit ≠ Saldo Akhir |
| 2 | Running Balance Tidak Konsisten | Saldo berjalan per baris ≠ saldo tercetak setelahnya |
| 3 | Mutasi Hilang / Gap Tidak Wajar | Jumlah transaksi ≠ klaim resmi PDF; atau gap ≥5 hari pada rekening aktif |
| 4 | Halaman/Periode Tidak Berurutan | Nomor halaman meloncat / total halaman berubah di periode sama |
| 5 | Duplikasi Transaksi | Tanggal + nominal + deskripsi identik berulang |
| 6 | Format Nominal Tidak Konsisten | Artefak titik-ribuan/koma-desimal tertukar |
| 7 | Template Halaman Berbeda | Header kolom standar hilang di halaman berisi transaksi |
| 8 | Metadata PDF Mencurigakan | Software pembuat/edit tidak lazim, tanggal modifikasi ≠ tanggal buat |
| 9 | Setoran Tunai di Hari Libur | "SETORAN TUNAI" jatuh di Minggu/libur tanggal-tetap |
| 10 | Transaksi RTGS di Hari Libur | "RTGS" jatuh di Minggu/libur (BI-RTGS tidak beroperasi) |
| 11 | Nominal Bulat Berulang | Banyak transaksi ≥Rp50 juta, kelipatan Rp10 juta |
| 12 | Indikasi Structuring | Transaksi tunai berulang mendekati ambang LTKT Rp500 juta |
| 13 | Rasio Pajak Bunga Tidak Wajar | Pajak Bunga ÷ Bunga di luar 0,195–0,205 (PPh Final 20%) |
| 14 | Jadwal Biaya Admin Tidak Wajar | Tanggal debet "BIAYA ADM" tidak sesuai jadwal resmi BCA (beda GIRO/TAHAPAN) |

**Prinsip wajib:** sistem hanya menyatakan indikasi/risiko, tidak pernah menyimpulkan "rekening palsu" tanpa bukti memadai. Setiap temuan harus punya evidence yang bisa ditelusuri ke transaksi sumber.

---

## 7. Out of scope (sengaja, untuk saat ini)

- Autentikasi, otorisasi, isolasi data antar cabang
- Background job queue untuk PDF besar
- Database + audit log histori
- Kebijakan retensi file otomatis
- Deployment aman (VPN/HTTPS+auth) untuk akses cabang
- OCR/vision untuk PDF hasil scan
- Bank selain BCA (BNI/Mandiri nonaktif, BRI belum ada)
- Libur lunar/hijriah dalam deteksi hari libur

Semua ini ada di `ROADMAP.md` — bukan dilupakan, cuma belum jadi prioritas.

---

## 8. Success criteria (definisi "berfungsi dengan baik")

- Total mutasi CR/DB hasil ekstraksi = 100% cocok dengan footer PDF sumber, untuk seluruh sample di `references/`.
- Tidak ada transaksi hilang/duplikat/salah klasifikasi yang tidak terdeteksi validasi.
- Setiap temuan Sheet 9 bisa ditelusuri ke transaksi & halaman sumber.
- Upload yang tidak valid (scan, bank beda, bulan bentrok) ditolak dengan pesan yang jelas — bukan silent fail atau hasil menyesatkan.
- Tidak ada dead button/fitur UI yang mengklaim tersedia tapi belum berfungsi.
