# ROADMAP.md — XR-App

Urut berdasarkan prioritas realistis (bukan urut "keren"). Jangan lompat ke fase berikutnya sebelum fase sebelumnya cukup matang.

---

## Fase 1 — Jangka pendek (masih di arsitektur Flask saat ini)

- [ ] **Reaktivasi BNI & Mandiri** — setelah pola BCA (regex, deteksi nama, dsb.) dianggap cukup matang untuk dijadikan acuan pola bank lain. Cek `ARCHITECTURE.md §5` untuk pola bug yang rawan berulang.
- [ ] **Perluas daftar hari libur nasional** — termasuk libur lunar/hijriah (Lebaran, Nyepi, Imlek, dst). Perlu referensi kalender resmi per tahun.
- [ ] **OCR / Claude Vision untuk PDF hasil scan** — saat ini hanya terdeteksi & ditolak. Rekomendasi: langsung ke vision model (Claude API) ketimbang OCR tradisional + regex, karena data finansial butuh akurasi tinggi dan OCR rentan salah baca digit pada tabel rapat. *Belum digarap — dinilai jarang terjadi untuk saat ini, bukan prioritas mendesak.*

---

## Fase 2 — Jangka menengah (untuk pemakaian tim, pusat & cabang)

**Ini lebih mendesak daripada migrasi framework**, karena aplikasi saat ini masih single-user/single-page tanpa histori.

1. [ ] **Autentikasi & otorisasi** — siapa boleh upload, isolasi data antar cabang.
2. [ ] **Background job queue** (Celery/RQ/arq) — ekstraksi PDF besar (296 halaman ⇒ 60–90 detik) saat ini blocking request; tidak scalable untuk banyak user bersamaan.
3. [ ] **Database + audit log** — riwayat upload, hasil ekstraksi, dan terutama histori temuan Sheet 9 (nilainya justru di riwayat, bukan cuma unduhan sekali pakai).
4. [ ] **Kebijakan retensi & keamanan file** — PDF/Excel yang diupload harus punya jadwal hapus otomatis (data rekening koran = PII finansial sensitif).
5. [ ] **Topologi deployment aman** — VPN atau HTTPS+auth kuat untuk akses cabang, bukan exposed langsung ke internet.

---

## Fase 3 — Jangka panjang (migrasi arsitektur, opsional & bertahap)

**Rekomendasi: FastAPI (backend) + Vue 3/TypeScript (frontend)** — dilakukan setelah Fase 2 tuntas secara konsep, bukan sebagai gerbang masuk.

- Fase 3a: bungkus engine yang ada dengan FastAPI + job queue + auth dasar.
- Fase 3b: bangun SPA Vue 3 dengan histori & tampilan indikasi kejanggalan interaktif (bukan cuma Excel).
- Fase 3c: role-based access pusat/cabang, kemungkinan integrasi SSO korporat.

`extractors/` dan `engine/` sudah portable tanpa perubahan — investasi parsing yang ada tidak hangus saat migrasi ini terjadi.

---

## Prinsip lintas-fase

- Jangan mulai fase berikutnya sambil fase sebelumnya masih ada isu terbuka yang signifikan.
- Setiap penambahan bank baru wajib divalidasi terhadap total MUTASI CR/DB resmi PDF sebelum dianggap selesai (lihat `PRD.md §8`).
- Known limitations yang belum digarap harus tetap dinyatakan eksplisit di `CLAUDE.md`/`PRD.md`, bukan didiamkan sampai lupa.
