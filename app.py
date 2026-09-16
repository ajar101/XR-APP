"""
app.py — XR-App Flask entrypoint.

Bertanggung jawab hanya untuk:
  1. Merender halaman muka (templates/index.html) dengan pilihan bank
  2. Menerima upload PDF
  3. Memanggil extractor yang tepat berdasarkan pilihan bank
  4. Memanggil excel_builder untuk generate output
  5. Mengirimkan file Excel ke user

Tidak ada logika parsing PDF atau styling Excel di sini — dan sejak
halamannya pindah, tidak ada HTML/CSS/JS juga: semuanya di templates/ dan
static/. Angka yang dijanjikan halaman muka (jumlah sheet, jumlah indikator,
jumlah bank & format, batas upload) dihitung di route-nya dari sumber
aslinya, tidak diketik ulang di dalam HTML.
"""

import io
import os
import secrets
from flask import Flask, render_template, request, send_file
from flask_login import current_user, login_required

import auth

from extractors.registry import get_enabled_banks, get_extractor, get_formats
from extractors.pdf_utils import is_probably_scanned
from engine.excel_builder import create_excel
from engine.multi_pdf_merger import merge_extractions, MergeValidationError, MAX_BULAN
from engine.report_catalog import SHEETS, DAFTAR_INDIKATOR

VERSI = '2.1'

# DEBUG: Print enabled banks
print("\n" + "="*60)
print("ENABLED BANKS:")
print("="*60)
for code, info in get_enabled_banks().items():
    print(f"  ✓ {code:12s} - {info['name']}")
print("="*60 + "\n")

app = Flask(__name__)

# Kunci penanda tangan cookie sesi. Tanpa kunci yang rahasia dan TETAP,
# siapa pun bisa membuat cookie sesi palsu dan masuk sebagai pengguna mana
# pun — jadi kuncinya tidak boleh punya nilai bawaan yang bisa ditebak.
#
# Kunci acak yang dibuat saat proses mulai juga tidak memadai di produksi:
# tiap worker gunicorn akan punya kunci berbeda sehingga sesi pemakai putus
# bergantian, dan semua orang ter-logout tiap kali aplikasi di-restart.
# Karena itu di produksi ketiadaan XR_SECRET_KEY membuat aplikasi MENOLAK
# jalan, bukan diam-diam memakai kunci sementara.
_kunci = os.environ.get('XR_SECRET_KEY')
if not _kunci:
    if os.environ.get('XR_DEBUG') == '1':
        _kunci = secrets.token_hex(32)
        print('⚠️  XR_SECRET_KEY tidak disetel — memakai kunci sementara '
              '(hanya boleh untuk pengembangan).')
    else:
        raise RuntimeError(
            'XR_SECRET_KEY belum disetel. Buat sekali dengan:\n'
            "    python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            'lalu simpan sebagai variabel lingkungan. Lihat DEPLOY.md.'
        )
app.secret_key = _kunci

# Cookie sesi: tidak bisa dibaca JavaScript, tidak ikut terkirim ke situs
# lain, dan hanya lewat HTTPS kecuali sedang dikembangkan di mesin sendiri.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('XR_DEBUG') != '1',
)

# PDF yang diunggah harus mendarat di disk karena extractor membacanya
# lewat pdfplumber (butuh path), tapi selalu dihapus lagi di blok `finally`
# di bawah. Berkas Excel hasilnya TIDAK pernah menyentuh disk sama sekali —
# lihat catatan di dekat create_excel().
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER']      = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # dinaikkan dari 16MB — mendukung upload multi-PDF sekaligus


# ============================================================
# FLASK ROUTES
# ============================================================

auth.pasang(app)


@app.context_processor
def nilai_bersama():
    """Nilai yang dipakai semua template (lihat templates/dasar.html)."""
    return {'versi': VERSI}


