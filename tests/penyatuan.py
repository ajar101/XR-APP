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

from engine.penyatu_nama import (MAKS_KANDIDAT_FUZZY, kunci,   # noqa: E402
                                 satukan)

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

    # Tahap 1 — bentuk badan usaha di EKOR nama. Satu pihak bisa muncul
    # dalam dua urutan di satu rekening koran yang sama.
    (['PT HINO FINANCE INDONESIA', 'HINO FINANCE INDONESIA PT'], 1,
     'PT HINO FINANCE INDONESIA'),
    (['PT. AEROFOOD INDONESIA', 'AEROFOOD INDONESIA, PT'], 1,
     'PT. AEROFOOD INDONESIA'),
    (['CV CIPTA SAUDARA', 'CIPTA SAUDARA CV', 'CIPTA SAUDARA'], 2,
     'CV CIPTA SAUDARA'),

    # Tahap 1 — sapaan di depan nama orang. Pasangan "Ibu ANI ROHIMAH" ada
    # di data referensi; nama yang ditampilkan tetap apa adanya.
    (['Ibu ANI ROHIMAH', 'ANI ROHIMAH'], 1, 'Ibu ANI ROHIMAH'),
    (['Sdr ROLAND GAROS HUTABARAT', 'ROLAND GAROS HUTABARAT'], 1,
     'Sdr ROLAND GAROS HUTABARAT'),

    # Tahap 2 — rantai potongan bertingkat (lihat juga
    # periksa_rantai_tidak_teracuni).
    (['PT AEROTRANS SERVICES IND', 'PT AEROTRANS SERVICES INDON',
      'PT AEROTRANS SERVICES INDONESIA'], 2,
     'PT AEROTRANS SERVICES INDONESIA'),

    # Tahap 3 — kemiripan huruf. Potongan di TENGAH teks gabungan: karena
    # ada "/BCA" menempel, kunci potongannya bukan awalan dari kunci nama
    # penuhnya, jadi tahap 2 memang tidak bisa melihatnya. Inilah satu-satunya
    # bentuk yang terbukti hanya bisa diberikan kemiripan huruf.
    (['INDOMOBIL FINANCE INDONE/BCA', 'PT INDOMOBIL FINANCE INDONESIA/BCA'], 1,
     'PT INDOMOBIL FINANCE INDONESIA/BCA'),

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

# ── Celah yang DIKETAHUI dan diukur, bukan diabaikan ─────────────────────
# Gelar akademik di ekor nama. Keduanya hampir pasti orang yang sama, tapi
# bentuk selisihnya — kata utuh yang ditambahkan — tidak bisa dibedakan dari
# "MIRNA HASANAH" vs "MIRNA HASANAH KOTO", yang bisa dua orang. Yang
# membedakannya hanya pengetahuan bahwa "S.Si" itu gelar dan "KOTO" itu nama
# keluarga, dan pengetahuan itu belum ada di kode.
#
# Jadi keduanya sengaja TIDAK digabung, dan dilaporkan sebagai kandidat.
# Tes ini menjaga keadaan itu tetap DISENGAJA: kalau suatu hari daftar gelar
# ditambahkan, tes ini yang gagal lebih dulu dan mengingatkan bahwa
# keputusannya berubah. Besar celahnya terukur di tests/palsu.py (pola
# "gelar ditambah").
BELUM_DITANGANI_GELAR = [
    ['DUDUNG MULYADI', 'DUDUNG MULYADI, M.'],
    ['Lili Muniri S', 'Lili Muniri S Si'],
    ['Sagirin', 'SAGIRIN, ST'],
]

