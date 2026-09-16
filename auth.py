"""
auth.py — Autentikasi, otorisasi, dan jejak audit XR-App.

Sebelum modul ini ada, aplikasi berjalan tanpa login sama sekali: siapa pun
yang bisa menjangkau portnya dapat mengunggah rekening koran dan mengunduh
laporannya, dan tidak ada catatan siapa melakukan apa. Untuk alat yang
membaca mutasi rekening nasabah, keduanya tidak bisa dibiarkan.

Dua hal yang sering dikira satu, dan dipisahkan tegas di sini:

  AUTENTIKASI  membuktikan Anda siapa                → halaman login
  OTORISASI    apa yang boleh Anda lihat setelah itu → peran + cabang

Penyimpanannya SQLite lewat modul `sqlite3` pustaka standar — tanpa ORM dan
tanpa dependensi baru. Jumlah penggunanya puluhan, bukan jutaan; memasang
lapisan ORM untuk itu hanya menambah hal yang harus dipelihara.

Catatan soal cakupan otorisasi. Aplikasi ini belum menyimpan hasil
ekstraksi — tiap unggahan langsung diunduh lalu dilupakan — jadi belum ada
"data hasil" yang bisa diisolasi antar cabang. Yang sudah nyata sekarang:
kolom cabang tercatat di jejak audit, dan halaman Riwayat hanya menampilkan
baris milik cabang penggunanya (admin melihat semua). Begitu histori hasil
ekstraksi disimpan (RINGKASAN_APLIKASI.md §6.2), batas yang sama tinggal
dipakai ulang.
"""

import functools
import logging
import os
import sqlite3
import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.security import check_password_hash, generate_password_hash

PERAN = ('admin', 'pemakai')

# Berkas basis data. WAJIB berada di volume yang awet kalau dijalankan dalam
# container — kalau tidak, seluruh akun pengguna dan jejak auditnya hilang
# setiap container dibuat ulang. Lihat DEPLOY.md.
DB_BAWAAN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'data', 'xr-app.db')

SKEMA = """
CREATE TABLE IF NOT EXISTS pengguna (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nama_pengguna TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    nama_lengkap  TEXT    NOT NULL,
    cabang        TEXT    NOT NULL,
    peran         TEXT    NOT NULL CHECK (peran IN ('admin', 'pemakai')),
    sandi_hash    TEXT    NOT NULL,
    aktif         INTEGER NOT NULL DEFAULT 1,
    dibuat        TEXT    NOT NULL
);

-- Jejak audit: siapa memproses rekening apa, kapan, dan hasilnya.
--
-- Yang SENGAJA tidak dicatat: nama pemilik rekening, nominal, dan isi
-- transaksinya. Mengikuti prinsip yang sudah dipakai app.py saat mencatat
-- checksum gagal — cukup catat apa yang diperiksa dan bagaimana hasilnya,
-- jangan salin datanya. Nomor rekening tetap dicatat karena tanpa itu
-- jejaknya tidak bisa menjawab "berkas siapa yang diproses", yang justru
-- alasan jejak ini ada.
CREATE TABLE IF NOT EXISTS jejak_audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    waktu         TEXT    NOT NULL,
    pengguna_id   INTEGER,
    nama_pengguna TEXT    NOT NULL,
    cabang        TEXT    NOT NULL,
    aksi          TEXT    NOT NULL,
    bank          TEXT,
    jumlah_berkas INTEGER,
    nama_berkas   TEXT,
    no_rekening   TEXT,
    hasil         TEXT    NOT NULL,
    keterangan    TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_waktu  ON jejak_audit (waktu DESC);
CREATE INDEX IF NOT EXISTS idx_audit_cabang ON jejak_audit (cabang);
"""


# ============================================================
# BASIS DATA
# ============================================================

def path_db(app=None) -> str:
    if app is not None:
        return app.config['DB_PATH']
    return os.environ.get('XR_DB', DB_BAWAAN)


def koneksi(app=None) -> sqlite3.Connection:
    path = path_db(app)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Foreign key & WAL: WAL supaya pembacaan riwayat tidak memblokir
    # penulisan jejak audit dari worker lain.
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def siapkan_db(app=None) -> None:
    with koneksi(app) as conn:
        conn.executescript(SKEMA)


# ============================================================
# PENGGUNA
# ============================================================

class Pengguna(UserMixin):
    def __init__(self, baris: sqlite3.Row):
        self.id = str(baris['id'])
        self.nama_pengguna = baris['nama_pengguna']
        self.nama_lengkap = baris['nama_lengkap']
        self.cabang = baris['cabang']
        self.peran = baris['peran']
        self.aktif = bool(baris['aktif'])

    @property
    def is_active(self) -> bool:
        # Flask-Login memakai ini: pengguna nonaktif tidak bisa login.
        return self.aktif

    @property
    def admin(self) -> bool:
        return self.peran == 'admin'


def ambil_pengguna(pengguna_id, app=None):
    with koneksi(app) as conn:
        baris = conn.execute(
            'SELECT * FROM pengguna WHERE id = ?', (pengguna_id,)).fetchone()
    return Pengguna(baris) if baris else None