@app.route('/')
@login_required
def index():
    # Semua angka yang dijanjikan halaman depan dihitung di sini dari sumber
    # aslinya — katalog laporan, registry bank, dan konfigurasi app — bukan
    # diketik ulang di dalam HTML. Halaman ini pernah menjanjikan 8 sheet
    # selama engine sudah menghasilkan 12, dan tidak menyebut sheet Indikasi
    # Kejanggalan sama sekali, karena daftarnya disalin dengan tangan.
    banks = get_enabled_banks()
    return render_template(
        'index.html',
        banks=banks,
        sheets=[(judul, deskripsi) for _, judul, deskripsi in SHEETS],
        jumlah_indikator=len(DAFTAR_INDIKATOR),
        jumlah_format=len(get_formats()),
        maks_bulan=MAX_BULAN,
        maks_mb=app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024),
        versi=VERSI,
    )


@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    bank_code = request.form.get('bank_code', '').strip()

    def tolak(pesan, kode=400, berkas=None):
        """
        Tolak permintaan sambil mencatatnya di jejak audit.

        Semua jalan keluar yang gagal lewat sini supaya tidak ada kegagalan
        yang hilang dari jejak — termasuk penolakan validasi, yang justru
        paling perlu terlihat (PDF hasil scan, rekening beda, bulan bentrok).
        """
        auth.catat_audit('ekstraksi', 'gagal', bank=bank_code or None,
                         jumlah_berkas=len(berkas) if berkas else None,
                         nama_berkas=', '.join(b.filename for b in berkas)
                                     if berkas else None,
                         keterangan=pesan[:300], app=app)
        return pesan, kode

    if not bank_code:
        return tolak('Pilih bank terlebih dahulu.')

    # Mendukung upload beberapa PDF sekaligus (mis. tiap file 1-3 bulan,
    # tidak perlu di-merge manual dulu jadi satu PDF) — lihat
    # engine/multi_pdf_merger.py untuk aturan validasinya (rekening harus
    # sama, bulan tidak boleh bentrok, total maks 6 bulan).
    files = [f for f in request.files.getlist('file') if f.filename]
    if not files:
        return tolak('Tidak ada file yang dipilih.')
    for f in files:
        if not f.filename.lower().endswith('.pdf'):
            return tolak(f"File '{f.filename}' harus berformat PDF.", berkas=files)

    try:
        ExtractorClass = get_extractor(bank_code)
    except ValueError as e:
        return tolak(str(e), berkas=files)

    saved_paths = []
    try:
        per_file_results = []
        first_extractor = None
        laporan_checksum = []

        for idx, f in enumerate(files):
            # Prefix indeks supaya nama file yang sama dari beberapa upload
            # tidak saling menimpa di UPLOAD_FOLDER.
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'{idx}_{f.filename}')
            f.save(filepath)
            saved_paths.append(filepath)

            # Cek dulu sebelum ekstraksi (yang bisa makan puluhan detik untuk
            # PDF ratusan halaman) — PDF hasil scan/foto tidak punya layer
            # teks sama sekali, jadi extractor apa pun pasti gagal. Lebih
            # baik gagal cepat dengan pesan jelas daripada nunggu extractor
            # jalan penuh lalu gagal dengan pesan generik.
            if is_probably_scanned(filepath):
                return tolak(
                    f"File '{f.filename}' terdeteksi sebagai PDF hasil scan/foto (gambar), "
                    f"bukan PDF teks asli dari sistem bank. Ekstraksi otomatis saat ini hanya "
                    f"mendukung PDF rekening koran yang diunduh langsung dari internet "
                    f"banking/e-statement resmi (bukan hasil scan atau foto kamera). Silakan "
                    f"unduh ulang PDF aslinya, atau hubungi bank untuk mendapatkan e-statement.",
                    berkas=files)

            try:
                extractor = ExtractorClass(filepath)
            except NotImplementedError as e:
                # Format terdeteksi tapi extractor-nya memang belum ada.
                # Ini kondisi yang WAJAR, bukan kerusakan — sampaikan apa
                # adanya (400), jangan jatuh ke handler 500 di bawah yang
                # hanya menampilkan pesan teknis generik.
                return tolak(f"File '{f.filename}': {e}", berkas=files)
            if first_extractor is None:
                first_extractor = extractor

            # Checksum dikumpulkan per file — kalau hanya extractor terakhir
            # yang diperiksa, file lain lolos tanpa validasi sama sekali.
            if hasattr(extractor, 'validate'):
                laporan_checksum.append((f.filename, extractor.validate()))

            saldo = extractor.extract_saldo()
            # Cek keberadaan data BULAN, bukan sekadar dict tidak kosong.
            # Extractor mengembalikan metadata ('_nama_pemilik', dst) walau
            # tidak satu pun transaksi terbaca, sehingga `if not saldo` selalu
            # lolos dan kegagalan baru meledak jauh di hilir sebagai HTTP 500
            # generik. Bank-agnostik: berlaku untuk semua extractor.
            if not any(not k.startswith('_') for k in saldo):
                return tolak(
                    f"Tidak ada satu pun periode transaksi yang bisa dibaca dari "
                    f"'{f.filename}'. PDF-nya terbaca, tapi tata letaknya tidak "
                    f"dikenali oleh extractor {bank_code.upper()} — kemungkinan "
                    f"format/varian yang belum didukung. Pastikan file ini memang "
                    f"rekening koran {bank_code.upper()} yang diunduh langsung dari "
                    f"layanan resmi banknya.",
                    berkas=files)
            trans = extractor.extract_transaksi()
            per_file_results.append((f.filename, saldo, trans))

        try:
            saldo_per_bulan, transaksi_per_bulan = merge_extractions(per_file_results)
        except MergeValidationError as e:
            return tolak(str(e), berkas=files)

        no_rekening = saldo_per_bulan.get('_no_rekening') or 'unknown'
        file_prefix = first_extractor.get_file_prefix()
        nama_file   = f'{file_prefix}_{no_rekening}.xlsx'

        # Checksum internal extractor: cocokkan hasil parsing dengan angka
        # resmi yang tercetak di PDF. Hanya nama pemeriksaan & jumlah baris
        # yang dicatat — nominal & isi transaksi sengaja tidak ikut di-log.
        for nama_sumber, laporan in laporan_checksum:
            if not laporan.get('ok', True):
                gagal = sorted({
                    k for per in laporan.get('periods', [])
                    for k, v in per.get('checks', {}).items() if v is False
                })
                app.logger.warning(
                    'Checksum %s / %s TIDAK COCOK dengan ringkasan resmi PDF '
                    '(pemeriksaan gagal: %s). Angka pada laporan perlu diperiksa manual.',
                    file_prefix, nama_sumber, ', '.join(gagal) or 'tidak diketahui',
                )
            # Periode yang tumpang tindih TIDAK menggagalkan checksum, jadi
            # tanpa baris ini penggabungan duplikat terjadi tanpa diketahui
            # siapa pun — persis yang harus dihindari pada data sumber.
            digabung = laporan.get('duplikat_digabung') or 0
            if digabung:
                app.logger.warning(
                    '%s / %s memuat periode yang tumpang tindih: %d transaksi ganda '
                    'digabung menjadi satu. Total mutasi laporan lebih kecil dari '
                    'penjumlahan mentah tiap periode — ini disengaja.',
                    file_prefix, nama_sumber, digabung,
                )

        # Laporan dibangun di MEMORI, tidak ditulis ke disk.
        #
        # Sebelumnya tiap laporan disimpan ke folder exports/ dan tidak pernah
        # dihapus — komentarnya menyebut itu disengaja karena send_file() masih
        # perlu membacanya. Akibatnya folder itu menumpuk tanpa batas, dan tiap
        # berkasnya memuat nama pemilik, nomor rekening, serta SELURUH mutasi
        # rekening seseorang. Data sesensitif itu tidak boleh tertinggal di
        # server tanpa jadwal hapus, dan tidak ada route mana pun yang
        # menyajikan ulang isi exports/, jadi berkasnya memang tidak pernah
        # dibutuhkan lagi setelah terunduh.
        #
        # Membangunnya di BytesIO menghapus persoalannya, bukan mengelolanya:
        # tidak ada berkas yang perlu dijadwalkan hapus karena tidak ada
        # berkas yang dibuat. Kalau kelak ekstraksi dipindah ke background job
        # queue, hasilnya HARUS tersimpan di suatu tempat (worker dan
        # pengunduh jadi proses berbeda) — dan saat itulah kebijakan retensi
        # dengan TTL benar-benar diperlukan.
        keluaran = io.BytesIO()
        create_excel(
            saldo_per_bulan,
            transaksi_per_bulan,
            keluaran,
            bank_name=file_prefix,
            pdf_path=saved_paths,
        )
        keluaran.seek(0)

        auth.catat_audit('ekstraksi', 'berhasil', bank=bank_code,
                         jumlah_berkas=len(files),
                         nama_berkas=', '.join(f.filename for f in files),
                         no_rekening=no_rekening, app=app)

        return send_file(
            keluaran,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nama_file,
        )

    except Exception as e:
        # Give some time for file handles to close (Windows fix)
        import time
        time.sleep(0.1)
        app.logger.exception('Ekstraksi %s gagal', bank_code)
        # Pesan teknisnya masuk jejak audit & log, TIDAK ke layar pemakai:
        # isi exception bisa memuat potongan data dokumen.
        auth.catat_audit('ekstraksi', 'gagal', bank=bank_code,
                         jumlah_berkas=len(files),
                         nama_berkas=', '.join(f.filename for f in files),
                         keterangan=f'{type(e).__name__}: {e}'[:300], app=app)
        return ('Terjadi kesalahan saat memproses berkas. Kejadian ini sudah '
                'dicatat — hubungi admin bila berulang.'), 500
    finally:
        # Bersihkan PDF yang diupload apa pun hasil akhirnya — sukses,
        # exception, ATAU return dini karena validasi gagal (mis. terdeteksi
        # scan, bulan bentrok). Berkas Excel hasilnya tidak perlu dibersihkan
        # karena tidak pernah ditulis ke disk.
        for p in saved_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


