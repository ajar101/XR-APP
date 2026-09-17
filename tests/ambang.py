"""
ambang.py — Ukur efek menyetel ambang kemiripan pada data nyata.

    python tests/ambang.py                  # dua sapuan bawaan
    python tests/ambang.py --rinci          # + daftar tiap penggabungan baru
    python tests/ambang.py --hanya BNI      # batasi ke snapshot tertentu

Kenapa ada: ambang di `engine/kemiripan_entitas.py` sengaja bisa disetel,
dan knob yang bisa disetel tanpa cara mengukurnya adalah undangan untuk
menebak. Yang ingin dijawab skrip ini dua hal, dan keduanya tidak bisa
dijawab dengan berpikir:

  1. Menurunkan ambang TINJAU — berapa banyak kandidat tambahan yang
     muncul, dan apakah tambahannya masih layak dibaca manusia? Daftar
     kandidat yang terlalu panjang bukan cuma tidak berguna, ia membuat
     pemeriksa berhenti membaca daftarnya sama sekali.

  2. Menurunkan ambang GABUNG — penggabungan apa yang mulai terjadi, dan
     BENAR atau SALAH? Ini yang tidak boleh diputuskan dari angka: tiap
     penggabungan baru harus dilihat satu per satu, karena satu penggabungan
     keliru membuat HHI Score salah ke arah sebaliknya tanpa jejak apa pun
     di laporan.

Datanya dari tests/snapshot/ — hasil ekstraksi 44 PDF referensi yang dijaga
tests/regresi.py, jadi tidak perlu membaca PDF lagi dan angkanya tetap angka
nyata, bukan sintetis.

Skrip ini MENGUKUR, tidak menguji: ia tidak punya nilai "benar" dan selalu
keluar dengan kode 0. Yang menjaga perilaku adalah tests/kemiripan.py dan
tests/penyatuan.py.
"""

import argparse
import glob
import json
import os
import sys
import time

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

import engine.penyatu_nama as pn                              # noqa: E402
from engine.kemiripan_entitas import AmbangKemiripan          # noqa: E402

# Label kategori ("Biaya Administrasi", dst) dikecualikan persis seperti di
# engine/excel_builder.py — kalau tidak, angkanya tidak sebanding dengan yang
# benar-benar terjadi di laporan.
from engine.excel_builder import LABEL_KATEGORI, LABEL_LAINNYA   # noqa: E402

ABAIKAN = LABEL_KATEGORI | {LABEL_LAINNYA}

# Sapuan 1: hanya ambang TINJAU yang digeser (ambang gabung tetap bawaan),
# jadi yang berubah murni jumlah kandidat. Dimulai dari 0.90 karena ambang
# tinjau tidak boleh melewati ambang "mungkin" — di atas itu rentang tinjau
# tidak ada artinya lagi.
SAPUAN_TINJAU = [0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60]

# Sapuan 2: ambang GABUNG yang digeser. Ambang tinjau ditahan di 0.60 supaya
# pasangan yang TIDAK ikut tergabung tetap terlihat sebagai kandidat, bukan
# hilang dari pandangan.
SAPUAN_GABUNG = [(0.95, 0.90), (0.90, 0.85), (0.85, 0.80), (0.80, 0.75),
                 (0.75, 0.70), (0.70, 0.65)]


def muat(pola: str = '') -> list:
    """(nama berkas, daftar nama lawan transaksi) dari tiap snapshot."""
    berkas = sorted(glob.glob(os.path.join(SESAT, 'tests', 'snapshot', '*.json')))
    hasil = []
    for f in berkas:
        nama_berkas = os.path.basename(f)
        if pola and pola.lower() not in nama_berkas.lower():
            continue
        with open(f) as fh:
            d = json.load(fh)
        nama = sorted({t[4] for t in d['transaksi']
                       if t[4] and str(t[4]).strip()})
        hasil.append((nama_berkas, nama))
    return hasil


