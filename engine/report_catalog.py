"""
report_catalog.py — Katalog isi laporan: sheet apa saja yang dibuat, dan
indikator kejanggalan apa saja yang diperiksa.

Kenapa modul ini ada terpisah dari excel_builder.py: isi laporan perlu
diketahui DUA pihak — engine yang membangun Excel-nya, dan UI yang
menjanjikannya di halaman depan. Selama daftar itu ditulis dua kali
(sekali sebagai urutan pemanggilan builder, sekali lagi sebagai teks HTML),
keduanya pasti berbeda cepat atau lambat: pernah terjadi UI menjanjikan
8 sheet sementara engine sudah menghasilkan 12.

Jadi daftarnya ditaruh di sini sebagai DATA, dan dibaca dua-duanya:

  engine/excel_builder.py  → membangun sheet dengan menelusuri SHEETS
  app.py + templates/      → merender daftar yang sama ke halaman depan

Menambah sheet = menambah satu entri di SHEETS + satu builder di
excel_builder.py. UI ikut berubah sendiri, tidak perlu disentuh.
"""

# ============================================================
# DAFTAR SHEET — urutannya = urutan sheet di dalam file Excel
# ============================================================

# (kunci, judul sheet, deskripsi singkat untuk UI)
#
# `kunci` menghubungkan entri ini dengan builder-nya di excel_builder.py
# (lihat _BUILDER di sana). `judul` HARUS sama persis dengan title sheet
# yang ditulis builder-nya — create_excel memeriksanya sebelum menyimpan
# berkas, jadi ketidakcocokan langsung ketahuan, bukan diam-diam lolos.
#
# Nomor urut sengaja TIDAK ditulis di sini: nomornya adalah posisi dalam
# daftar ini. Nomor yang ditulis tangan akan salah begitu ada sisipan.
SHEETS = [
    ('saldo_harian',         'Saldo Harian',          'Saldo akhir per tanggal'),
    ('detail_transaksi',     'Detail Transaksi',      'Seluruh mutasi'),
    ('rekap_kredit',         'Rekap Kredit',          'Per pengirim'),
    ('rekap_debit',          'Rekap Debit',           'Per penerima'),
    ('summary_rekap_kredit', 'Summary Rekap Kredit',  'Ringkasan per pengirim'),
    ('summary_rekap_debit',  'Summary Rekap Debit',   'Ringkasan per penerima'),
    ('cashflow_harian',      'Cashflow Harian',       'Net per hari'),
    ('kategori_debit',       'Kategori Debit',        'Auto-klasifikasi'),
    ('kategori_kredit',      'Kategori Kredit',       'Auto-klasifikasi'),
    ('daftar_indikator',     'Daftar Indikator',      'Cara deteksi tiap indikasi'),
    ('indikasi_kejanggalan', 'Indikasi Kejanggalan',  'Skor risiko + detail temuan'),
    ('summary',              'Summary',               'Identitas, ringkasan + HHI'),
]

JUDUL_SHEET = [judul for _, judul, _ in SHEETS]

# ============================================================
# DAFTAR INDIKATOR KEJANGGALAN
# ============================================================