if __name__ == '__main__':
    # Blok ini HANYA untuk menjalankan aplikasi di mesin sendiri saat
    # mengembangkan. Untuk dipakai orang lain, jalankan lewat gunicorn —
    # lihat wsgi.py dan DEPLOY.md. Server bawaan Werkzeug bukan server
    # produksi, dan itu pernyataan pembuatnya sendiri, bukan pendapat.
    #
    # debug MATI secara bawaan. Sebelumnya di sini tertulis debug=True
    # bersama host='0.0.0.0', dan itu berbahaya justru untuk aplikasi ini:
    # begitu ada error, Werkzeug menampilkan halaman traceback berisi
    # potongan kode sumber DAN isi variabel lokal di tiap bingkai. Pada
    # aplikasi ini variabel lokal itu berisi nama pemilik rekening, nomor
    # rekening, dan baris-baris mutasinya. Debugger-nya memang terkunci PIN
    # pada Werkzeug versi sekarang, tapi halaman tracebacknya sendiri tidak.
    #
    # Dinyalakan hanya kalau diminta EKSPLISIT lewat XR_DEBUG=1, dan saat
    # itu pun hanya mengikat ke localhost supaya tidak terjangkau dari
    # jaringan.
    debug = os.environ.get('XR_DEBUG') == '1'
    host = '127.0.0.1' if debug else os.environ.get('XR_HOST', '127.0.0.1')
    port = int(os.environ.get('XR_PORT', '5000'))

    print("\n" + "=" * 50)
    print(f"🚀 XR-App · eXtract-Report v{VERSI}")
    print("=" * 50)
    print(f"\n📍 Akses aplikasi di: http://{host}:{port}")
    print(f"\n🏦 Bank tersedia: {', '.join(get_enabled_banks().keys())}")
    if debug:
        print("\n⚠️  MODE DEBUG AKTIF — halaman error akan menampilkan isi")
        print("    variabel, termasuk data rekening. Jangan dipakai untuk")
        print("    melayani orang lain. Hanya mengikat ke localhost.")
    else:
        print("\n💡 Untuk melayani pemakai lain, jangan pakai server ini —")
        print("   jalankan lewat gunicorn (lihat DEPLOY.md).")
    print("\n⏹️  Tekan CTRL+C untuk stop server\n")
    app.run(debug=debug, host=host, port=port)
