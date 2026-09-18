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
import uuid
from flask import Flask, abort, jsonify, render_template, request, send_file
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

import antrean
import auth
import tugas

from extractors.registry import get_enabled_banks, get_formats
from engine.multi_pdf_merger import MAX_BULAN
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

# ── Kunci penanda tangan cookie sesi ─────────────────────────────────────
#
# Tanpa kunci yang rahasia dan TETAP, siapa pun bisa membuat cookie sesi
# palsu dan masuk sebagai pengguna mana pun — jadi kuncinya tidak boleh
# punya nilai bawaan yang bisa ditebak.
#
# Kunci acak yang dibuat ulang tiap proses mulai juga tidak memadai: tiap
# worker gunicorn akan punya kunci berbeda sehingga sesi pemakai putus
# bergantian, dan semua orang ter-logout tiap kali aplikasi di-restart.
#
# DULU SATU-SATUNYA JALAN KELUARNYA JUSTRU YANG PALING TIDAK AMAN. Kalau
# XR_SECRET_KEY tidak disetel, aplikasi menolak jalan kecuali XR_DEBUG=1 —
# dan XR_DEBUG=1 sekaligus menyalakan halaman traceback Werkzeug yang
# menampilkan isi variabel lokal, yaitu nama pemilik rekening, nomor
# rekening, dan baris mutasinya. Jadi pemakai yang cuma ingin mencoba di
# mesin sendiri didorong ke pilihan yang membuka data rekening. Itu cacat
# rancangan, bukan kelalaian pemakai, dan ditemukan dari pemakaian nyata.
#
# Perbaikannya memisahkan dua hal yang tidak ada hubungannya: "sesi harus
# awet" dan "halaman error boleh menampilkan isi variabel". Di mesin
# sendiri, kunci dibuat SEKALI lalu disimpan di data/secret_key dengan izin
# 0600 — sesi tetap awet antar restart, tanpa menyalakan debug apa pun.
# Letaknya bisa disetel lewat XR_SECRET_FILE, mengikuti pola XR_DB: bawaan
# ada di data/ (sudah di .gitignore karena memuat PII), tapi deployment yang
# memisahkan volume rahasia bisa memindahkannya — dan tesnya bisa memakai
# berkas sementara alih-alih menimpa kunci yang sedang dipakai.
KUNCI_BERKAS = os.environ.get('XR_SECRET_FILE') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'secret_key')

# Alamat yang hanya bisa dijangkau dari mesin ini sendiri. Berkas kunci
# HANYA dipakai kalau aplikasi mengikat ke salah satunya.
#
# String kosong SENGAJA tidak ada di sini, dan itu bukan kelalaian: socket
# yang di-bind ke '' mengikat ke SELURUH antarmuka (`bind('')` menghasilkan
# 0.0.0.0), jadi memasukkannya berarti XR_HOST= yang kosong akan melayani
# jaringan memakai kunci yang dimaksudkan untuk mesin sendiri. Nilai kosong
# ditangani `_alamat_ikat` sebagai "tidak disetel".
ALAMAT_LOKAL = frozenset({'127.0.0.1', 'localhost', '::1'})

PESAN_TANPA_KUNCI = (
    'XR_SECRET_KEY belum disetel. Buat sekali dengan:\n'
    "    python -c \"import secrets; print(secrets.token_hex(32))\"\n"
    'lalu simpan sebagai variabel lingkungan:\n'
    '    Linux/macOS : export XR_SECRET_KEY="..."\n'
    '    PowerShell  : $env:XR_SECRET_KEY="..."   (sesi ini saja)\n'
    '                  setx XR_SECRET_KEY "..."   (tetap, buka terminal baru)\n'
    'Lihat DEPLOY.md §4.1.\n'
    '\n'
    'Untuk mencoba di mesin sendiri, jalankan `python app.py` tanpa menyetel '
    f'XR_HOST — kuncinya akan dibuat sekali dan disimpan di {KUNCI_BERKAS}.'
)


