"""
penyatuan.py — Tes aturan penyatuan varian nama lawan transaksi.

    python tests/penyatuan.py

Kenapa perlu tes sendiri: aturannya halus, dan kesalahannya tidak kelihatan
di laporan. Menggabungkan dua pihak yang sebenarnya berbeda membuat HHI salah
ke arah yang justru berbahaya (rekening terpusat tampak terdiversifikasi),
dan hasilnya tetap terlihat rapi. Jadi yang diuji bukan hanya "apa yang
digabung", tapi juga **apa yang HARUS TETAP terpisah**.

Kasus-kasusnya diambil dari data nyata: rekening koran yang dikirim pemakai
dan PDF referensi di references/. Pasangan yang tidak boleh digabung ("CITRA
PERISAI" vs "CITRA PERISAI LINTASINDO") sempat benar-benar tergabung oleh
percobaan aturan sebelumnya — itulah yang tes ini jaga supaya tidak terulang.

Keluar dengan kode 1 kalau ada yang meleset, jadi bisa dipakai di CI.
"""

import os
import sys

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from engine.penyatu_nama import kunci, satukan   # noqa: E402

# ── Nama yang HARUS menyatu ──────────────────────────────────────────────
# (daftar nama, berapa baris yang menyatu, wakil yang diharapkan)
HARUS_MENYATU = [
    # Tahap 1 — beda bentuk badan usaha, spasi, tanda baca.
    (['PT BARASENTOSA LESTARI', 'BARASENTOSA LESTARI'], 1,
     'PT BARASENTOSA LESTARI'),
    (['PT WIRAKARYA SAKTI', 'PT WIRA KARYA SAKTI'], 1, 'PT WIRA KARYA SAKTI'),
    (['TRI PUTRA ERGUNA', 'PT.TRI PUTRA ERGUNA'], 1, 'PT.TRI PUTRA ERGUNA'),
    (['MAHYONO', 'Mahyono'], 1, 'MAHYONO'),

    # Tahap 2 — terpotong di TENGAH KATA.
    (['BARASENTOSA LESTARI', 'BARASENTOSA LES'], 1, 'BARASENTOSA LESTARI'),
    (['PT TEBO MULTI AGRO', 'PT TEBO MULTI A'], 1, 'PT TEBO MULTI AGRO'),
    (['PT SINGA LAUTAN INDONESIA', 'SINGA LAUTAN IN'], 1,
     'PT SINGA LAUTAN INDONESIA'),
    (['EKAMATRA RIZQI ANUGRAH', 'EKAMATRA RIZQI ANUGR'], 1,
     'EKAMATRA RIZQI ANUGRAH'),

    # Gabungan kedua tahap dalam satu kelompok.
    (['PT BARASENTOSA LESTARI', 'BARASENTOSA LESTARI', 'BARASENTOSA LES'], 2,
     'PT BARASENTOSA LESTARI'),
]

# ── Nama yang HARUS TETAP TERPISAH ───────────────────────────────────────
# Semuanya relasi awalan, tapi berhenti di BATAS KATA — bisa jadi memang
# pihak yang berbeda, jadi tidak boleh diputuskan sistem.
HARUS_TERPISAH = [
    ['RASITA ANUGRAH', 'RASITA ANUGRAH MULIA'],
    ['CITRA PERISAI', 'CITRA PERISAI LINTASINDO'],
    ['DAULAY HUMALA', 'DAULAY HUMALA BERSAUDARA'],
    ['BAHRUM HELMI LUBIS', 'BAHRUM HELMI LUBIS BB SPSI'],
    ['SIMSEM', 'SIMSEM PAYROLL BNI DIRECT'],
    # Awalan yang cocok ke beberapa nama yang tidak serantai.
    ['PT GARUDA INDON', 'PT GARUDA INDONESIA CARGO',
     'PT GARUDA INDONESIA TBK'],
    # Nama berawalan huruf bentuk badan usaha, tapi tanpa pemisah.
    ['PTX SEJAHTERA', 'PTX SEJAHTERA ABADI'],
]

# ── Label kategori tidak boleh ikut dilebur ──────────────────────────────
LABEL_UJI = {'Biaya Administrasi', 'Tidak Teridentifikasi',
             'Transaksi Lainnya / Tanpa Keterangan'}


def periksa_menyatu() -> list:
    masalah = []
    for nama, harap_gabung, harap_wakil in HARUS_MENYATU:
        h = satukan(nama)
        if h.jumlah_digabung != harap_gabung:
            masalah.append(
                f'{nama} → {h.jumlah_digabung} menyatu, harusnya {harap_gabung}')
        wakil = {h(n) for n in nama}
        if wakil != {harap_wakil}:
            masalah.append(f'{nama} → wakil {sorted(wakil)}, '
                           f'harusnya {harap_wakil!r}')
    return masalah


def periksa_terpisah() -> list:
    masalah = []
    for nama in HARUS_TERPISAH:
        h = satukan(nama)
        if h.jumlah_digabung:
            masalah.append(
                f'{nama} TERGABUNG padahal harus terpisah — '
                f'varian: {h.varian}')
        # Yang tidak digabung wajib muncul sebagai kandidat, supaya
        # pemeriksa tahu sistem melihatnya dan memilih tidak memutuskan.
        if not h.kandidat:
            masalah.append(f'{nama} tidak digabung TAPI juga tidak '
                           f'dilaporkan sebagai kandidat')
    return masalah


def periksa_label() -> list:
    """Label kategori harus lolos apa adanya, walau mirip satu sama lain."""
    nama = sorted(LABEL_UJI) + ['Biaya Administrasi Bulanan']
    h = satukan(nama, abaikan=LABEL_UJI)
    return [f'label {n!r} berubah jadi {h(n)!r}' for n in LABEL_UJI if h(n) != n]


def periksa_stabil() -> list:
    """Urutan masukan tidak boleh mengubah hasil."""
    nama = ['BARASENTOSA LES', 'PT BARASENTOSA LESTARI', 'BARASENTOSA LESTARI']
    a = satukan(nama).peta
    b = satukan(list(reversed(nama))).peta
    return [] if a == b else [f'hasil berubah saat urutan dibalik: {a} vs {b}']


def periksa_kunci() -> list:
    """Bentuk badan usaha hanya dibuang bila ada pemisahnya."""
    kasus = [('PT BARASENTOSA', 'BARASENTOSA'), ('PT.BARASENTOSA', 'BARASENTOSA'),
             ('PT. BARASENTOSA', 'BARASENTOSA'), ('BARASENTOSA', 'BARASENTOSA'),
             ('PTX SEJAHTERA', 'PTXSEJAHTERA'), ('CVITO MANDIRI', 'CVITOMANDIRI')]
    return [f'kunci({a!r}) = {kunci(a)!r}, harusnya {b!r}'
            for a, b in kasus if kunci(a) != b]


def main() -> int:
    masalah = []
    for nama, fungsi in (
        ('nama yang harus menyatu', periksa_menyatu),
        ('nama yang harus tetap terpisah', periksa_terpisah),
        ('label kategori tidak tersentuh', periksa_label),
        ('hasil stabil apa pun urutan masukannya', periksa_stabil),
        ('normalisasi bentuk badan usaha', periksa_kunci),
    ):
        hasil = fungsi()
        print(f'{"BEDA" if hasil else "OK  "}  {nama}')
        masalah += hasil

    if masalah:
        print('\n' + '\n'.join(f'  - {m}' for m in masalah))
        print(f'\nRingkasan: {len(masalah)} masalah.')
        return 1
    print('\nRingkasan: seluruh aturan penyatuan nama berperilaku benar.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