def jalankan(data, ambang):
    """
    Jalankan penyatuan atas seluruh snapshot dengan satu setelan ambang.

    Batas jumlah kandidat tahap 3 dinaikkan sementara supaya yang terukur
    adalah jumlah SEBENARNYA, bukan jumlah yang tertampung di laporan —
    dua hal berbeda yang perlu dilihat berdampingan saat menyetel ambang.
    """
    batas_asli = pn.MAKS_KANDIDAT_FUZZY
    pn.MAKS_KANDIDAT_FUZZY = 10**9
    asli = pn._tahap3_kemiripan
    jumlah = {'gabung': 0, 'kandidat': 0}
    gabungan = []

    def bungkus(kelompok, kandidat, ambang_dipakai=ambang):
        sebelum = len(kandidat)
        hasil = asli(kelompok, kandidat, ambang_dipakai)
        jumlah['kandidat'] += len(kandidat) - sebelum
        jumlah['gabung'] += len(hasil)
        wakil = {k: pn._wakil(v) for k, v in kelompok.items()}
        for anak, orang_tua in hasil.items():
            gabungan.append((wakil[anak], wakil[orang_tua]))
        return hasil

    pn._tahap3_kemiripan = bungkus
    try:
        t0 = time.time()
        total_menyatu = 0
        for _nama_berkas, nama in data:
            total_menyatu += pn.satukan(nama, abaikan=ABAIKAN,
                                        ambang=ambang).jumlah_digabung
        durasi = time.time() - t0
    finally:
        pn._tahap3_kemiripan = asli
        pn.MAKS_KANDIDAT_FUZZY = batas_asli

    return {
        'menyatu_total': total_menyatu,
        'menyatu_tahap3': jumlah['gabung'],
        'kandidat_tahap3': jumlah['kandidat'],
        'durasi': durasi,
        'gabungan': gabungan,
        'batas_laporan': batas_asli,
    }


def sapuan_tinjau(data, rinci: bool) -> None:
    print('\n' + '=' * 72)
    print('SAPUAN 1 — ambang TINJAU digeser, ambang gabung tetap 0.95/0.90')
    print('=' * 72)
    print(f'{"tinjau":>7} | {"kandidat tahap 3":>16} | {"menyatu tahap 3":>15} '
          f'| {"waktu":>7}')
    print('-' * 60)
    for tinjau in SAPUAN_TINJAU:
        h = jalankan(data, AmbangKemiripan(tinjau=tinjau))
        print(f'{tinjau:>7.2f} | {h["kandidat_tahap3"]:>16} '
              f'| {h["menyatu_tahap3"]:>15} | {h["durasi"]:>6.2f}s')
    print(f'\nCatatan: kolom kandidat adalah jumlah SEBENARNYA. Yang tercetak '
          f'di sheet Rekap\ndibatasi MAKS_KANDIDAT_FUZZY = '
          f'{pn.MAKS_KANDIDAT_FUZZY} per laporan.')


def sapuan_gabung(data, rinci: bool) -> None:
    print('\n' + '=' * 72)
    print('SAPUAN 2 — ambang GABUNG digeser, ambang tinjau ditahan di 0.60')
    print('=' * 72)
    print(f'{"tinggi":>7} {"mungkin":>8} | {"menyatu tahap 3":>15} '
          f'| {"menyatu total":>13} | {"kandidat":>8}')
    print('-' * 62)
    semua = {}
    for tinggi, mungkin in SAPUAN_GABUNG:
        h = jalankan(data, AmbangKemiripan(tinggi=tinggi, mungkin=mungkin,
                                           tinjau=0.60))
        semua[(tinggi, mungkin)] = h
        print(f'{tinggi:>7.2f} {mungkin:>8.2f} | {h["menyatu_tahap3"]:>15} '
              f'| {h["menyatu_total"]:>13} | {h["kandidat_tahap3"]:>8}')

    print('\nPENGGABUNGAN YANG MULAI TERJADI — diperiksa satu per satu, '
          'karena satu\npenggabungan keliru membuat HHI salah tanpa jejak:')
    sudah = set()
    for (tinggi, mungkin), h in semua.items():
        baru = [g for g in h['gabungan'] if g not in sudah]
        sudah.update(h['gabungan'])
        if not baru:
            continue
        print(f'\n  pada ambang {tinggi:.2f}/{mungkin:.2f} — {len(baru)} baru:')
        for a, b in sorted(baru)[:40 if rinci else 12]:
            print(f'    {a!r}\n      ↔ {b!r}')
        if not rinci and len(baru) > 12:
            print(f'    ... {len(baru) - 12} lagi (jalankan dengan --rinci)')
    if not sudah:
        print('\n  (tidak ada — tahap 3 tidak menggabungkan apa pun pada '
              'seluruh setelan di atas)')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rinci', action='store_true',
                   help='tampilkan seluruh penggabungan baru, bukan 12 pertama')
    p.add_argument('--hanya', default='',
                   help='batasi ke snapshot yang namanya memuat teks ini')
    args = p.parse_args()

    data = muat(args.hanya)
    total_nama = sum(len(n) for _f, n in data)
    print(f'{len(data)} laporan, {total_nama} nama (dijumlah per laporan)')

    dasar = jalankan(data, AmbangKemiripan())
    print(f'Setelan bawaan 0.95/0.90/0.80: {dasar["menyatu_total"]} baris '
          f'menyatu, {dasar["menyatu_tahap3"]} di antaranya dari tahap 3, '
          f'{dasar["kandidat_tahap3"]} kandidat tahap 3.')

    sapuan_tinjau(data, args.rinci)
    sapuan_gabung(data, args.rinci)
    return 0


if __name__ == '__main__':
    sys.exit(main())
