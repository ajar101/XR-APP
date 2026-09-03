# DOD.md — Definition of Done (XR-App)

**Update terakhir:** 2 September 2026 · Status proyek: fokus stabilisasi BCA

Dokumen ini dipakai untuk menilai apakah suatu perubahan/fitur benar-benar "selesai" — bukan cuma "jalan tanpa error". Dipecah per fase sesuai `ROADMAP.md`, supaya standar yang dituntut realistis dengan tahap proyek saat ini.

**Aturan dasar untuk Claude Code:** jangan pernah menyatakan sesuatu "DONE"/"PRODUCTION READY" hanya karena aplikasi berhasil dijalankan, UI terlihat, atau tidak ada error di satu PDF sample. Kalau ada limitation/uncertainty, nyatakan eksplisit — jangan disembunyikan supaya terlihat selesai.

---

## A. DoD untuk perubahan di extractor BCA (fase saat ini — paling sering dipakai)

Berlaku setiap kali menyentuh `extractors/bca.py`, `engine/categorizer.py`, `engine/anomaly_detector.py`, atau `engine/excel_builder.py`.

- [ ] Total MUTASI CR/DB hasil ekstraksi **cocok 100%** dengan angka resmi di footer PDF sumber, untuk seluruh sample relevan di `references/` — bukan cuma 1 sample.
- [ ] Tidak ada transaksi hilang/terduplikasi/salah klasifikasi dibanding PDF sumber (cek manual side-by-side, minimal untuk sample yang terdampak perubahan).
- [ ] Kalau perubahan menyentuh regex nominal/nama — cek dulu apakah pola lama yang pernah jadi bug (artefak format Eropa, kode channel `/KBB`, `M-BCA`, `MyBCA`, artefak page-break) masih tertangani dengan benar. Regresi di sini pernah menyebabkan selisih ~Rp301 juta — jangan anggap remeh.
- [ ] Kalau perubahan menyentuh salah satu dari 13 indikator kejanggalan: hasil deteksi diuji ulang terhadap sample yang memang mengandung & tidak mengandung kondisi tersebut (cek false positive & false negative dasar).
- [ ] Saldo berjalan tidak "reset" salah di batas bulan (bug lama yang pernah terjadi di `anomaly_detector.py`) — cek ulang kalau logic ini tersentuh.
- [ ] `PRD.md` §6.1 diperbarui kalau ada perubahan cara deteksi salah satu indikator.

**FAIL kalau:** ada selisih angka terhadap footer PDF yang tidak dijelaskan, atau perubahan "kelihatannya benar" tapi belum diuji terhadap sample referensi.

---

## B. DoD untuk validasi & error handling input

- [ ] Semua kondisi penolakan di `PRD.md §5` (bukan PDF, hasil scan, rekening beda, bulan bentrok, >6 bulan, gagal ekstrak saldo) tetap menghasilkan pesan jelas — bukan error generik atau silent fail.
- [ ] File upload tetap dibersihkan di `finally` block untuk semua jalur: sukses, gagal validasi, exception tak terduga.
- [ ] Tidak ada data rekening/transaksi yang ter-log ke console/file dalam bentuk plain text yang tidak perlu.
- [ ] Error tidak menyebabkan aplikasi crash atau menghasilkan file Excel yang terlihat valid padahal datanya salah/kosong.

---

## C. DoD untuk menambah/mengaktifkan bank baru (BNI, Mandiri, BRI)

Selain poin A & B (yang tetap berlaku), tambahan khusus:

- [ ] `extract_saldo()` dan `extract_transaksi()` mengembalikan struktur output **identik** dengan kontrak `BaseExtractor` yang dipakai BCA — cek `extractors/base.py`.
- [ ] Extractor tidak menyentuh apa pun di `engine/` — kalau ternyata perlu, itu tanda kontrak `BaseExtractor` kurang lengkap, bukan alasan untuk bikin pengecualian bank-spesifik di `engine/`.
- [ ] Total MUTASI CR/DB tervalidasi 100% terhadap footer PDF sumber bank tersebut (bukan asumsi format mirip BCA = pasti benar).
- [ ] Dicek eksplisit apakah pola bug lama (artefak format angka lokal, kode channel yang menempel di nama, page-break yang memotong baris) muncul dalam bentuk berbeda di format bank ini.
- [ ] `PRD.md §4` dan `registry.py` diperbarui status banknya.
- [ ] Bank baru **tidak** diaktifkan default di registry sampai eksplisit dinyatakan siap — nonaktif adalah default yang aman.
- [ ] **Checksum PASS di sample referensi ≠ otomatis boleh diaktifkan.** Kalau jumlah PDF referensi kecil (mis. 5 file), itu rawan overfit — terutama untuk sub-pola yang belum sepenuhnya terkaidahkan (mis. kategori "lain-lain" pada ekstraksi nama). Standar prosesnya: kerjakan & verifikasi di branch → laporkan hasil (angka akurasi, checksum, contoh output) → `enabled: True` di-toggle manual oleh pemilik proyek setelah review, bukan bagian otomatis dari task perbaikan.

**FAIL kalau:** extractor "kelihatan jalan" tapi belum ada satu pun sample yang divalidasi 100% terhadap footer PDF resmi.

---

## D. DoD untuk fitur UI

- [ ] Tidak ada tombol/fitur yang terlihat di UI tapi belum berfungsi (dead button).
- [ ] Status proses (upload, parsing, error, selesai) terlihat jelas oleh user.
- [ ] Pesan error/validasi (§B) tertampil dengan jelas ke user, bukan cuma di log server.

---

## E. Yang BELUM jadi syarat "done" saat ini (sengaja ditunda, lihat `ROADMAP.md`)

Jangan tolak PR/perubahan hanya karena poin-poin ini belum ada — ini bukan skip permanen, cuma belum relevan di fase saat ini:

- Unit test / integration test otomatis, golden dataset formal, regression test terjadwal (belum ada test suite per 2 Sep 2026)
- Autentikasi, otorisasi, isolasi data antar cabang
- Database, audit log persisten, histori upload
- Background job queue untuk PDF besar
- Kebijakan retensi file otomatis
- Deployment aman (VPN/HTTPS+auth) untuk akses cabang
- OCR/vision untuk PDF hasil scan
- Libur lunar/hijriah dalam deteksi hari libur (baru 4 tanggal tetap)

**Catatan penting:** kalau salah satu dari ini mulai disentuh (misalnya menambah auth), pindah acuan ke DoD yang lebih ketat — jangan pakai standar bagian A-D di atas untuk perubahan sebesar itu. Tanyakan dulu sebelum mulai, karena ini perubahan arsitektur, bukan iterasi kecil.

---

## F. Definisi "DONE" untuk fase saat ini (Fase 1 — stabilisasi BCA)

Suatu perubahan bisa dinyatakan **DONE** apabila:

- Bagian A = PASS (untuk perubahan di parsing/anomaly/excel)
- Bagian B = PASS (untuk perubahan di validasi/error handling)
- Bagian D = PASS (untuk perubahan UI)
- `references/` diperbarui kalau ada sample baru yang dipakai untuk validasi
- Limitation yang tersisa dinyatakan eksplisit di `CLAUDE.md`/`PRD.md`, bukan didiamkan

Kalau salah satu bagian relevan = FAIL → **status = NOT DONE**, dan itu harus dinyatakan eksplisit, bukan dibungkus jadi "sudah lumayan" atau "sudah bisa dipakai".
