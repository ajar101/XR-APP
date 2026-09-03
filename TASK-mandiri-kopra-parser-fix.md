# TASK — Perbaikan Parser Mandiri Kopra

**Branch:** `claude/mandiri-kopra-parser-review-1q04ra`
**Berdasarkan:** Laporan Pemeriksaan Parser Mandiri Kopra (audit, belum ada perubahan kode)
**Acuan standar:** `DOD.md §C` (DoD untuk menambah/mengaktifkan bank baru)
**Status saat ini:** BCA selesai & stabil — Mandiri Kopra sekarang jadi fokus utama.

---

## 0. Baseline & konteks

- Akurasi saat ini: **41%** transaksi tertangkap (204/496), label jenis mutasi salah untuk sebagian besar baris, saldo ≥1 miliar terbaca 0, ekstraksi nama gagal 60%.
- Sudah ada prototipe pembanding (di luar repo, di scratchpad sesi audit) yang mencapai **100%** kecocokan terhadap ringkasan resmi (No. of Debit/Credit, Total Amount Debited/Credited) di kelima PDF referensi. Prototipe ini jadi acuan pendekatan, bukan langsung di-copy — logic-nya perlu diintegrasikan ke `mandiri_kopra.py` sesuai kontrak `BaseExtractor`.
- 5 PDF referensi yang dipakai audit: `1200010763543`, `1200011620205`, `1290012867301`, `1460019302319`, `1630003882837` (di `/references`).

---

## 1. Scope perbaikan

### 1.1 Deteksi kolom dinamis (akar masalah utama)
- [ ] Ganti batas koordinat hardcode (`418`, `458`, `503`, dst.) dengan deteksi posisi header per halaman: `Remark`, `Reference No.`, `Debit`, `Credit`, `Balance`.
- [ ] Untuk 3 kolom angka yang rata-kanan (Debit, Credit, Balance), gunakan **x1** sebagai patokan — bukan x0. Ini yang menyebabkan saldo ≥1 miliar hilang (selisih 1,3pt di x0 vs batas hardcode).
- [ ] Kolom Remark tidak boleh memotong kata pertama (bug lama: mulai dari x0=150 padahal remark mulai di ~119, sehingga kata pertama nama hilang).
- [ ] Reference No. tidak boleh bocor ke kolom Remark.

### 1.2 Pengelompokan baris (row clustering)
- [ ] Potong klaster baris di **titik tengah antar-anchor tanggal** (bukan `anchor['y'] - 3`), supaya baris remark multi-baris tidak salah nempel ke transaksi sebelumnya.
- [ ] Mulai deteksi bidang tabel dari **bawah baris header**, supaya baris periode (`01 Jun 2025 - 30 Jun 2025 IDR KCP Jkt...`) tidak ikut terbaca sebagai transaksi hantu tanggal 1.

### 1.3 Ekstraksi nama — tulis ulang sebagai pipeline berurutan
- [ ] Urutan pengecekan pola (bukan keyword generik di awal):
  1. `MCM InhouseTrf KE/DARI <NAMA> [Transfer Fee]` — ambil nama antara `KE`/`DARI` dan `Transfer Fee`/angka referensi (40% dari data, pola terbesar)
  2. `<TGL><KODEBANK>IDJA... <KODEBANK>IDJA/<NAMA> <angka>` — ambil setelah `IDJA/` sampai sebelum digit panjang (15%)
  3. `UBP<kode> <angka>` — biller/pembayaran tagihan (5%)
  4. `MCM Outw CN <NAMA> Clearing Fee` — ambil setelah `CN`
  5. Kategori tetap: `Clearing Fee`, `MONTHLY CARD CHARGE`, `Tarik Tunai`, `DARI <rek> KE <rek> Sweep`
  6. Kategori biaya admin (`ADM`, `FEE`, `RTGS`, `BUNGA`, `PAJAK`, `CHRG`, `MATERAI`) — **dipindah ke paling akhir**, dan `Transfer Fee`/`Clearing Fee` diperlakukan sebagai penanda **batas akhir nama**, bukan keyword biaya. Ini bug utama yang menelan 183/496 (36%) transaksi InhouseTrf menjadi "Biaya Admin".
