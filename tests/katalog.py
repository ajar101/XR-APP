"""
katalog.py — Pagar supaya isi laporan yang DIJANJIKAN sama dengan yang DIBUAT.

Kenapa ada: daftar sheet pernah ditulis tiga kali — sebagai urutan
pemanggilan builder di engine/excel_builder.py, sebagai teks di halaman
depan, dan sebagai tabel di RINGKASAN_APLIKASI.md. Ketiganya berbeda:
engine sudah menghasilkan 12 sheet sementara halaman depan masih
menjanjikan 8 dan tidak menyebut sheet "Indikasi Kejanggalan" sama sekali.

Sekarang daftarnya tinggal di engine/report_catalog.py dan dibaca semua
pihak. Tes ini memastikan hubungan itu tidak putus lagi:

    python tests/katalog.py

Keluar dengan kode 1 kalau ada beda — jadi bisa dipakai di CI. Tidak butuh
PDF referensi: datanya dibuat sintetis, karena yang diperiksa STRUKTUR
laporan, bukan kebenaran angkanya (itu tugas tests/regresi.py).
"""

import os
import sys
import tempfile

import pandas as pd

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from openpyxl import load_workbook                                # noqa: E402

from engine.report_catalog import SHEETS, JUDUL_SHEET, DAFTAR_INDIKATOR  # noqa: E402
from engine.excel_builder import create_excel, _BUILDER           # noqa: E402


def contoh_data():
    """Satu bulan berisi dua transaksi — cukup untuk membangun semua sheet."""
    saldo_df = pd.DataFrame({
        'Bulan': ['Januari'] * 2,
        'Tanggal': [1, 2],
        'Saldo Akhir Harian': [1_000_000, 1_500_000],
    })
    transaksi_df = pd.DataFrame({
        'Bulan': ['Januari'] * 2,
        'Tanggal': [1, 2],
        'Jenis Mutasi': ['Kredit', 'Debit'],
        'Mutasi': [700_000, 200_000],
        'Nama Pengirim/Penerima': ['PT CONTOH SATU', 'PT CONTOH DUA'],
        'Keterangan Transaksi': ['TRSF E-BANKING CR', 'TRSF E-BANKING DB'],
    })
    saldo = {
        'Januari': {'df': saldo_df, 'tahun': '2026'},
        '_nama_pemilik': 'PEMILIK UJI',
        '_no_rekening': '0000000000',
        '_jenis_rekening': 'GIRO',
        '_saldo_awal_Januari': 500_000,
    }
    return saldo, {'Januari': transaksi_df}


def periksa_pemetaan() -> list:
    """Tiap entri katalog punya builder, dan sebaliknya."""
    kunci_katalog = {kunci for kunci, _, _ in SHEETS}
    beda = kunci_katalog ^ set(_BUILDER)
    if beda:
        return [f'SHEETS dan _BUILDER tidak menutup satu sama lain: {sorted(beda)}']
    return []


def periksa_workbook() -> list:
    """Judul sheet di berkas Excel = judul di katalog, urutannya sekalian."""
    saldo, transaksi = contoh_data()
    with tempfile.TemporaryDirectory() as tmp:
        keluaran = os.path.join(tmp, 'uji.xlsx')
        # pdf_path=None: pemeriksaan berbasis PDF mentah dilewati, sheetnya
        # tetap dibuat — itulah yang sedang diperiksa di sini.
        create_excel(saldo, transaksi, keluaran, bank_name='UJI', pdf_path=None)
        judul = load_workbook(keluaran).sheetnames
    if judul != JUDUL_SHEET:
        return ['Judul sheet berbeda dari katalog:',
                f'  katalog : {JUDUL_SHEET}',
                f'  workbook: {judul}']
    return []


def periksa_halaman_depan() -> list:
    """Angka yang dijanjikan halaman depan = angka katalog."""
    from app import app

    with app.test_client() as klien:
        respons = klien.get('/')
        if respons.status_code != 200:
            return [f'Halaman depan gagal dirender: HTTP {respons.status_code}']
        html = respons.data.decode()

    masalah = []
    if f'Output · {len(SHEETS)} Sheet Excel' not in html:
        masalah.append(f'Halaman depan tidak menyebut {len(SHEETS)} sheet.')
    for judul in JUDUL_SHEET:
        if judul not in html:
            masalah.append(f'Sheet "{judul}" tidak disebut di halaman depan.')
    if f'{len(DAFTAR_INDIKATOR)} indikator' not in html:
        masalah.append(
            f'Halaman depan tidak menyebut {len(DAFTAR_INDIKATOR)} indikator.')
    return masalah


def main() -> int:
    masalah = []
    for nama, periksa in (
        ('pemetaan katalog → builder', periksa_pemetaan),
        ('judul sheet di berkas Excel', periksa_workbook),
        ('janji halaman depan', periksa_halaman_depan),
    ):
        hasil = periksa()
        print(f'{"BEDA" if hasil else "OK  "}  {nama}')
        masalah += hasil

    if masalah:
        print('\n' + '\n'.join(masalah))
        print(f'\nRingkasan: {len(masalah)} ketidakcocokan.')
        return 1
    print('\nRingkasan: katalog, berkas Excel, dan halaman depan cocok.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
