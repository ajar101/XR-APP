# ARCHITECTURE.md — XR-App

**Update terakhir:** 2 September 2026 · Total kode inti: ±4.900 baris Python

---

## 1. Struktur folder

```
XR-APP/
├── app.py                       # Flask entrypoint — UI, routing upload, orkestrasi (695 baris)
├── extractors/                  # Lapisan parsing PDF (spesifik per bank)
│   ├── base.py                  #   Kontrak abstrak BaseExtractor
│   ├── registry.py              #   Daftar bank & status aktif/nonaktif
│   ├── bca.py                   #   Extractor BCA — satu-satunya aktif (565 baris)
│   ├── bni.py                   #   Extractor BNI — nonaktif sementara
│   ├── mandiri.py                #   Dispatcher format Mandiri — nonaktif sementara
│   ├── mandiri_kopra.py         #   Sub-extractor Mandiri Kopra — nonaktif sementara
│   └── pdf_utils.py             #   Deteksi PDF hasil scan/foto (bank-agnostic)
├── engine/                      # Lapisan pemrosesan (bank-agnostic)
│   ├── excel_builder.py         #   Generator Excel 9-sheet (1.193 baris)
│   ├── categorizer.py           #   Kategorisasi transaksi berbasis keyword
│   ├── anomaly_detector.py      #   13 pemeriksaan indikasi kejanggalan (825 baris)
│   └── multi_pdf_merger.py      #   Gabungkan hasil ekstraksi dari beberapa PDF
├── references/                  # PDF contoh + hasil Excel untuk validasi manual
└── parse_rekening.py            # Skrip CLI lama, tidak terhubung ke app.py (peninggalan awal)
```

---

## 2. Prinsip desain kunci

1. **Kontrak `BaseExtractor` yang ketat.** Setiap extractor bank wajib mengimplementasikan `extract_saldo()` dan `extract_transaksi()` dengan struktur output yang sama persis, supaya `engine/` bisa bekerja **tanpa modifikasi** apa pun bank-nya. Menambah bank baru = 1 file extractor baru + daftar di `registry.py`.

2. **Pemisahan tegas parsing vs presentasi.**
   - `extractors/` tidak tahu soal Excel/styling.
   - `engine/` tidak tahu soal bank tertentu — **kecuali** `anomaly_detector.py`, karena sebagian pemeriksaan (mis. pola "BIAYA ADM", format teks BCA) memang spesifik teks BCA. Ini pengecualian yang disadari, bukan kebocoran arsitektur.

3. **`app.py` cuma orkestrasi** — terima upload → panggil extractor sesuai bank → panggil `excel_builder` → kirim file. Tidak ada logic parsing/styling di sini.

4. **Validasi terhadap data riil, bukan asumsi.** Setiap perbaikan bug/fitur baru diverifikasi ulang terhadap total MUTASI CR/DB resmi di footer PDF, sebelum dianggap selesai.

---

## 3. Alur request

```
User upload 1-N PDF (bank + file)
        │
        ▼
app.py /upload
  ├─ Validasi ekstensi .pdf & bank dipilih
  ├─ pdf_utils.is_probably_scanned() → tolak kalau PDF hasil scan/foto
  ├─ Extractor per file → extract_saldo() + extract_transaksi()
  ├─ multi_pdf_merger.merge_extractions()
  │     → tolak kalau: rekening beda antar file / bulan bentrok / total > 6 bulan
  ├─ excel_builder.create_excel()
  │     ├─ Sheet 1-8: data keuangan
  │     └─ Sheet 9: anomaly_detector.detect_anomalies()
  └─ Kirim file .xlsx ke user, bersihkan file upload (finally-block)
```

---

## 4. Kontrak `BaseExtractor`

Setiap extractor bank harus mengembalikan struktur data yang identik lewat dua method:

- `extract_saldo()` — saldo awal, saldo akhir, identitas rekening, periode.
- `extract_transaksi()` — daftar mutasi: tanggal, jenis (debit/kredit), nominal, nama pengirim/penerima, keterangan, nomor halaman sumber (untuk traceability).

Karena kontrak ini seragam, `engine/` (Excel builder, kategorisasi, anomaly detector) tidak perlu tahu bank apa yang sedang diproses — kecuali beberapa pemeriksaan anomaly yang memang eksplisit spesifik BCA.

**Implikasi penting:** kalau kontrak ini diubah, semua extractor (termasuk yang nonaktif: BNI, Mandiri) perlu disesuaikan. Ini perubahan besar — bukan hal yang dilakukan sambil lalu.

---

## 5. Riwayat perbaikan signifikan (untuk konteks debugging)

Sebagian besar waktu pengembangan dihabiskan memperbaiki **akurasi ekstraksi** berdasarkan pengujian PDF riil:

- Klasifikasi Debit/Kredit sempat salah baca nama nasabah ("DBS", "M-BCA") sebagai penanda transaksi.
- Regex nominal sempat salah tangkap pecahan angka pada baris dengan artefak format Eropa (titik ribuan/koma desimal) — pernah menyebabkan selisih ~Rp301 juta dalam satu bulan sebelum diperbaiki.
- Nama pengirim/penerima sempat kepotong kode channel (`/KBB`, `M-BCA`, `MyBCA`), kode referensi VA, dan artefak page-break (`TANGGAL :dd/mm`).
- `anomaly_detector.py` sendiri sempat punya bug (klasifikasi tidak baca satu baris penuh, saldo berjalan tidak reset di batas bulan) yang menyebabkan false-positive besar — sudah diperbaiki dan divalidasi ulang.

**Pola bug yang rawan berulang saat menambah bank baru:** artefak format angka lokal, kode channel yang menempel di nama, page-break yang memotong baris transaksi. Kalau mengaktifkan kembali BNI/Mandiri, cek dulu apakah pola-pola ini muncul dalam bentuk berbeda di format bank tersebut.

---

## 6. Rencana migrasi arsitektur (jangka panjang, lihat `ROADMAP.md`)

Rekomendasi: **FastAPI (backend) + Vue 3/TypeScript (frontend)**, dilakukan *setelah* kebutuhan jangka menengah (auth, job queue, database) tuntas secara konsep — bukan sebagai gerbang masuk.

Alasan desain saat ini mendukung migrasi ini tanpa investasi hangus:
- `extractors/` dan `engine/` sudah 100% terpisah dari Flask — portable tanpa perubahan.
- FastAPI native mendukung async/background task + validasi Pydantic — lebih rapi untuk auth & job queue.
- Vue 3 SPA membuka peluang render Sheet 9 langsung di browser untuk triase cepat, histori per cabang, role-based access.