- [ ] Hapus pemotongan nama 4 kata (`" ".join(nama.split()[:4])`) — penyebab "PT" dan suku kata belakang terpotong.
- [ ] Sisa 33% pola "lain-lain" (ATM, fee PG, dll.) — dokumentasikan pola yang teridentifikasi selama implementasi; boleh fallback ke nilai mentah remark yang dibersihkan (bukan "-") kalau tidak match pola manapun, supaya tidak menambah entri kosong di rekap.

### 1.4 Bug lain
- [ ] Hapus definisi duplikat `extract_no_rekening` dan `extract_saldo` (baris ~50/72 vs ~283/305) — definisi kedua saat ini diam-diam menimpa yang pertama, sisakan satu versi yang benar dan hapus dead code-nya.
- [ ] Perbaiki `_nama_pemilik`: baca baris **setelah** header "Account No. Account Name Alias", bukan regex yang salah cocok ke header itu sendiri.
- [ ] Pastikan `_saldo_awal_<bulan>` benar-benar dikeluarkan oleh versi `extract_saldo` yang aktif (saat ini hanya versi dead code yang menulis kunci ini, sehingga kolom "Saldo Awal" di Excel selalu kosong).
- [ ] Perbaiki `_extract_opening_balance`: regex saat ini tidak pernah cocok karena angkanya ada di baris setelah label "Opening Balance ... Total Amount Debited", bukan di baris yang sama.
- [ ] Perbaiki pemilihan bulan: jangan pakai `list(result_map.keys())[-1]` (index terakhir yang dimasukkan) — pakai bulan halaman yang sedang diproses secara eksplisit. Ini "kebetulan jalan" untuk urutan halaman tertentu tapi rapuh untuk urutan lain.
- [ ] Tanggal dari anchor dicocokkan dengan bulan halaman terkait, bukan diasumsikan.

### 1.5 Validasi otomatis (checksum) — WAJIB, bukan opsional
- [ ] Tambahkan pencocokan otomatis hasil parsing terhadap angka resmi yang tercetak di tiap PDF Kopra: **No. of Debit, No. of Credit, Total Amount Debited, Total Amount Credited, Opening Balance, Closing Balance**.
- [ ] Kalau tidak klop, sistem harus memberi warning yang jelas — bukan diam-diam lanjut dengan data yang salah. Ini selaras dengan `DOD.md §A` (validasi terhadap footer PDF) yang sudah jadi standar wajib untuk BCA — sekarang diterapkan ke Kopra.

### 1.6 Kebersihan alur Mandiri
- [ ] Hapus `parse_rekening.py` dari repo (keputusan final, lihat §3.1) — script CLI lama, target format BSI, tidak kompatibel dengan Kopra dan tidak terhubung ke `app.py`.

---

## 2. Kriteria selesai (acceptance criteria)

Mengacu `DOD.md §C` — semua poin berikut harus PASS sebelum dianggap selesai:

