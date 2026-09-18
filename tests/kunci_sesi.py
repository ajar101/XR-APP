"""
kunci_sesi.py — Pagar atas cara aplikasi mendapat kunci penanda tangan sesi.

    python tests/kunci_sesi.py

KENAPA TES INI ADA

Kunci sesi punya dua syarat yang menarik ke arah berlawanan, dan cacat yang
dulu ada lahir persis dari situ:

  · Di produksi kunci HARUS disetel dari luar. Kunci acak per proses membuat
    tiap worker gunicorn menandatangani dengan kunci berbeda, dan semua
    orang ter-logout tiap restart.
  · Di mesin sendiri orang cuma ingin mencoba, dan menyuruh mereka menyetel
    variabel lingkungan lebih dulu membuat jalan keluar termudah jadi
    XR_DEBUG=1 — yang menyalakan halaman traceback berisi data rekening.

Perbaikannya memisahkan keduanya, dan pemisahan itu hanya berarti kalau
ARAHNYA tidak bisa terbalik: berkas kunci lokal tidak boleh pernah menjadi
jalan pintas di produksi. Itu yang dijaga di sini.

Tesnya menjalankan app.py sebagai PROSES TERPISAH, bukan mengimpornya,
karena yang diuji justru keputusan yang bergantung pada `__name__` dan
variabel lingkungan — dua hal yang tidak bisa dipalsukan dari dalam satu
proses tanpa sekaligus membuang yang sedang diuji.
"""

import os
import subprocess
import sys
import tempfile

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Panggilan yang meniru gunicorn: wsgi.py MENGIMPOR app, jadi __name__ di
# app.py bukan '__main__' dan berkas kunci tidak boleh terpakai.
IMPOR_WSGI = (
    'import wsgi; '
    "print('SECRET', bool(wsgi.app.secret_key)); "
    "print('SECURE', wsgi.app.config['SESSION_COOKIE_SECURE'])"
)

# Panggilan yang meniru `python app.py`: dijalankan dengan run_name
# '__main__' supaya keputusan kunci sama persis dengan yang sebenarnya, tapi
# Flask.run diganti supaya tidak benar-benar mengikat port.
JALAN_LANGSUNG = '''
import os, runpy, sys
sys.path.insert(0, {sesat!r})
os.chdir({sesat!r})
import flask
def diam(self, *a, **k):
    print('SECRET', bool(self.secret_key))
    print('SECURE', self.config['SESSION_COOKIE_SECURE'])
    print('HOST', k.get('host'))
flask.Flask.run = diam
runpy.run_path(os.path.join({sesat!r}, 'app.py'), run_name='__main__')
'''


def jalankan(kode: str, lingkungan: dict):
    """Jalankan potongan kode di proses baru, kembalikan (rc, keluaran)."""
    env = dict(os.environ)
    env.pop('XR_SECRET_KEY', None)
    env.pop('XR_DEBUG', None)
    env.pop('XR_HOST', None)
    env.update({k: v for k, v in lingkungan.items() if v is not None})
    hasil = subprocess.run([sys.executable, '-c', kode], cwd=SESAT, env=env,
                           capture_output=True, text=True, timeout=120)
    return hasil.returncode, hasil.stdout + hasil.stderr


def nilai(keluaran: str, nama: str):
    for baris in keluaran.splitlines():
        if baris.startswith(nama + ' '):
            return baris.split(' ', 1)[1].strip()
    return None