def ambil_pengguna_nama(nama_pengguna: str, app=None):
    with koneksi(app) as conn:
        baris = conn.execute(
            'SELECT * FROM pengguna WHERE nama_pengguna = ?',
            (nama_pengguna,)).fetchone()
    return baris


def buat_pengguna(nama_pengguna, sandi, nama_lengkap, cabang,
                  peran='pemakai', app=None) -> int:
    """Tambah pengguna baru. Melempar ValueError kalau namanya sudah dipakai."""
    if peran not in PERAN:
        raise ValueError(f"Peran harus salah satu dari {PERAN}, bukan '{peran}'.")
    if not nama_pengguna or not nama_pengguna.strip():
        raise ValueError('Nama pengguna tidak boleh kosong.')
    if len(sandi or '') < 12:
        # Aplikasi internal pun tetap memegang data rekening nasabah.
        raise ValueError('Kata sandi minimal 12 karakter.')
    try:
        with koneksi(app) as conn:
            cur = conn.execute(
                'INSERT INTO pengguna (nama_pengguna, nama_lengkap, cabang, '
                '    peran, sandi_hash, aktif, dibuat) '
                'VALUES (?, ?, ?, ?, ?, 1, ?)',
                (nama_pengguna.strip(), nama_lengkap.strip(), cabang.strip(),
                 peran, generate_password_hash(sandi),
                 datetime.datetime.now().isoformat(timespec='seconds')))
            return cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Pengguna '{nama_pengguna}' sudah ada.") from e


def ubah_aktif(pengguna_id: int, aktif: bool, app=None) -> None:
    with koneksi(app) as conn:
        conn.execute('UPDATE pengguna SET aktif = ? WHERE id = ?',
                     (1 if aktif else 0, pengguna_id))


def daftar_pengguna(app=None) -> list:
    with koneksi(app) as conn:
        return conn.execute(
            'SELECT id, nama_pengguna, nama_lengkap, cabang, peran, aktif, '
            '       dibuat FROM pengguna ORDER BY cabang, nama_pengguna'
        ).fetchall()


def ada_pengguna(app=None) -> bool:
    with koneksi(app) as conn:
        return conn.execute('SELECT 1 FROM pengguna LIMIT 1').fetchone() is not None


# ============================================================
# JEJAK AUDIT
# ============================================================

def catat_audit_langsung(db_path, pengguna_id, nama_pengguna, cabang,
                         aksi, hasil, bank=None, jumlah_berkas=None,
                         nama_berkas=None, no_rekening=None,
                         keterangan=None) -> None:
    """
    Tulis satu baris jejak audit TANPA konteks Flask apa pun.

    Dipakai worker RQ (lihat tugas.py): worker berjalan di proses lain, tidak
    punya `current_user` maupun objek aplikasi, jadi semua yang dibutuhkan
    diteruskan eksplisit.

    Tidak pernah melempar exception ke pemanggilnya: gagal mencatat jejak
    tidak boleh menggagalkan pekerjaan penggunanya — tapi juga tidak boleh
    diam, jadi kegagalannya masuk log.
    """
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute('PRAGMA journal_mode = WAL')
            conn.executescript(SKEMA)
            conn.execute(
                'INSERT INTO jejak_audit (waktu, pengguna_id, nama_pengguna, '
                '    cabang, aksi, bank, jumlah_berkas, nama_berkas, '
                '    no_rekening, hasil, keterangan) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (datetime.datetime.now().isoformat(timespec='seconds'),
                 pengguna_id, nama_pengguna, cabang, aksi, bank,
                 jumlah_berkas, nama_berkas, no_rekening, hasil, keterangan))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logging.getLogger(__name__).exception(
            'Gagal menulis jejak audit (aksi=%s)', aksi)


def catat_audit(aksi: str, hasil: str, bank=None, jumlah_berkas=None,
                nama_berkas=None, no_rekening=None, keterangan=None,
                app=None) -> None:
    """
    Catat satu kejadian atas nama pengguna yang sedang masuk.

    Pembungkus catat_audit_langsung() yang mengambil identitas dari sesi.
    Hanya bisa dipakai di dalam konteks request.
    """
    catat_audit_langsung(
        db_path=path_db(app),
        pengguna_id=getattr(current_user, 'id', None),
        nama_pengguna=getattr(current_user, 'nama_pengguna', '-'),
        cabang=getattr(current_user, 'cabang', '-'),
        aksi=aksi, hasil=hasil, bank=bank, jumlah_berkas=jumlah_berkas,
        nama_berkas=nama_berkas, no_rekening=no_rekening,
        keterangan=keterangan)


def baca_audit(cabang=None, batas=200, app=None) -> list:
    """Baris jejak audit, dibatasi cabang kalau diminta."""
    with koneksi(app) as conn:
        if cabang is None:
            return conn.execute(
                'SELECT * FROM jejak_audit ORDER BY id DESC LIMIT ?',
                (batas,)).fetchall()
        return conn.execute(
            'SELECT * FROM jejak_audit WHERE cabang = ? ORDER BY id DESC LIMIT ?',
            (cabang, batas)).fetchall()


