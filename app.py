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

import os
from flask import Flask, render_template, request, send_file

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

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
EXPORT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'exports')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER']      = UPLOAD_FOLDER
app.config['EXPORT_FOLDER']      = EXPORT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # dinaikkan dari 16MB — mendukung upload multi-PDF sekaligus


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route('/')
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
def upload_file():
    bank_code = request.form.get('bank_code', '').strip()
    if not bank_code:
        return 'Pilih bank terlebih dahulu.', 400

    # Mendukung upload beberapa PDF sekaligus (mis. tiap file 1-3 bulan,
    # tidak perlu di-merge manual dulu jadi satu PDF) — lihat
    # engine/multi_pdf_merger.py untuk aturan validasinya (rekening harus
    # sama, bulan tidak boleh bentrok, total maks 6 bulan).
    files = [f for f in request.files.getlist('file') if f.filename]
    if not files:
        return 'Tidak ada file yang dipilih.', 400
    for f in files:
        if not f.filename.lower().endswith('.pdf'):
            return f"File '{f.filename}' harus berformat PDF.", 400

    try:
        ExtractorClass = get_extractor(bank_code)
    except ValueError as e:
        return str(e), 400

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
                return (
                    f"File '{f.filename}' terdeteksi sebagai PDF hasil scan/foto (gambar), "
                    f"bukan PDF teks asli dari sistem bank. Ekstraksi otomatis saat ini hanya "
                    f"mendukung PDF rekening koran yang diunduh langsung dari internet "
                    f"banking/e-statement resmi (bukan hasil scan atau foto kamera). Silakan "
                    f"unduh ulang PDF aslinya, atau hubungi bank untuk mendapatkan e-statement."
                ), 400

            try:
                extractor = ExtractorClass(filepath)
            except NotImplementedError as e:
                # Format terdeteksi tapi extractor-nya memang belum ada.
                # Ini kondisi yang WAJAR, bukan kerusakan — sampaikan apa
                # adanya (400), jangan jatuh ke handler 500 di bawah yang
                # hanya menampilkan pesan teknis generik.
                return f"File '{f.filename}': {e}", 400
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
                return (
                    f"Tidak ada satu pun periode transaksi yang bisa dibaca dari "
                    f"'{f.filename}'. PDF-nya terbaca, tapi tata letaknya tidak "
                    f"dikenali oleh extractor {bank_code.upper()} — kemungkinan "
                    f"format/varian yang belum didukung. Pastikan file ini memang "
                    f"rekening koran {bank_code.upper()} yang diunduh langsung dari "
                    f"layanan resmi banknya."
                ), 400
            trans = extractor.extract_transaksi()
            per_file_results.append((f.filename, saldo, trans))

        try:
            saldo_per_bulan, transaksi_per_bulan = merge_extractions(per_file_results)
        except MergeValidationError as e:
            return str(e), 400

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

        output_path = os.path.join(app.config['EXPORT_FOLDER'], nama_file)
        create_excel(
            saldo_per_bulan,
            transaksi_per_bulan,
            output_path,
            bank_name=file_prefix,
            pdf_path=saved_paths,
        )

        return send_file(
            output_path,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nama_file,
        )

    except Exception as e:
        # Give some time for file handles to close (Windows fix)
        import time
        time.sleep(0.1)
        return f'Error: {str(e)}', 500
    finally:
        # Bersihkan PDF yang diupload apa pun hasil akhirnya — sukses,
        # exception, ATAU return dini karena validasi gagal (mis. terdeteksi
        # scan, bulan bentrok). output_path (file xlsx hasil) sengaja tidak
        # ikut dihapus di sini karena send_file() masih perlu membacanya.
        for p in saved_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


if __name__ == '__main__':
    print("\n" + "=" * 50)
    print(f"🚀 XR-App · eXtract-Report v{VERSI}")
    print("=" * 50)
    print("\n📍 Akses aplikasi di: http://localhost:5000")
    print("📍 Atau: http://127.0.0.1:5000")
    print(f"\n🏦 Bank tersedia: {', '.join(get_enabled_banks().keys())}")
    print("\n⏹️  Tekan CTRL+C untuk stop server\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