# ── Pasangan bernilai kemiripan TINGGI yang tetap harus terpisah ─────────
# Semuanya dari data referensi, dan semuanya bernilai 0.80-0.92 — lebih
# tinggi daripada potongan mesin yang benar-benar satu pihak. Inilah yang
# dijaga tahap 4 (validasi konteks); tanpa itu, menurunkan ambang gabung
# otomatis akan meleburnya diam-diam dan HHI Score ikut salah.
MIRIP_TAPI_BEDA = [
    # Nominal topup berbeda — nilainya 0.91, tertinggi di antara semuanya.
    ['FLAZZ BCA TOPUP08111441280 200,000.00',
     'FLAZZ BCA TOPUP08111441280 300,000.00'],
    # Nomor kontrak berbeda.
    ['DEXTRATAMA NITYA SANJAYA PT - HT002',
     'DEXTRATAMA NITYA SANJAYA PT - HT003'],
    # Keterangan transaksi menempel di ekor nama.
    ['ERWINSYAH HARAHAP', 'ERWINSYAH HARAHAP THR'],
    ['PANUSUNAN ALAMSAH SRG', 'PANUSUNAN ALAMSAH SRG DP'],
    # Dua badan usaha berbeda yang berawalan dan berakhiran sama.
    ['TIGA BERSAMA LOGISTIK PT', 'TIGA PERMATA LOGISTIK PT'],
    # Inisial satu huruf: bisa orang yang sama, bisa dua orang.
    ['Sdr M ABDINTA TARIGAN', 'Sdr ABDINTA TARIGAN'],
    # Gelar di ekor nama orang ("S.Pd"), bukan bentuk badan usaha.
    ['Armanto S Pd', 'Armanto Suprapto'],
    # Nama orang lawan badan usaha — tidak dibandingkan sama sekali.
    ['PT HINO FINANCE INDONESIA', 'Sdr HENDRI'],
    # Berhenti di batas kata, dan nilainya di rentang 0.85-an: ikut tergabung
    # kalau ambang gabung diturunkan ke 0.85/0.80, jadi dijaga di sini.
    ['Depari Mujeham Naska', 'Depari Mujeham Naska Pratama'],
    ['MIRNA HASANAH', 'MIRNA HASANAH KOTO'],
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


def periksa_mirip_tapi_beda() -> list:
    """Nilai kemiripan tinggi tidak boleh cukup untuk menggabungkan."""
    masalah = []
    for nama in MIRIP_TAPI_BEDA:
        h = satukan(nama)
        if h.jumlah_digabung:
            masalah.append(f'{nama} TERGABUNG padahal kemiripan tinggi saja '
                           f'tidak cukup — varian: {h.varian}')
    return masalah


def periksa_rantai_tidak_teracuni() -> list:
    """
    Satu saudara yang bukan varian nama tidak boleh membatalkan keluarganya.

    Kasus nyata dari references/: "PT AEROTRANS SERVICES IND", "...INDON",
    dan "...INDONESIA" adalah satu rantai potongan mesin, tapi di berkas yang
    sama ada "AEROTRANS SERVICES INDON PINBUK KE BNI OPS" — nama berekor
    keterangan transaksi. Dulu kehadirannya membuat ketiganya ikut dianggap
    "cocok ke beberapa nama yang berbeda", sehingga satu pihak tetap pecah
    jadi beberapa baris Rekap dan HHI ikut salah.

    Sekarang yang berekor keterangan itu disingkirkan lebih dulu (ekornya
    menyambung di BATAS KATA, dan "PINBUK" adalah keterangan transaksi), lalu
    sisanya menyatu. Yang disingkirkan wajib muncul sebagai kandidat.
    """
    rantai = ['PT AEROTRANS SERVICES IND', 'PT AEROTRANS SERVICES INDON',
              'PT AEROTRANS SERVICES INDONESIA']
    asing = 'AEROTRANS SERVICES INDON PINBUK KE BNI OPS'
    h = satukan(rantai + [asing])
    masalah = []
    wakil = {h(n) for n in rantai}
    if wakil != {'PT AEROTRANS SERVICES INDONESIA'}:
        masalah.append(f'rantai potongan tidak menyatu: {sorted(wakil)}')
    if h(asing) != asing:
        masalah.append(f'{asing!r} ikut dilebur jadi {h(asing)!r}, padahal '
                       f'ekornya keterangan transaksi')
    if not any(asing in (a, b) for a, b, _ in h.kandidat):
        masalah.append(f'{asing!r} tidak digabung TAPI juga tidak dilaporkan '
                       f'sebagai kandidat')
    return masalah


def periksa_tidak_menjembatani() -> list:
    """
    Penggabungan tidak boleh MENJEMBATANI dua nama yang tidak pernah lolos
    syaratnya sendiri.

    Penggabungan bersifat menular: A ke B dan B ke C membuat A dan C berakhir
    di satu baris Rekap. Kalau pasangan A-C tidak pernah diuji, satu baris
    Rekap bisa memuat dua pihak berbeda — bahayanya sama dengan salah gabung
    biasa, dan sama tidak kelihatannya.

    Ketiga nama di bawah dipilih supaya persis itu yang terjadi kalau
    pagarnya tidak ada: A-B lolos (spasi tersisip), B-C lolos (kata
    terpotong), tapi A-C TIDAK — selisihnya memuat "DONESIA" yang tidak
    berpasangan dengan kata mana pun di sisi lain.
    """
    a = 'PT HARAPAN JAYA SEJAHTERA INDONESIA'
    b = 'PT HARAPAN JAYA SEJAHTERA I DONESIA'
    c = 'PT HARAPAN JAYA SEJAHTER I DONESIA'
    h = satukan([a, b, c])
    masalah = []
    if h(a) == h(c):
        masalah.append(f'{a!r} dan {c!r} berakhir satu kelompok ({h(a)!r}) '
                       f'lewat {b!r} — padahal pasangannya sendiri tidak '
                       f'lolos syarat apa pun')
    if not h.kandidat:
        masalah.append('penggabungan ditahan karena jembatan, tapi tidak '
                       'dilaporkan sebagai kandidat')
    # Hasilnya tidak boleh bergantung urutan masukan.
    if {n: h(n) for n in (a, b, c)} != {n: satukan([c, b, a])(n)
                                        for n in (a, b, c)}:
        masalah.append('hasil berubah saat urutan masukan dibalik')
    return masalah


def periksa_uraian_asli_utuh() -> list:
    """
    Sapaan dibuang hanya untuk MEMBANDINGKAN, tidak dari nama yang
    ditampilkan. Uraian yang tercetak di dokumen adalah bukti, dan pemeriksa
    berhak melihatnya apa adanya.
    """
    nama = ['Sdr ROLAND GAROS HUTABARAT', 'ROLAND GAROS HUTABARAT']
    h = satukan(nama)
    masalah = []
    if set(h.varian.get(h(nama[0]), [])) != set(nama):
        masalah.append(f'varian tidak memuat kedua penulisan asli: {h.varian}')
    if 'Sdr' not in h(nama[0]):
        masalah.append(f'wakil kelompok {h(nama[0])!r} sudah kehilangan '
                       f'sapaan yang tercetak di dokumen')
    return masalah


def periksa_celah_gelar() -> list:
    """Gelar akademik belum ditangani — dan ketidaktanganannya disengaja."""
    masalah = []
    for nama in BELUM_DITANGANI_GELAR:
        h = satukan(nama)
        if h.jumlah_digabung:
            masalah.append(
                f'{nama} TERGABUNG. Kalau daftar gelar memang baru '
                f'ditambahkan, pindahkan kasus ini ke HARUS_MENYATU dan '
                f'perbarui catatan celahnya di tests/palsu.py')
        if not h.kandidat:
            masalah.append(f'{nama} tidak digabung TAPI juga tidak '
                           f'dilaporkan sebagai kandidat')
    return masalah


def periksa_pemotongan_kandidat_dilaporkan() -> list:
    """
    Kalau daftar kandidat dipotong karena batas cetak, jumlahnya wajib
    dilaporkan.

    Ini kegagalan yang paling mudah luput: laporannya tetap terlihat rapi,
    daftarnya tetap terisi, dan pemeriksa menyimpulkan itu daftar lengkap.
    Pada data referensi sekarang ada satu laporan yang jumlah kandidatnya
    TEPAT di batas, jadi satu nama baru saja sudah cukup memicunya.
    """
    # Nomor invoice yang berbeda: kemiripannya tinggi, tapi validasi konteks
    # menolaknya (angka berbeda), jadi semuanya jatuh ke daftar kandidat.
    nama = [f'PEMBAYARAN INVOICE {i:04d} KEPADA MITRA UTAMA' for i in range(12)]
    h = satukan(nama)
    masalah = []
    if h.jumlah_digabung:
        masalah.append(f'nomor invoice berbeda ikut tergabung: {h.varian}')
    if len(h.kandidat) > MAKS_KANDIDAT_FUZZY:
        masalah.append(f'daftar kandidat {len(h.kandidat)} baris, melewati '
                       f'batas {MAKS_KANDIDAT_FUZZY} tanpa dipotong')
    if not h.kandidat_dipotong:
        masalah.append(f'{len(nama)} nama menghasilkan {len(h.kandidat)} '
                       f'kandidat tercetak tapi kandidat_dipotong = '
                       f'{h.kandidat_dipotong} — pemotongannya senyap')
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
        ('mirip tapi pihak berbeda', periksa_mirip_tapi_beda),
        ('celah gelar akademik masih disengaja', periksa_celah_gelar),
        ('penggabungan tidak menjembatani', periksa_tidak_menjembatani),
        ('satu saudara asing tidak meracuni rantai potongan',
         periksa_rantai_tidak_teracuni),
        ('uraian asli tetap utuh', periksa_uraian_asli_utuh),
        ('pemotongan daftar kandidat dilaporkan',
         periksa_pemotongan_kandidat_dilaporkan),
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
