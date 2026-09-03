# CLAUDE.md

Panduan ini dibaca otomatis oleh Claude Code setiap kali bekerja di repo ini. Tujuannya: memberi konteks supaya Claude Code tidak menebak-nebak arsitektur, tidak merusak hal yang sudah stabil, dan tahu batasan proyek ini.

---

## Tentang proyek

**XR-App (eXtract-Report)** — aplikasi Flask yang menerima upload PDF rekening koran, mengekstrak mutasi & saldo, lalu menghasilkan laporan Excel 9-sheet (termasuk deteksi 13 indikator kejanggalan rekening).

Status saat ini: **fokus stabilisasi extractor BCA**. Extractor BNI dan Mandiri sudah ada kodenya tapi **sengaja dinonaktifkan** di `extractors/registry.py` — jangan aktifkan kembali kecuali diminta eksplisit.

Detail lengkap arsitektur & fitur ada di `ARCHITECTURE.md` dan `PRD.md`. Baca keduanya sebelum melakukan perubahan struktural.

---

## Aturan kerja yang WAJIB dipatuhi

1. **Jangan pernah klaim "selesai" hanya karena aplikasi jalan atau tidak error di satu PDF sample.**
   Validasi wajib: total MUTASI CR/DB hasil ekstraksi harus dicocokkan dengan angka resmi yang tercetak di footer PDF sumber. Ini adalah standar yang sudah dipegang konsisten sepanjang proyek — jangan diturunkan.

2. **`extractors/` tidak boleh tahu soal Excel/styling. `engine/` tidak boleh tahu soal bank tertentu** — kecuali `anomaly_detector.py`, yang memang sebagian pemeriksaannya spesifik pola teks BCA (didokumentasikan, bukan kebetulan). Jangan langgar batas ini saat menambah fitur.

3. **Kontrak `BaseExtractor` (`extractors/base.py`) itu tetap.** Extractor bank baru wajib mengimplementasikan `extract_saldo()` dan `extract_transaksi()` dengan struktur output yang sama persis dengan extractor BCA. Kalau perlu mengubah kontrak ini, itu perubahan besar — tanyakan dulu, jangan langsung ubah.

4. **`app.py` cuma orkestrasi.** Jangan taruh logic parsing atau styling Excel di `app.py`. Kalau ada logic baru, cari tahu dulu apakah tempatnya di `extractors/` atau `engine/`.

5. **Data rekening koran = PII finansial sensitif.**
   - Jangan pernah log isi transaksi/nomor rekening ke console/file log dalam bentuk plain text yang tidak perlu.
   - File upload harus tetap dibersihkan di `finally` block, sukses maupun gagal — jangan hapus mekanisme ini.
   - Jangan hardcode kredensial/API key apa pun.

6. **Sistem hanya boleh menyatakan "indikasi/anomali/risiko" — tidak pernah menyimpulkan "rekening palsu" secara langsung.** Ini prinsip legal/etis, bukan cuma gaya bahasa. Jangan ubah wording output anomaly detector ke arah kesimpulan pasti.

7. **Jangan diam-diam "membetulkan" data sumber.** Kalau saldo tidak balance atau ada anomali, tandai dan tampilkan — jangan disesuaikan supaya terlihat rapi.

---

## Perintah yang sering dipakai

> Isi bagian ini sesuai kondisi environment kamu (venv, dependency manager, dll) — draft di bawah asumsi umum, sesuaikan dulu sebelum dipakai:

```bash
# Jalankan aplikasi
python app.py

# Install dependency
pip install -r requirements.txt

# (Belum ada test suite otomatis per 2 Sep 2026 — lihat PRD.md §Testing)
```

---

## Saat menambah fitur atau memperbaiki bug

- Cek dulu apakah perubahan menyentuh `extractors/` (khusus 1 bank) atau `engine/` (bank-agnostic) — jangan campur.
- Kalau menyentuh regex nominal/nama pengirim, ingat riwayat bug yang pernah terjadi (lihat `ARCHITECTURE.md §Riwayat Perbaikan`) — kasus serupa (artefak format Eropa, kode channel `/KBB`, `M-BCA`) rawan muncul lagi di pola bank lain.
- Kalau menambah/mengubah salah satu dari 13 indikator kejanggalan, dokumentasikan cara deteksinya di `PRD.md` — jangan cuma di kode.
- Kalau perubahan berpotensi mengubah angka di Sheet 9 (Indikasi Kejanggalan), uji ulang terhadap PDF referensi di `references/` sebelum menyatakan selesai.

## Yang TIDAK perlu dikerjakan tanpa diminta

- Jangan reaktivasi BNI/Mandiri tanpa instruksi eksplisit.
- Jangan mulai migrasi ke FastAPI/Vue kecuali diminta — itu rencana jangka panjang, bukan prioritas saat ini (lihat `ROADMAP.md`).
- Jangan tambahkan auth/database/job queue secara mendadak di tengah task lain — ini perubahan arsitektur besar yang perlu dibahas dulu.

## Known limitations (jangan "perbaiki" diam-diam, laporkan saja)

- Daftar hari libur nasional baru mencakup 4 tanggal tetap — belum ada libur lunar/hijriah.
- Deteksi RTGS bergantung PDF mencetak kata "RTGS" secara eksplisit.
- PDF hasil scan/foto ditolak, belum ada OCR/vision fallback.
- Belum ada auth, database, audit log persisten, atau job queue — single-user/single-page.