# Deskripsi tiap pemeriksaan di engine/anomaly_detector.py, supaya tim tahu
# apa saja yang diperiksa dan bagaimana cara mendeteksinya — bukan cuma
# melihat hasil temuannya. Urutannya mengikuti urutan pemanggilan di
# detect_anomalies(). Kolom "Sumber" membedakan pemeriksaan atas data hasil
# ekstraksi dari pemeriksaan atas jejak cetak dokumen.
#
# Dipakai dua kali: dirender sebagai sheet "Daftar Indikator" di dalam Excel,
# dan dihitung jumlahnya oleh halaman depan. Jadi jumlah indikator yang
# dijanjikan UI tidak pernah bisa berbeda dari yang benar-benar didaftar.
DAFTAR_INDIKATOR = [
    ('Saldo Tidak Balance', 'Tinggi', 'Data ekstraksi',
     'Saldo awal + total kredit − total debit tidak sama dengan saldo akhir bulan.',
     'Dihitung per bulan dan dibandingkan dengan saldo akhir harian terakhir, '
     'dengan toleransi pembulatan Rp100.'),

    ('Duplikasi Transaksi', 'Sedang', 'Data ekstraksi',
     'Beberapa baris transaksi identik dalam satu bulan.',
     'Dikelompokkan atas tanggal, jenis mutasi, nominal, dan keterangan yang sama persis. '
     'Jumlah pengulangan TIDAK menaikkan tingkat indikasi — transaksi rutin memang wajar '
     'berulang identik (setoran per shift, pembayaran per unit), jadi temuan ini selalu '
     'perlu dicek konteksnya, bukan langsung dianggap janggal.'),

    ('Mutasi Hilang / Gap Tidak Wajar', 'Sedang', 'Data ekstraksi',
     'Ada rentang hari tanpa transaksi sama sekali.',
     'Minimal 5 hari beruntun kosong pada bulan yang punya ≥30 transaksi. Gap belum tentu '
     'janggal: libur panjang, rekening musiman, atau pola bisnis tertentu bisa '
     'menjelaskannya — bandingkan dengan pola bulan lain sebelum menyimpulkan.'),

    ('Setoran Tunai di Hari Libur', 'Tinggi', 'Data ekstraksi',
     'Setoran tunai bertanggal Minggu atau libur nasional.',
     'Baris yang keterangannya memuat "SETORAN TUNAI" dicek terhadap hari Minggu '
     'dan daftar libur nasional tanggal tetap.'),

    ('Transaksi RTGS di Hari Libur', 'Tinggi', 'Data ekstraksi',
     'Transaksi RTGS bertanggal Minggu atau libur nasional.',
     'Indikasinya lebih kuat dari setoran tunai: sistem BI-RTGS tidak beroperasi di '
     'luar hari kerja bank, sedangkan setoran tunai lewat CDM bisa 24/7. '
     'Hanya baris yang keterangannya eksplisit memuat "RTGS".'),

    ('Nominal Bulat Berulang', 'Rendah', 'Data ekstraksi',
     'Banyak transaksi bernilai sangat bulat.',
     'Minimal 5 transaksi ≥Rp50 juta yang merupakan kelipatan Rp10 juta dalam satu bulan.'),

    ('Indikasi Structuring', 'Sedang', 'Data ekstraksi',
     'Transaksi tunai yang nilainya mendekati batas pelaporan.',
     'Transaksi berketerangan "TUNAI" bernilai Rp400 juta sampai di bawah Rp500 juta, '
     'dikelompokkan per tanggal.'),

    ('Rasio Pajak Bunga Tidak Wajar', 'Rendah / Sedang', 'Data ekstraksi',
     'Pajak bunga tidak sebanding dengan bunga yang diterima.',
     'PPh Final atas bunga tabungan/giro umumnya 20%, jadi rasio Pajak Bunga terhadap '
     'Bunga seharusnya mendekati 0,20. Hanya baris berketerangan persis "BUNGA" / '
     '"PAJAK BUNGA" yang dihitung.'),

    ('Jadwal Biaya Admin Tidak Wajar', 'Sedang', 'Data ekstraksi',
     'Tanggal pendebetan biaya administrasi tidak sesuai jadwal resmi bank ybs.',
     'Jadwalnya seluruhnya datang dari extractor lewat metadata _biaya_admin, jadi '
     'engine tidak menebak apa pun — BCA: Giro akhir bulan / Tahapan Jumat ke-3, '
     'seragam tanggal 1 sejak Juni 2026; Mandiri: akhir bulan; BNI: hari terakhir '
     'periode laporan; BRI: tanggal 20 untuk keluarga BritAma. Jadwal Giro & Simpedes '
     'BRI sengaja TIDAK dikirim karena belum terbukti di data referensi, sehingga '
     'pemeriksaannya dilewati — bukan ditebak. Baris dicocokkan PERSIS lewat kolom '
     'Nama, jadi biaya lain yang namanya mirip (mis. biaya administrasi kartu debit '
     'Mandiri, yang ikut tanggal ulang tahun kartu) tidak ikut diperiksa.'),

    ('Selisih dengan Ringkasan PDF', 'Tinggi', 'Data ekstraksi + PDF mentah',
     'Jumlah transaksi atau total nominal hasil ekstraksi tidak sama dengan angka '
     'ringkasan yang tercetak di PDF itu sendiri.',
     'Dicocokkan terhadap angka resmi di footer/blok ringkasan bila PDF mencantumkannya: '
     'jumlah transaksi debit & kredit, total nominal debit & kredit, dan saldo akhir. '
     'Jumlah dan nominal dicek terpisah — baris yang hilang bisa terkompensasi jumlahnya '
     'oleh baris ganda, sehingga hanya selisih nominal yang menangkapnya. '
     'PDF yang tidak mencantumkan ringkasan tidak bisa diperiksa dengan cara ini.'),

    ('Peringatan Pembacaan Dokumen', 'Tinggi / Sedang / Rendah', 'Data ekstraksi',
     'Hal yang diketahui extractor saat membaca PDF dan perlu dilihat pemeriksa, '
     'tapi bukan soal kecocokan angka.',
     'Berbeda dari "Selisih dengan Ringkasan PDF" yang membandingkan ANGKA: '
     'indikator ini soal KONDISI DOKUMEN — halaman yang bukan bagian rekening '
     'yang diperiksa (mis. PDF rekening lain ikut ter-merge jadi satu berkas), '
     'rentang tanggal yang tidak dicakup laporan mana pun, rantai saldo berjalan '
     'yang putus, periode yang saling tumpang tindih, atau baris yang tanggalnya '
     'tidak terbaca. Isinya datang dari extractor bank yang bersangkutan, jadi '
     'bank yang extractor-nya belum mengirim peringatan tidak akan memunculkan '
     'temuan di sini — ketiadaan temuan BUKAN berarti dokumennya bersih.'),

    ('Urutan Tanggal Tidak Wajar', 'Tinggi', 'Data ekstraksi',
     'Tanggal transaksi mundur dari baris sebelumnya.',
     'Rekening koran dicetak kronologis, jadi urutan seperti 01, 02, 03, 01, 04 — atau '
     'transaksi tanggal 15 muncul setelah tanggal 20 — bisa menandakan baris disisipkan '
     'atau dokumen disusun ulang. Beberapa transaksi di tanggal yang sama tidak dihitung '
     'sebagai pelanggaran urutan.'),

    ('Running Balance Tidak Konsisten', 'Tinggi', 'Jejak cetak dokumen',
     'Saldo berjalan antar baris di dokumen tidak menyambung.',
     'Saldo tiap baris dihitung ulang dari saldo tercetak sebelumnya ditambah mutasi '
     'di antaranya, dengan toleransi Rp5, dan di-reset tiap ganti blok laporan. '
     'Angkanya diserahkan extractor lewat metadata _provenance — kini dikirim oleh '
     'SELURUH extractor (BCA, ketiga format Mandiri, kedua format BNI, dan BRI), jadi '
     'pemeriksaan ini berlaku untuk semua bank yang didukung.'),

    ('Halaman/Periode Tidak Berurutan', 'Sedang / Tinggi', 'Jejak cetak dokumen',
     'Nomor halaman meloncat, atau total halaman berubah di tengah satu laporan.',
     'Nomor halaman dalam satu blok laporan harus naik satu per satu. Hanya berlaku '
     'untuk dokumen yang MENCETAK nomor halaman: BCA ("HALAMAN : 2 /42"), Mandiri '
     'Kopra ("Page 2 of 4"), BNI Account Statement, dan BRI. Rekening Koran & '
     'e-Statement Mandiri serta BNI Transaction Inquiry tidak mencetaknya sama sekali, '
     'jadi untuk ketiganya pemeriksaan ini dilewati — bukan berarti lolos.'),

    ('Template Halaman Berbeda', 'Sedang', 'Jejak cetak dokumen',
     'Halaman berisi transaksi tapi header kolom standar tidak ditemukan.',
     'Bisa berarti halaman disisipkan dari sumber lain atau tata letaknya diubah. '
     'Hanya berlaku untuk format yang memang mengulang header kolom di SETIAP halaman '
     '(BCA, Mandiri Kopra & e-Statement, kedua format BNI, dan BRI). Rekening Koran '
     'Mandiri mencetaknya sekali di awal tiap laporan, jadi ketiadaannya di halaman '
     'lanjutan wajar dan tidak diperiksa.'),

    ('Format Nominal Tidak Konsisten', 'Sedang', 'Jejak cetak dokumen',
     'Ada baris memakai format angka yang berbeda dari sisa dokumen.',
     'Mendeteksi campuran format ribuan/desimal gaya Eropa di antara baris berformat '
     'standar — pola yang lazim muncul pada dokumen yang diedit. Hanya diperiksa pada '
     'baris yang extractor-nya nyatakan berisi teks CETAK MESIN. Keterangan yang '
     'memuat berita bebas dari nasabah sengaja dikecualikan: nasabah lazim menulis '
     'nominal gaya Indonesia di berita transfer ("19.655.050"), dan itu bukan artefak '
     'dokumen. Saat ini yang memenuhi syarat baru BCA.'),

    ('Metadata PDF', 'Rendah / Sedang', 'PDF mentah',
     'Informasi pembuat, aplikasi, dan waktu pembuatan/modifikasi berkas.',
     'Selalu ditampilkan apa adanya untuk direview manual. Ditandai mencurigakan bila '
     'aplikasi pembuatnya bukan sistem perbankan, atau waktu modifikasi berbeda dari '
     'waktu pembuatan.'),
]