def _baca_kunci_berkas():
    """Kunci yang tersimpan, atau None kalau belum ada."""
    try:
        with open(KUNCI_BERKAS, encoding='ascii') as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def _buat_kunci_berkas() -> str:
    """
    Buat kunci sekali, simpan dengan izin 0600, kembalikan isinya.

    Dibuat lewat os.open dengan O_CREAT|O_EXCL dan mode 0600 sejak DETIK
    PERTAMA, bukan open() biasa lalu chmod: di antara keduanya ada jeda saat
    berkasnya masih bisa dibaca pengguna lain di mesin yang sama, dan yang
    bocor di jeda itu adalah kunci yang menandatangani seluruh sesi.
    O_EXCL menutup lomba yang sama dari sisi lain — kalau dua proses mulai
    bersamaan, satu membuat dan yang lain membaca, bukan saling menimpa.
    """
    folder = os.path.dirname(KUNCI_BERKAS)
    if folder:                      # XR_SECRET_FILE boleh nama berkas saja
        os.makedirs(folder, exist_ok=True)
    kunci = secrets.token_hex(32)
    try:
        fd = os.open(KUNCI_BERKAS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Berkasnya sudah ada padahal pembacaan barusan tidak menghasilkan
        # apa-apa: proses lain baru saja membuatnya (dan isinya terbaca
        # sekarang), atau berkasnya KOSONG — mis. penulisan sebelumnya
        # terhenti karena disk penuh.
        #
        # Kasus kedua TIDAK boleh dijawab dengan kunci acak. Kunci acak di
        # sini berarti proses ini menandatangani dengan kunci yang berbeda
        # dari yang lain dan berubah lagi tiap restart — persis kegagalan
        # senyap yang seluruh rancangan ini hindari, dan bentuknya cuma
        # "kok saya ter-logout terus". Jadi lebih baik berhenti dan bilang.
        tersimpan = _baca_kunci_berkas()
        if tersimpan:
            return tersimpan
        raise RuntimeError(
            f'{KUNCI_BERKAS} ada tapi kosong. Hapus berkasnya supaya dibuat '
            'ulang, atau setel XR_SECRET_KEY.')
    with os.fdopen(fd, 'w') as fh:
        fh.write(kunci + '\n')
    return kunci


def _kunci_sesi(boleh_pakai_berkas: bool) -> str:
    """
    XR_SECRET_KEY kalau disetel; kalau tidak, berkas kunci — tapi HANYA di
    mesin sendiri.

    `boleh_pakai_berkas` sengaja diputuskan pemanggilnya, bukan ditebak di
    sini, karena yang menentukan aman atau tidaknya adalah ALAMAT IKAT — dan
    itu hal yang cuma diketahui titik masuknya. Lewat gunicorn/wsgi.py
    alamatnya ditentukan gunicorn dan tidak terlihat dari sini sama sekali,
    jadi di sana jawabannya selalu "tidak boleh" dan XR_SECRET_KEY tetap
    wajib. Dengan begitu daftar periksa DEPLOY.md tidak pernah dilemahkan
    diam-diam oleh berkas yang kebetulan tertinggal dari percobaan lokal.
    """
    kunci = os.environ.get('XR_SECRET_KEY')
    if kunci:
        return kunci
    if not boleh_pakai_berkas:
        raise RuntimeError(PESAN_TANPA_KUNCI)
    return _baca_kunci_berkas() or _buat_kunci_berkas()


def _alamat_ikat() -> str:
    """
    Alamat yang akan diikat server pengembangan di blok __main__.

    Satu-satunya sumber, dipakai DUA kali: di sini untuk memutuskan boleh
    tidaknya berkas kunci, dan di bawah untuk benar-benar mengikat. Kalau
    keduanya menghitung sendiri-sendiri, keduanya bisa berbeda — dan
    perbedaan yang mungkin justru yang berbahaya: menyimpulkan "lokal" lalu
    mengikat ke alamat yang terjangkau jaringan.
    """
    if os.environ.get('XR_DEBUG') == '1':
        return '127.0.0.1'          # debug selalu dikurung ke localhost
    # XR_HOST yang kosong berarti "tidak disetel", BUKAN alamat kosong:
    # meneruskan '' ke app.run mengikat ke seluruh antarmuka.
    return os.environ.get('XR_HOST', '').strip() or '127.0.0.1'


# Berkas kunci hanya boleh dipakai kalau app.py dijalankan LANGSUNG (server
# pengembangan) DAN alamat ikatnya lokal. `__name__` yang membedakan
# `python app.py` dari impor oleh wsgi.py.
_lokal = __name__ == '__main__' and _alamat_ikat().strip() in ALAMAT_LOKAL
app.secret_key = _kunci_sesi(_lokal)

# Cookie sesi: tidak bisa dibaca JavaScript, tidak ikut terkirim ke situs
# lain, dan hanya lewat HTTPS kecuali sedang dikembangkan di mesin sendiri.
#
# `_lokal` ikut di sini, bukan cuma XR_DEBUG. Sebelumnya satu-satunya cara
# menjalankan tanpa XR_SECRET_KEY adalah XR_DEBUG=1, yang sekaligus
# mematikan syarat HTTPS — jadi jalur "melayani http di localhost" selalu
# datang bersama SECURE=False. Berkas kunci membuat jalur itu bisa dipakai
# TANPA debug, jadi syaratnya harus ikut pindah, bukan tertinggal di
# XR_DEBUG.
#
# Diukur, bukan diasumsikan: dengan SECURE=True pun login di
# http://127.0.0.1 masih berhasil (POST /login 302, lalu GET / 200) —
# `curl` dan peramban modern memperlakukan loopback sebagai origin
# tepercaya, sehingga cookie ber-flag Secure tetap dikirim. Jadi ini bukan
# perbaikan kerusakan yang terlihat, melainkan pembetulan pernyataan:
# SECURE=True berarti "hanya kirim lewat HTTPS" pada server yang sama
# sekali tidak punya HTTPS. Yang membuatnya jalan adalah pengecualian
# loopback di peramban, dan menyandarkan sesi pada pengecualian itu berarti
# ia diam-diam putus di peramban lama, atau begitu alamat ikatnya bukan
# loopback lagi — putus tanpa pesan galat apa pun, cuma halaman login yang
# kembali terus.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=not (_lokal or os.environ.get('XR_DEBUG') == '1'),
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
antrean.periksa_sambungan(app.logger)


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
    """
    Terima berkas, lalu ekstrak — langsung atau lewat antrean.

    Validasi yang MURAH tetap dikerjakan di sini supaya pemakai dapat jawaban
    seketika (bank belum dipilih, bukan .pdf). Yang mahal — deteksi PDF hasil
    scan dan ekstraksi itu sendiri — dikerjakan tugas.proses_ekstraksi(),
    yang dipakai kedua mode supaya isinya tidak pernah berbeda.
    """
    bank_code = request.form.get('bank_code', '').strip()

    def tolak(pesan, kode=400, berkas=None):
        """Tolak permintaan sambil mencatatnya di jejak audit."""
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

    # Tiap permintaan dapat foldernya sendiri. Sebelumnya semua berkas
    # menumpuk di satu folder dengan awalan indeks, yang cukup selama hanya
    # ada satu proses; dengan worker terpisah dan beberapa permintaan
    # bersamaan, dua unggahan bernama sama bisa saling menimpa.
    id_job = uuid.uuid4().hex
    folder_job = os.path.join(app.config['UPLOAD_FOLDER'], id_job)
    os.makedirs(folder_job, exist_ok=True)

    berkas = []
    for idx, f in enumerate(files):
        # secure_filename membuang path traversal dan karakter yang
        # menyusahkan; nama aslinya tetap dibawa terpisah untuk pesan
        # kesalahan supaya pemakai mengenali berkas mana yang dimaksud.
        aman = secure_filename(f.filename) or f'berkas_{idx}.pdf'
        path = os.path.join(folder_job, f'{idx}_{aman}')
        f.save(path)
        berkas.append((path, f.filename))

    # ── Mode antrean ────────────────────────────────────────────────────
    if antrean.mode_antrean():
        # Sapu sisa job yang worker-nya mati di tengah jalan. Dilakukan di
        # sini, bukan hanya saat worker start, supaya pemasangan dengan
        # worker yang jarang di-restart tetap bersih.
        tugas.sapu_yatim(app.config['UPLOAD_FOLDER'])

        job = antrean.antrean().enqueue(
            tugas.jalankan_job,
            bank_code, berkas, folder_job,
            {'id': current_user.id,
             'nama_pengguna': current_user.nama_pengguna,
             'cabang': current_user.cabang},
            app.config['DB_PATH'],
            job_id=id_job,
            # TTL hasil INILAH kebijakan retensinya: Redis sendiri yang
            # menghapus laporan setelah lewat batas, jadi tidak ada job
            # pembersih yang bisa lupa dijalankan. Lihat tugas.py.
            result_ttl=tugas.TTL_HASIL_DETIK,
            failure_ttl=tugas.TTL_HASIL_DETIK,
        )
        # Pemiliknya disimpan di meta job, lalu dicocokkan saat mengunduh:
        # tanpa itu, siapa pun yang sudah masuk bisa mengunduh laporan orang
        # lain kalau id job-nya ketahuan.
        job.meta['pemilik'] = current_user.id
        job.meta['nama_file'] = None
        job.save_meta()

        app.logger.info('Job %s diantre oleh %s (%s), bank=%s, %d berkas',
                        job.id, current_user.nama_pengguna,
                        current_user.cabang, bank_code, len(berkas))
        return jsonify({'mode': 'antrean', 'job_id': job.id}), 202

    # ── Mode langsung ───────────────────────────────────────────────────
    try:
        hasil = tugas.proses_ekstraksi(bank_code, berkas, folder_job)
    except tugas.GagalEkstraksi as e:
        return tolak(str(e), berkas=files)
    except Exception as e:
        app.logger.exception('Ekstraksi %s gagal', bank_code)
        # Pesan teknisnya masuk jejak audit & log, TIDAK ke layar pemakai:
        # isi exception bisa memuat potongan data dokumen.
        return tolak(
            'Terjadi kesalahan saat memproses berkas. Kejadian ini sudah '
            'dicatat — hubungi admin bila berulang.', 500, berkas=files)

    for pesan in hasil['peringatan']:
        app.logger.warning(pesan)

    auth.catat_audit('ekstraksi', 'berhasil', bank=bank_code,
                     jumlah_berkas=len(files),
                     nama_berkas=', '.join(f.filename for f in files),
                     no_rekening=hasil['no_rekening'], app=app)

    return send_file(
        io.BytesIO(hasil['xlsx']),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=hasil['nama_file'],
    )


def _ambil_job(id_job):
    """
    Ambil job milik pengguna yang sedang masuk.

    Mengembalikan (job, respons_galat). Pemeriksaan pemilik dilakukan di sini
    supaya tidak ada endpoint job yang lupa melakukannya.
    """
    from rq.job import Job
    from rq.exceptions import NoSuchJobError
    try:
        job = Job.fetch(id_job, connection=antrean.koneksi_redis())
    except NoSuchJobError:
        # Hasil yang sudah lewat TTL juga jatuh ke sini — itu bukan
        # kerusakan, melainkan kebijakan retensi yang bekerja.
        return None, (jsonify({
            'status': 'hilang',
            'pesan': ('Pekerjaan tidak ditemukan atau hasilnya sudah '
                      'kedaluwarsa. Silakan unggah ulang.')}), 404)
    if job.meta.get('pemilik') != current_user.id:
        app.logger.warning('%s mencoba mengakses job %s milik pengguna lain',
                           current_user.nama_pengguna, id_job)
        return None, (jsonify({'status': 'ditolak'}), 403)
    return job, None


@app.route('/job/<id_job>')
@login_required
def status_job(id_job):
    """Status satu pekerjaan, dipanggil berkala oleh halaman depan."""
    if not antrean.mode_antrean():
        return jsonify({'status': 'hilang'}), 404

    job, galat = _ambil_job(id_job)
    if galat:
        return galat

    keadaan = job.get_status(refresh=True)
    if keadaan in ('queued', 'deferred'):
        return jsonify({'status': 'antre'})
    if keadaan == 'started':
        return jsonify({'status': 'jalan'})
    if keadaan in ('failed', 'canceled', 'stopped'):
        # Job yang meledak sampai RQ menandainya gagal berarti ada yang tidak
        # tertangani di dalam tugas.jalankan_job — pesannya tetap tidak
        # ditampilkan apa adanya ke pemakai.
        app.logger.error('Job %s berakhir dengan status %s', id_job, keadaan)
        return jsonify({'status': 'gagal',
                        'pesan': ('Pekerjaan gagal diselesaikan. Kejadian ini '
                                  'sudah dicatat — hubungi admin bila berulang.')})
    if keadaan == 'finished':
        hasil = job.return_value() or {}
        if not hasil.get('ok'):
            return jsonify({'status': 'gagal',
                            'pesan': hasil.get('pesan', 'Ekstraksi gagal.')})
        return jsonify({'status': 'selesai', 'nama_file': hasil['nama_file']})
    return jsonify({'status': 'jalan'})


@app.route('/job/<id_job>/unduh')
@login_required
def unduh_job(id_job):
    """Kirim berkas hasil satu pekerjaan."""
    if not antrean.mode_antrean():
        abort(404)

    job, galat = _ambil_job(id_job)
    if galat:
        return galat

    if job.get_status(refresh=True) != 'finished':
        return jsonify({'status': 'belum selesai'}), 409

    hasil = job.return_value() or {}
    if not hasil.get('ok'):
        return jsonify({'status': 'gagal',
                        'pesan': hasil.get('pesan', 'Ekstraksi gagal.')}), 400

    return send_file(
        io.BytesIO(hasil['xlsx']),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=hasil['nama_file'],
    )


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
    host = _alamat_ikat()
    port = int(os.environ.get('XR_PORT', '5000'))

    print("\n" + "=" * 50)
    print(f"🚀 XR-App · eXtract-Report v{VERSI}")
    print("=" * 50)
    print(f"\n📍 Akses aplikasi di: http://{host}:{port}")
    print(f"\n🏦 Bank tersedia: {', '.join(get_enabled_banks().keys())}")
    if not os.environ.get('XR_SECRET_KEY'):
        print(f"\n🔑 Kunci sesi dibaca dari {os.path.relpath(KUNCI_BERKAS)} "
              "(dibuat sekali, izin 0600).")
        print("   Sesi tetap awet antar restart tanpa menyalakan debug.")
        print("   Untuk melayani pemakai lain, setel XR_SECRET_KEY — lihat "
              "DEPLOY.md §4.1.")
    if debug:
        print("\n⚠️  MODE DEBUG AKTIF — halaman error akan menampilkan isi")
        print("    variabel, termasuk data rekening. Jangan dipakai untuk")
        print("    melayani orang lain. Hanya mengikat ke localhost.")
        print("    Debug TIDAK lagi diperlukan untuk menjalankan tanpa")
        print("    XR_SECRET_KEY — jangan pakai kalau itu alasannya.")
    else:
        print("\n💡 Untuk melayani pemakai lain, jangan pakai server ini —")
        print("   jalankan lewat gunicorn (lihat DEPLOY.md).")
    print("\n⏹️  Tekan CTRL+C untuk stop server\n")
    app.run(debug=debug, host=host, port=port)
