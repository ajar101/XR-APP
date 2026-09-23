"""
jadwal_admin.py — Pagar atas jadwal pendebetan biaya administrasi.

    python tests/jadwal_admin.py

KENAPA TES INI ADA

Pemeriksaan "Jadwal Biaya Admin Tidak Wajar" bertingkat **Sedang**, jadi
salah di sini bukan kosmetik: tiap bulan yang keliru dinilai menghasilkan
satu temuan palsu yang harus dibaca dan dibantah pemeriksa.

Dan itu memang pernah terjadi. Pengumuman resmi BCA berbunyi:

    "Efektif per tanggal 1 Juni 2026, pendebitan biaya administrasi bulanan
     akan dilakukan setiap AWAL BULAN."

Kode menuliskannya sebagai "tanggal 1" — pengetatan yang KITA tambahkan,
bukan yang bank katakan. Akibatnya tiap bulan yang tanggal 1-nya libur
menghasilkan temuan palsu: akhir pekan (di 2026: Februari, Maret, Agustus,
November) maupun libur nasional yang jatuh tanggal 1 (Tahun Baru dan Hari
Buruh). Ketahuan dari pemakaian nyata, bukan dari pengujian — pendebetan
3 Agustus dilaporkan tidak wajar padahal 1 Agustus 2026 Sabtu.

Jadi yang dijaga di sini dua arah: jendela "awal bulan" TIDAK menghasilkan
temuan palsu, dan ia tetap cukup ketat untuk menangkap tanggal yang
sungguh-sungguh menyimpang.
"""

import datetime
import os
import sys

import pandas as pd

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from engine.anomaly_detector import _check_jadwal_biaya_adm      # noqa: E402
from extractors.bca import BCAExtractor                          # noqa: E402
from kalender import hari_bank_pertama, hari_libur               # noqa: E402

BULAN = {1: 'Januari', 2: 'Februari', 3: 'Maret', 4: 'April', 5: 'Mei',
         6: 'Juni', 7: 'Juli', 8: 'Agustus', 9: 'September',
         10: 'Oktober', 11: 'November', 12: 'Desember'}


def jadwal(jenis: str, bulan: str, tahun: int):
    """Entri jadwal seperti yang dikirim extractor lewat metadata."""
    kosong = BCAExtractor.__new__(BCAExtractor)
    return BCAExtractor._jadwal_biaya_admin(kosong, jenis, bulan, tahun)


def ada_temuan(jenis: str, bulan: str, tahun: int, tanggal: int) -> bool:
    entri = jadwal(jenis, bulan, tahun)
    saldo = {'_biaya_admin': {'label_nama': 'Biaya Admin',
                              'jadwal': {bulan: entri}}}
    df = pd.DataFrame([{'Bulan': bulan, 'Tanggal': tanggal,
                        'Jenis Mutasi': 'Debit', 'Mutasi': 15000,
                        'Nama Pengirim/Penerima': 'Biaya Admin',
                        'Keterangan Transaksi': 'BIAYA ADM'}])
    return bool(_check_jadwal_biaya_adm({bulan: df}, saldo))


def periksa_libur_tidak_jadi_temuan() -> list:
    """
    Di SETIAP bulan yang tanggal 1-nya libur, hari bank pertama tidak boleh
    dinilai tidak wajar.

    "Libur" di sini akhir pekan MAUPUN libur nasional bertanggal tetap. Dua
    dari empat libur tetap justru jatuh tanggal 1 — Tahun Baru dan Hari
    Buruh — jadi mengabaikannya bukan kasus pinggiran.

    Diuji menyeluruh atas 2026-2030, bukan pada bulan contoh saja: yang
    dicari justru bulan yang belum pernah terlihat di data referensi, karena
    di situlah aturan yang terlalu sempit baru ketahuan — dan ketahuannya
    lewat laporan pemakai, bukan lewat tes.
    """
    masalah = []
    akhir_pekan = libur_nasional = 0
    for tahun in range(2026, 2031):
        for bln in range(1, 13):
            if (tahun, bln) < (2026, 6):
                continue                      # sebelum cutover, aturan lain
            satu = datetime.date(tahun, bln, 1)
            if not hari_libur(satu):
                continue
            if satu.weekday() >= 5:
                akhir_pekan += 1
            else:
                libur_nasional += 1
            hb = hari_bank_pertama(tahun, bln)
            if ada_temuan('TAHAPAN', BULAN[bln], tahun, hb):
                masalah.append(f'{BULAN[bln]} {tahun}: tgl 1 libur, debet tgl '
                               f'{hb} dinilai tidak wajar')
            # Tanggal 1 apa adanya juga tetap sah — BCA terbukti mendebet di
            # akhir pekan (biaya GIRO didebet Minggu 31 Mei 2026).
            if ada_temuan('TAHAPAN', BULAN[bln], tahun, 1):
                masalah.append(f'{BULAN[bln]} {tahun}: debet tgl 1 sendiri '
                               'dinilai tidak wajar')
    if akhir_pekan < 10:
        masalah.append(f'cuma {akhir_pekan} bulan berakhir pekan yang teruji')
    if libur_nasional < 3:
        masalah.append(f'cuma {libur_nasional} bulan yang tanggal 1-nya libur '
                       'nasional di hari kerja yang teruji — justru kasus itu '
                       'yang baru ditambahkan')
    return masalah


