"""
audit_nama.py — Alat bantu audit akurasi kolom "Nama Pengirim/Penerima".

Kolom Nama adalah satu-satunya kolom di laporan yang isinya hasil TAFSIR
atas teks bebas, bukan angka yang bisa dicocokkan dengan ringkasan PDF.
Checksum extractor tidak bisa menjaganya — satu-satunya cara menilai adalah
membaca kolom keterangannya dan memutuskan apakah namanya benar. Karena itu
skrip ini TIDAK memberi nilai lulus/gagal: ia menyiapkan bahan supaya
penilaian manusianya bisa diulang orang lain dan diperiksa ulang.

    python tests/audit_nama.py bri              # statistik + sampel 40 baris
    python tests/audit_nama.py bca --sampel 60
    python tests/audit_nama.py bri --seed 12345

Sampelnya diacak dengan seed tetap (default: tanggal audit pertama), jadi
perintah yang sama selalu menghasilkan baris yang sama — hasil penilaian
bisa dirujuk dan diperiksa ulang orang lain.

Yang dihitung OTOMATIS hanya cacat yang bisa dikenali dari bentuknya (nama
kosong, nama yang isinya angka saja, penanda "Tidak Teridentifikasi").
Cacat yang bentuknya wajar tapi isinya keliru — nama yang terbaca rapi
padahal bukan lawan transaksinya — hanya bisa ditemukan dengan membaca, dan
untuk itulah sampel di bagian bawah keluaran ada.

Hasil audit yang sudah dilakukan dicatat di RINGKASAN_APLIKASI.md §7.3.
"""

import argparse
import collections
import glob
import os
import re
import random
import sys

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from extractors.registry import BANK_REGISTRY           # noqa: E402

REFERENSI = os.path.join(SESAT, 'references')

# Awalan berkas referensi per bank — sama dengan tests/regresi.py.
AWALAN_BANK = {
    'bca': ('BCA_',),
    'mandiri': ('mandiri_',),
    'bni': ('bni_', 'BNI_'),
    'bri': ('BRI ',),
    'mestika': ('Mestika',),
}

SEED_BAWAAN = 20260916          # tanggal audit BRI pertama
ANGKA_SAJA_RE = re.compile(r'[\d\s\-#.]+')


def kumpulkan(bank: str) -> list:
    """Seluruh baris transaksi dari semua PDF referensi bank tersebut."""
    awalan = AWALAN_BANK[bank]
    berkas = sorted(
        p for p in glob.glob(os.path.join(REFERENSI, '*.pdf'))
        if os.path.basename(p).startswith(awalan)
    )
    if not berkas:
        sys.exit(f'Tidak ada PDF referensi untuk {bank} di {REFERENSI}.')

    ExtractorClass = BANK_REGISTRY[bank]['extractor']
    baris = []
    for path in berkas:
        ex = ExtractorClass(path)
        saldo = ex.extract_saldo()
        for bulan, df in ex.extract_transaksi().items():
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                baris.append({
                    'berkas': os.path.basename(path),
                    'jenis': saldo.get('_jenis_rekening'),
                    'bulan': bulan,
                    'tanggal': int(r['Tanggal']),
                    'mutasi_jenis': r['Jenis Mutasi'],
                    'mutasi': int(r['Mutasi']),
                    'nama': str(r['Nama Pengirim/Penerima']),
                    'ket': str(r['Keterangan Transaksi']),
                })
    return baris


def bentuk_nama(nama: str) -> str:
    n = (nama or '').strip()
    if not n:
        return 'kosong'
    if n.upper() == 'TIDAK TERIDENTIFIKASI':
        return 'ditandai tidak teridentifikasi'
    if ANGKA_SAJA_RE.fullmatch(n):
        return 'angka/nomor saja'
    return 'nama atau label teks'


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    p.add_argument('bank', choices=sorted(AWALAN_BANK))
    p.add_argument('--sampel', type=int, default=40,
                   help='jumlah baris yang ditarik untuk dinilai manual')
    p.add_argument('--seed', type=int, default=SEED_BAWAAN)
    a = p.parse_args()

    baris = kumpulkan(a.bank)
    N = len(baris)
    print(f'POPULASI {a.bank.upper()} — {N} baris dari '
          f'{len({b["berkas"] for b in baris})} PDF referensi\n')

    print('Bentuk isi kolom Nama (dihitung atas SELURUH populasi):')
    for bentuk, n in collections.Counter(bentuk_nama(b['nama'])
                                         for b in baris).most_common():
        print(f'  {bentuk:32s} {n:5d}  {n / N * 100:6.2f}%')

    print('\nNama paling sering muncul — periksa apakah ada kode kanal atau')
    print('token sampah yang menyamar jadi nama (mis. "ATMSTRPRM", "0"):')
    for nama, n in collections.Counter(b['nama'] for b in baris).most_common(15):
        contoh = next(b for b in baris if b['nama'] == nama)
        print(f'  {n:5d}x  {nama[:40]:40s} | {contoh["ket"][:60]}')

    jml = min(a.sampel, N)
    print(f'\n{"=" * 70}')
    print(f'SAMPEL {jml} BARIS (seed {a.seed}) — nilai tiap baris dengan membaca')
    print('kolom KET: apakah NAMA sama dengan yang akan dibaca pemeriksa manusia,')
    print('atau label/nomor yang tepat ketika dokumennya memang tidak mencetak nama?')
    print('=' * 70)
    for i, b in enumerate(random.Random(a.seed).sample(baris, jml), 1):
        print(f'\n[{i:02d}] {b["berkas"]} | {b["jenis"]} | {b["bulan"]} {b["tanggal"]} '
              f'| {b["mutasi_jenis"]} Rp{b["mutasi"]:,}')
        print(f'     NAMA: {b["nama"]}')
        print(f'     KET : {b["ket"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