def periksa_produksi_tetap_wajib() -> list:
    """
    Jalur gunicorn menolak jalan tanpa XR_SECRET_KEY — TERMASUK kalau berkas
    kunci lokal kebetulan ada.

    Ini pemeriksaan terpenting di berkas ini. Berkas kunci dibuat dengan
    sengaja saat seseorang mencoba di mesinnya sendiri, lalu ikut terbawa
    kalau foldernya disalin ke server. Kalau jalur produksi mau membacanya,
    daftar periksa DEPLOY.md ("XR_SECRET_KEY disetel dari nilai yang tetap
    dan rahasia") jadi tidak benar tanpa ada yang tahu.
    """
    masalah = []
    with tempfile.TemporaryDirectory() as tmp:
        berkas = os.path.join(tmp, 'secret_key')

        rc, keluaran = jalankan(IMPOR_WSGI, {'XR_SECRET_FILE': berkas})
        if rc == 0:
            masalah.append('wsgi jalan tanpa XR_SECRET_KEY — produksi tidak '
                           'lagi menuntut kunci')
        elif 'XR_SECRET_KEY' not in keluaran:
            masalah.append(f'penolakannya tidak menyebut XR_SECRET_KEY: '
                           f'{keluaran[-200:]}')

        # Sekarang dengan berkas kunci yang BENAR-BENAR ada.
        with open(berkas, 'w') as fh:
            fh.write('a' * 64 + '\n')
        rc, keluaran = jalankan(IMPOR_WSGI, {'XR_SECRET_FILE': berkas})
        if rc == 0:
            masalah.append('wsgi memakai berkas kunci lokal — jalur produksi '
                           'bisa dilemahkan berkas yang tertinggal')

        # Dengan XR_SECRET_KEY barulah boleh, dan di situ HTTPS tetap wajib.
        rc, keluaran = jalankan(IMPOR_WSGI, {'XR_SECRET_FILE': berkas,
                                             'XR_SECRET_KEY': 'x' * 64})
        if rc != 0:
            masalah.append(f'wsgi menolak padahal XR_SECRET_KEY disetel: '
                           f'{keluaran[-200:]}')
        elif nilai(keluaran, 'SECURE') != 'True':
            masalah.append('SESSION_COOKIE_SECURE tidak True di jalur '
                           'produksi — cookie sesi boleh lewat http')
    return masalah


def periksa_lokal_tidak_butuh_debug() -> list:
    """
    `python app.py` di localhost jalan tanpa XR_SECRET_KEY dan tanpa
    XR_DEBUG, kuncinya tersimpan dengan izin 0600, dan TIDAK berubah di
    jalan berikutnya.

    Kunci yang berubah tiap restart sama saja dengan tidak ada: pemakai
    ter-logout tiap kali aplikasinya dinyalakan ulang, dan itu persis yang
    dulu terjadi dengan kunci sementara XR_DEBUG.
    """
    masalah = []
    kode = JALAN_LANGSUNG.format(sesat=SESAT)
    with tempfile.TemporaryDirectory() as tmp:
        berkas = os.path.join(tmp, 'data', 'secret_key')

        rc, keluaran = jalankan(kode, {'XR_SECRET_FILE': berkas})
        if rc != 0 or nilai(keluaran, 'SECRET') != 'True':
            masalah.append(f'`python app.py` gagal tanpa XR_SECRET_KEY: '
                           f'{keluaran[-300:]}')
            return masalah

        if not os.path.exists(berkas):
            masalah.append(f'berkas kunci tidak dibuat di {berkas}')
            return masalah

        izin = oct(os.stat(berkas).st_mode & 0o777)
        if izin != '0o600':
            masalah.append(f'izin berkas kunci {izin}, harusnya 0o600 — '
                           'kunci sesi terbaca pengguna lain di mesin itu')

        with open(berkas) as fh:
            pertama = fh.read().strip()
        if len(pertama) < 32:
            masalah.append(f'kunci tersimpan cuma {len(pertama)} karakter')

        # Cookie sesi tidak boleh menuntut HTTPS saat melayani http lokal.
        if nilai(keluaran, 'SECURE') != 'False':
            masalah.append('SESSION_COOKIE_SECURE True padahal melayani http '
                           'di localhost — sesi bergantung pada pengecualian '
                           'loopback peramban')

        rc, keluaran = jalankan(kode, {'XR_SECRET_FILE': berkas})
        with open(berkas) as fh:
            kedua = fh.read().strip()
        if pertama != kedua:
            masalah.append('kunci berubah di jalan kedua — sesi putus tiap '
                           'restart, sama saja dengan tidak punya kunci')

        # Berkas kunci KOSONG — mis. penulisan sebelumnya terhenti karena
        # disk penuh. Jawaban yang salah di sini adalah memakai kunci acak:
        # ia "berhasil jalan" lalu membuat pemakai ter-logout tiap restart
        # tanpa satu pun pesan galat. Yang benar berhenti dan bilang.
        open(berkas, 'w').close()
        rc, keluaran = jalankan(kode, {'XR_SECRET_FILE': berkas})
        if rc == 0:
            masalah.append('berkas kunci kosong diterima diam-diam — kunci '
                           'acak per proses, dan pemakai ter-logout tiap '
                           'restart tanpa pesan galat')
        elif 'kosong' not in keluaran:
            masalah.append(f'berkas kunci kosong ditolak tapi pesannya tidak '
                           f'menyebut sebabnya: {keluaran[-200:]}')
    return masalah