def periksa_masih_ketat() -> list:
    """
    Jendela "awal bulan" tidak boleh jadi jendela untuk apa saja.

    Melonggarkan aturan selalu menggoda sampai tak ada lagi yang
    tertangkap. Tanggal yang benar-benar menyimpang harus tetap muncul.
    """
    masalah = []
    for tanggal in (5, 10, 15, 20, 28):
        if not ada_temuan('TAHAPAN', 'Agustus', 2026, tanggal):
            masalah.append(f'Agustus 2026 tgl {tanggal} tidak tertangkap — '
                           'jendelanya terlalu longgar')
    # Bulan yang tanggal 1-nya hari kerja: tidak ada yang perlu dilonggarkan.
    if not ada_temuan('TAHAPAN', 'Juli', 2026, 2):
        masalah.append('Juli 2026 tgl 2 tidak tertangkap padahal 1 Juli Rabu '
                       '— pelonggaran bocor ke bulan yang tidak membutuhkan')
    return masalah


def periksa_aturan_lama_tidak_tersentuh() -> list:
    """
    Aturan sebelum Juni 2026 tetap dicocokkan PERSIS satu tanggal.

    Justru karena keduanya terkonfirmasi dari data riil — GIRO akhir bulan
    (April tgl 30, Mei tgl 31) dan TAHAPAN Jumat minggu ke-3 (Mei 2026 tgl
    15) — melonggarkannya berarti membuang ketelitian yang sudah terbukti.
    """
    masalah = []
    for jenis, tanggal, harap in (('GIRO', 31, False), ('GIRO', 1, True),
                                  ('TAHAPAN', 15, False), ('TAHAPAN', 1, True)):
        if ada_temuan(jenis, 'Mei', 2026, tanggal) != harap:
            masalah.append(f'{jenis} Mei 2026 tgl {tanggal}: '
                           f'temuan={not harap}, harusnya {harap}')

    # Jenis rekening tak dikenal SEBELUM cutover tetap dilewati, karena di
    # situ jadwalnya memang beda per jenis dan menebak berarti temuan palsu.
    if jadwal('TAPRES', 'Mei', 2026) is not None:
        masalah.append('jenis tak dikenal sebelum cutover tidak dilewati')
    return masalah


def periksa_semua_jenis_sesudah_cutover() -> list:
    """
    Sesudah cutover, jenis rekening tidak lagi menentukan jadwal.

    Pengumumannya menyebut keenam jenis sekaligus — Tahapan, Tahapan Gold,
    Tahapan Xpresi, Tapres, BCA Dollar, Giro — dengan aturan yang SAMA. Kalau
    gerbang jenis rekening lama tetap berlaku di sini, rekening Tapres dan
    BCA Dollar kehilangan pemeriksaan yang dasarnya justru sudah kita punya:
    regex jenis rekening membaca "REKENING BCA DOLLAR" sebagai "BCA" dan
    "REKENING TAPRES" sebagai "TAPRES", dua-duanya di luar daftar lama.
    """
    masalah = []
    for jenis in ('GIRO', 'TAHAPAN', 'TAPRES', 'BCA', '-'):
        entri = jadwal(jenis, 'Agustus', 2026)
        if entri is None:
            masalah.append(f'{jenis!r} dilewati sesudah cutover — '
                           'pemeriksaannya hilang padahal dasarnya ada')
            continue
        if sorted(entri.get('tanggal_sah') or []) != [1, 3]:
            masalah.append(f'{jenis!r} Agustus 2026: tanggal sah '
                           f'{entri.get("tanggal_sah")!r}, harusnya [1, 3]')
    return masalah


def periksa_bank_lain_tidak_tersentuh() -> list:
    """
    Extractor yang mengirim satu tanggal saja (tanpa 'tanggal_sah') tetap
    dicocokkan persis seperti sebelumnya.

    Mandiri, BNI, dan BRI memakai kontrak metadata yang sama. Pelonggaran
    untuk BCA tidak boleh merembes ke jadwal mereka.
    """
    masalah = []
    saldo = {'_biaya_admin': {'label_nama': 'Biaya Admin',
                              'jadwal': {'Agustus': {'tanggal': 20,
                                                     'aturan': 'tanggal 20'}}}}
    for tanggal, harap in ((20, False), (19, True), (21, True), (1, True)):
        df = pd.DataFrame([{'Bulan': 'Agustus', 'Tanggal': tanggal,
                            'Jenis Mutasi': 'Debit', 'Mutasi': 15000,
                            'Nama Pengirim/Penerima': 'Biaya Admin',
                            'Keterangan Transaksi': 'BIAYA ADM'}])
        ada = bool(_check_jadwal_biaya_adm({'Agustus': df}, saldo))
        if ada != harap:
            masalah.append(f'jadwal satu tanggal, debet tgl {tanggal}: '
                           f'temuan={ada}, harusnya {harap}')
    return masalah


def main() -> int:
    masalah = []
    for nama, fungsi in (
        ('tanggal 1 yang libur tidak jadi temuan palsu',
         periksa_libur_tidak_jadi_temuan),
        ('jendela awal bulan masih ketat', periksa_masih_ketat),
        ('aturan sebelum Juni 2026 tidak tersentuh',
         periksa_aturan_lama_tidak_tersentuh),
        ('semua jenis rekening diperiksa sesudah cutover',
         periksa_semua_jenis_sesudah_cutover),
        ('jadwal bank lain tidak ikut longgar',
         periksa_bank_lain_tidak_tersentuh),
    ):
        hasil = fungsi()
        print(f'{"BEDA" if hasil else "OK  "}  {nama}')
        masalah += hasil

    if masalah:
        print(f'\nRingkasan: {len(masalah)} masalah.')
        for m in masalah:
            print(f'  - {m}')
        return 1
    print('\nRingkasan: jadwal biaya admin dinilai sesuai ketentuan banknya.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