- [ ] **Akurasi transaksi:** 496/496 transaksi tertangkap di kelima PDF referensi (atau: 100% cocok dengan jumlah yang tercantum di ringkasan resmi tiap PDF, kalau ada PDF baru yang dipakai untuk uji tambahan).
- [ ] **Label jenis mutasi:** proporsi Debit/Kredit hasil parsing cocok dengan komposisi resmi (296 debit / 200 kredit untuk 5 PDF ini).
- [ ] **Saldo:** tidak ada saldo harian yang jatuh ke 0 secara keliru (khususnya kasus saldo ≥1 miliar). `Saldo Awal` per bulan terisi dan cocok dengan Opening Balance resmi.
- [ ] **Checksum otomatis:** No. of Debit/Credit dan Total Amount Debited/Credited hasil parsing cocok 100% dengan angka resmi di kelima PDF, divalidasi otomatis oleh kode (bukan cek manual).
- [ ] **Ekstraksi nama:** turunkan signifikan dari kondisi saat ini (189 "Biaya Admin" salah label + 107 gagal total = 60% tidak terpakai). Target realistis: pola-pola besar (InhouseTrf 40%, IDJA 15%, UBP 5%, Outw CN, kategori tetap) terklasifikasi benar; sisa "lain-lain" (33%) boleh punya fallback yang masuk akal, tapi tidak boleh salah label sebagai "Biaya Admin".
- [ ] **Tidak ada transaksi hantu:** baris periode header tidak lagi terbaca sebagai transaksi tanggal 1 dengan saldo 0.
- [ ] **Kontrak `BaseExtractor` tetap dipatuhi** — output `extract_saldo()` dan `extract_transaksi()` sama strukturnya dengan extractor BCA (lihat `ARCHITECTURE.md §4`).
- [ ] **`extractors/` tetap tidak menyentuh `engine/`** — perbaikan ini murni di lapisan parsing, tidak butuh perubahan di `excel_builder.py`/`categorizer.py`/`anomaly_detector.py`. Kalau ternyata perlu, itu sinyal untuk didiskusikan dulu, bukan langsung dikerjakan.
- [ ] Method duplikat, `_nama_pemilik` kosong, `_saldo_awal_<bulan>` hilang, pemilihan bulan tidak aman — semua sudah diperbaiki dan diverifikasi lewat kelima PDF referensi.

**FAIL kalau:** ada satu pun dari 5 PDF referensi yang tidak mencapai kecocokan 100% pada checksum resmi, atau perbaikan "kelihatan benar" tapi belum diverifikasi otomatis.

---

## 3. Keputusan (sudah di-approve, laporan audit disetujui)

1. **`parse_rekening.py`** → **HAPUS** dari repo. Sudah dinyatakan eksplisit di laporan bahwa script ini tidak kompatibel dengan Kopra (target format BSI, tidak punya garis tabel) dan tidak terhubung ke `app.py`. Kalau suatu saat perlu direferensikan, tersedia di git history.

2. **Aktivasi di `registry.py`** → **JANGAN diaktifkan otomatis**, meskipun kriteria §2 sudah PASS di kelima PDF referensi. Alasan: 5 sample rawan overfit, khususnya untuk pola "lain-lain" yang belum terkaidahkan (lihat poin 3). Alur yang benar:
   - Selesaikan perbaikan §1, verifikasi kriteria §2 PASS di branch.
   - **Laporkan hasilnya** (angka akurasi, checksum, contoh output) — jangan langsung ubah `enabled` di `registry.py`.
   - Toggle `enabled: True` dilakukan manual setelah ada review, bukan bagian otomatis dari task ini.

3. **Sisa pola "lain-lain" (33%)** → **tidak perlu dikaidahkan habis di task ini**. Cukup fallback aman: remark yang sudah dibersihkan (hasil §1.1–§1.2) dipakai apa adanya sebagai nama — bukan "Biaya Admin" (salah label) dan bukan "-" (buang informasi). Dokumentasikan sebagai known limitation untuk task lanjutan kalau nanti data riil menunjukkan pola yang sering berulang di kategori ini.

---

## 4. Setelah selesai

- [ ] Update `PRD.md §4` — status Mandiri Kopra dari "nonaktif sementara" sesuai keputusan §3.2.
- [ ] Update `ARCHITECTURE.md §5` (riwayat perbaikan) — catat pola bug baru yang ditemukan di Kopra untuk referensi kalau nanti mengerjakan format Mandiri lain (E-Banking/Statement) atau BNI/BRI.
- [ ] Update `ROADMAP.md` — centang item "Reaktivasi BNI & Mandiri" sesuai bagian yang selesai (Kopra saja, atau termasuk BNI kalau dikerjakan terpisah).