def periksa_alamat_jaringan_tetap_ditolak() -> list:
    """
    Begitu alamat ikatnya bisa dijangkau jaringan, berkas kunci tidak berlaku
    lagi — sekalipun dijalankan lewat `python app.py`.

    Yang menentukan aman atau tidaknya memang alamat ikat, bukan cara
    menjalankan. Dan karena XR_DEBUG mengurung alamatnya ke localhost, ia
    tetap boleh: itu satu-satunya hal yang XR_DEBUG masih putuskan di sini.
    """
    masalah = []
    kode = JALAN_LANGSUNG.format(sesat=SESAT)
    with tempfile.TemporaryDirectory() as tmp:
        berkas = os.path.join(tmp, 'data', 'secret_key')

        for host in ('0.0.0.0', '192.168.1.10'):
            rc, keluaran = jalankan(kode, {'XR_SECRET_FILE': berkas,
                                           'XR_HOST': host})
            if rc == 0:
                masalah.append(f'XR_HOST={host} tanpa XR_SECRET_KEY tetap '
                               'jalan — berkas kunci lokal dipakai melayani '
                               'jaringan')
            if os.path.exists(berkas):
                masalah.append(f'XR_HOST={host} sempat MEMBUAT berkas kunci')
                os.remove(berkas)

        # XR_DEBUG memaksa alamatnya kembali ke localhost, jadi boleh.
        rc, keluaran = jalankan(kode, {'XR_SECRET_FILE': berkas,
                                       'XR_HOST': '0.0.0.0', 'XR_DEBUG': '1'})
        if rc != 0:
            masalah.append(f'XR_DEBUG=1 (yang mengurung ke localhost) malah '
                           f'ditolak: {keluaran[-200:]}')

        # XR_HOST kosong. Socket yang di-bind ke '' mengikat ke SELURUH
        # antarmuka, jadi memperlakukannya sebagai "alamat lokal" akan
        # melayani jaringan memakai kunci mesin sendiri. Yang benar:
        # dianggap tidak disetel, lalu jatuh ke loopback.
        rc, keluaran = jalankan(kode, {'XR_SECRET_FILE': berkas,
                                       'XR_HOST': ''})
        alamat = nilai(keluaran, 'HOST')
        if rc != 0:
            masalah.append(f'XR_HOST kosong ditolak, harusnya dianggap tidak '
                           f'disetel: {keluaran[-200:]}')
        elif alamat not in ("'127.0.0.1'", '127.0.0.1'):
            masalah.append(f'XR_HOST kosong mengikat ke {alamat!r}, bukan '
                           'loopback — bind("") berarti SELURUH antarmuka')
    return masalah


def main() -> int:
    masalah = []
    for nama, fungsi in (
        ('produksi tetap menuntut XR_SECRET_KEY',
         periksa_produksi_tetap_wajib),
        ('localhost jalan tanpa XR_SECRET_KEY dan tanpa XR_DEBUG',
         periksa_lokal_tidak_butuh_debug),
        ('alamat yang terjangkau jaringan tetap ditolak',
         periksa_alamat_jaringan_tetap_ditolak),
    ):
        hasil = fungsi()
        print(f'{"BEDA" if hasil else "OK  "}  {nama}')
        masalah += hasil

    if masalah:
        print(f'\nRingkasan: {len(masalah)} masalah.')
        for m in masalah:
            print(f'  - {m}')
        return 1
    print('\nRingkasan: kunci sesi diperoleh dengan cara yang benar di '
          'ketiga jalur.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