CATATAN_INDIKATOR = [
    'Sistem hanya menyatakan INDIKASI yang perlu diperiksa manusia — bukan kesimpulan '
    'bahwa dokumen dipalsukan. Satu temuan tidak berarti dokumen bermasalah, dan tidak '
    'adanya temuan tidak menjamin dokumen asli.',
    'Daftar libur nasional yang dipakai baru mencakup 4 tanggal tetap '
    '(1 Januari, 1 Mei, 17 Agustus, 25 Desember) ditambah hari Minggu. Libur yang '
    'mengikuti kalender lunar/hijriah dan cuti bersama BELUM tercakup, sehingga '
    'transaksi di hari-hari itu tidak akan tertandai.',
    'Pemeriksaan "Jadwal Biaya Admin Tidak Wajar" memakai jadwal resmi masing-masing '
    'bank yang dikirim extractor-nya. Jenis rekening yang jadwalnya belum terbukti di '
    'data referensi (Giro & Simpedes BRI) sengaja dilewati, jadi ketiadaan temuan di '
    'situ BUKAN berarti jadwalnya sudah benar.',
    'Pemeriksaan berbasis PDF mentah dijalankan per berkas. Pada upload beberapa PDF, '
    'nama berkas dicantumkan di kolom halaman supaya temuan bisa dilacak.',
    'Empat pemeriksaan bersumber "Jejak cetak dokumen" (Running Balance, '
    'Halaman/Periode, Template Halaman, Format Nominal) memakai fakta yang '
    'diserahkan extractor lewat metadata _provenance — bukan pembacaan ulang PDF. '
    'Cakupannya karena itu mengikuti apa yang memang dicetak dokumennya: nomor '
    'halaman, header kolom per halaman, dan saldo berjalan tidak selalu ada di semua '
    'format. Bagian yang dilewati BUKAN berarti dokumennya bersih — kolom Sumber dan '
    'penjelasan tiap indikator menyebutkan syaratnya.',
    'Pemeriksaan "Urutan Tanggal Tidak Wajar" dan "Selisih dengan Ringkasan PDF" hanya '
    'berjalan bila extractor bank tersebut mempertahankan urutan cetak dan membaca angka '
    'ringkasan PDF. Keduanya kini tersedia untuk SELURUH format yang didukung: BCA, '
    'Mandiri (Kopra, e-Statement, Rekening Koran), BNI (Account Statement, Transaction '
    'Inquiry), dan BRI (Laporan Transaksi Finansial).',
    '"Peringatan Pembacaan Dokumen" kini dikirim oleh ketiga extractor Mandiri, kedua '
    'extractor BNI, dan BRI. Extractor BCA belum mengirimnya, jadi untuk rekening BCA '
    'bagian ini akan selalu kosong — itu keterbatasan cakupan, bukan pernyataan bahwa '
    'dokumennya bersih.',
]
