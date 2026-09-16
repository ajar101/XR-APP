"""
manage.py — Perintah administrasi XR-App dari baris perintah.

    python manage.py buat-admin        # admin pertama, wajib sebelum dipakai
    python manage.py tambah-pengguna
    python manage.py daftar
    python manage.py ganti-sandi <nama_pengguna>

Kenapa admin pertama dibuat lewat CLI dan bukan lewat halaman web
"pendaftaran": halaman semacam itu harus terbuka tanpa login, dan sepanjang
belum ada yang mendaftar, siapa pun yang bisa menjangkau aplikasi bisa
mengangkat dirinya jadi admin. Membuatnya dari baris perintah berarti
pembuat admin pertama harus sudah punya akses ke servernya.

Kata sandi diminta interaktif (tidak ditampilkan saat diketik) dan tidak
pernah diterima sebagai argumen — argumen baris perintah tersimpan di
riwayat shell dan terlihat oleh proses lain lewat daftar proses.
"""

import argparse
import getpass
import os
import sys

SESAT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SESAT)

import auth  # noqa: E402


def _minta_sandi() -> str:
    sandi = getpass.getpass('Kata sandi (min. 12 karakter): ')
    if sandi != getpass.getpass('Ulangi kata sandi: '):
        sys.exit('Kata sandi tidak cocok.')
    if len(sandi) < 12:
        sys.exit('Kata sandi minimal 12 karakter.')
    return sandi


def _buat(peran: str, args) -> int:
    auth.siapkan_db()
    nama_pengguna = args.nama_pengguna or input('Nama pengguna: ').strip()
    nama_lengkap = args.nama_lengkap or input('Nama lengkap: ').strip()
    cabang = args.cabang or input('Cabang: ').strip()
    try:
        auth.buat_pengguna(nama_pengguna, _minta_sandi(), nama_lengkap,
                           cabang, peran)
    except ValueError as e:
        sys.exit(str(e))
    print(f'✓ Pengguna "{nama_pengguna}" ({peran}, cabang {cabang}) dibuat '
          f'di {auth.path_db()}')
    return 0


def perintah_buat_admin(args) -> int:
    auth.siapkan_db()
    if auth.ada_pengguna() and not args.paksa:
        sys.exit('Sudah ada pengguna di basis data. Tambahkan lewat halaman '
                 'Pengguna di aplikasi, atau pakai --paksa bila memang '
                 'ingin menambah admin lain dari sini.')
    return _buat('admin', args)


def perintah_tambah(args) -> int:
    return _buat(args.peran, args)


def perintah_daftar(_) -> int:
    auth.siapkan_db()
    baris = auth.daftar_pengguna()
    if not baris:
        print('Belum ada pengguna. Buat admin pertama: '
              'python manage.py buat-admin')
        return 0
    print(f'{"NAMA PENGGUNA":<20} {"NAMA LENGKAP":<26} {"CABANG":<14} '
          f'{"PERAN":<9} STATUS')
    print('-' * 80)
    for p in baris:
        print(f'{p["nama_pengguna"]:<20} {p["nama_lengkap"]:<26} '
              f'{p["cabang"]:<14} {p["peran"]:<9} '
              f'{"aktif" if p["aktif"] else "nonaktif"}')
    return 0


def perintah_ganti_sandi(args) -> int:
    from werkzeug.security import generate_password_hash
    auth.siapkan_db()
    if auth.ambil_pengguna_nama(args.nama_pengguna) is None:
        sys.exit(f'Pengguna "{args.nama_pengguna}" tidak ditemukan.')
    sandi = _minta_sandi()
    with auth.koneksi() as conn:
        conn.execute('UPDATE pengguna SET sandi_hash = ? WHERE nama_pengguna = ?',
                     (generate_password_hash(sandi), args.nama_pengguna))
    print(f'✓ Kata sandi "{args.nama_pengguna}" diganti.')
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description='Administrasi XR-App')
    sub = p.add_subparsers(dest='perintah', required=True)

    def tambah_argumen_umum(sp):
        sp.add_argument('--nama-pengguna', dest='nama_pengguna')
        sp.add_argument('--nama-lengkap', dest='nama_lengkap')
        sp.add_argument('--cabang')

    sp = sub.add_parser('buat-admin', help='Buat admin pertama')
    tambah_argumen_umum(sp)
    sp.add_argument('--paksa', action='store_true',
                    help='Tetap buat walau sudah ada pengguna lain')
    sp.set_defaults(fungsi=perintah_buat_admin)

    sp = sub.add_parser('tambah-pengguna', help='Tambah pengguna')
    tambah_argumen_umum(sp)
    sp.add_argument('--peran', choices=auth.PERAN, default='pemakai')
    sp.set_defaults(fungsi=perintah_tambah)

    sp = sub.add_parser('daftar', help='Tampilkan seluruh pengguna')
    sp.set_defaults(fungsi=perintah_daftar)

    sp = sub.add_parser('ganti-sandi', help='Ganti kata sandi pengguna')
    sp.add_argument('nama_pengguna')
    sp.set_defaults(fungsi=perintah_ganti_sandi)

    args = p.parse_args()
    return args.fungsi(args)


if __name__ == '__main__':
    sys.exit(main())