# ============================================================
# OTORISASI
# ============================================================

def wajib_admin(f):
    """Batasi route ke pengguna berperan admin."""
    @functools.wraps(f)
    @login_required
    def pembungkus(*a, **kw):
        if not current_user.admin:
            abort(403)
        return f(*a, **kw)
    return pembungkus


# ============================================================
# PEMASANGAN KE APLIKASI FLASK
# ============================================================

def pasang(app) -> None:
    """Pasang login manager, route autentikasi, dan halaman admin."""
    app.config.setdefault('DB_PATH', os.environ.get('XR_DB', DB_BAWAAN))
    siapkan_db(app)

    manajer = LoginManager()
    manajer.login_view = 'login'
    manajer.login_message = 'Silakan masuk terlebih dahulu.'
    manajer.session_protection = 'strong'
    manajer.init_app(app)

    @manajer.user_loader
    def muat(pengguna_id):
        return ambil_pengguna(pengguna_id, app)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))

        if request.method == 'POST':
            nama = (request.form.get('nama_pengguna') or '').strip()
            sandi = request.form.get('sandi') or ''
            baris = ambil_pengguna_nama(nama, app)

            # Pesan gagal sengaja SAMA untuk ketiga sebab (nama tidak ada,
            # sandi salah, akun nonaktif). Pesan yang membedakannya
            # memberitahu orang luar nama pengguna mana yang benar-benar ada.
            if (baris is None
                    or not baris['aktif']
                    or not check_password_hash(baris['sandi_hash'], sandi)):
                app.logger.warning('Login gagal untuk "%s" dari %s',
                                   nama or '(kosong)', request.remote_addr)
                flash('Nama pengguna atau kata sandi salah.', 'galat')
                return render_template('login.html'), 401

            pengguna = Pengguna(baris)
            login_user(pengguna)
            catat_audit('login', 'berhasil', app=app)
            app.logger.info('Login berhasil: %s (%s) dari %s',
                            pengguna.nama_pengguna, pengguna.cabang,
                            request.remote_addr)
            # `next` TIDAK diikuti apa adanya: nilainya datang dari URL dan
            # bisa diarahkan ke situs lain (open redirect). Hanya path
            # relatif dalam aplikasi ini yang diterima.
            lanjut = request.args.get('next') or ''
            if lanjut.startswith('/') and not lanjut.startswith('//'):
                return redirect(lanjut)
            return redirect(url_for('index'))

        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        catat_audit('logout', 'berhasil', app=app)
        logout_user()
        return redirect(url_for('login'))

    @app.route('/riwayat')
    @login_required
    def riwayat():
        # Inilah batas otorisasinya: admin melihat seluruh cabang, pemakai
        # hanya cabangnya sendiri. Penyaringan dilakukan DI KUERI, bukan di
        # template — supaya data cabang lain tidak pernah ikut terambil.
        cabang = None if current_user.admin else current_user.cabang
        return render_template('riwayat.html',
                               baris=baca_audit(cabang, app=app),
                               lingkup=('semua cabang' if cabang is None
                                        else f'cabang {cabang}'))

    @app.route('/pengguna', methods=['GET', 'POST'])
    @wajib_admin
    def kelola_pengguna():
        if request.method == 'POST':
            aksi = request.form.get('aksi')
            try:
                if aksi == 'tambah':
                    buat_pengguna(
                        request.form.get('nama_pengguna', ''),
                        request.form.get('sandi', ''),
                        request.form.get('nama_lengkap', ''),
                        request.form.get('cabang', ''),
                        request.form.get('peran', 'pemakai'), app)
                    catat_audit('tambah_pengguna', 'berhasil',
                                keterangan=request.form.get('nama_pengguna'),
                                app=app)
                    flash('Pengguna ditambahkan.', 'oke')
                elif aksi in ('aktifkan', 'nonaktifkan'):
                    target = int(request.form['pengguna_id'])
                    # Admin tidak boleh menonaktifkan dirinya sendiri —
                    # kalau ia satu-satunya admin, tidak ada lagi yang bisa
                    # mengaktifkannya kembali.
                    if aksi == 'nonaktifkan' and str(target) == current_user.id:
                        flash('Tidak bisa menonaktifkan akun Anda sendiri.', 'galat')
                    else:
                        ubah_aktif(target, aksi == 'aktifkan', app)
                        catat_audit(aksi, 'berhasil', keterangan=str(target), app=app)
                        flash('Status pengguna diperbarui.', 'oke')
            except ValueError as e:
                flash(str(e), 'galat')
            return redirect(url_for('kelola_pengguna'))

        return render_template('pengguna.html', daftar=daftar_pengguna(app),
                               peran_tersedia=PERAN)

    @app.errorhandler(403)
    def terlarang(_):
        return render_template('galat.html', kode=403,
                               pesan='Halaman ini hanya untuk admin.'), 403
