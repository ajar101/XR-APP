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

    # Tahap 1b — karakter ke-24 dikosongkan dokumen. Keempat ejaan ini nyata,
    # dari satu berkas BNI: dua utuh, dua rusak di posisi yang sama. Yang
    # diuji bukan cuma mereka menyatu, tapi WAKILNYA ejaan yang utuh —
    # "HINO FINANCE INDONESIA T" punya kunci terpanjang justru karena huruf
    # yang hilang, jadi tanpa pagar ia yang menang.
    (['PT HINO FINANCE INDONESIA', 'PT HINO FINANCE INDONES A',
      'HINO FINANCE INDONESIA PT', 'HINO FINANCE INDONESIA T'], 3,
     'PT HINO FINANCE INDONESIA'),
    (['CV. SURYA SUMATERA SEJATI', 'CV. SURYA SUMATERA SEJA I'], 1,
     'CV. SURYA SUMATERA SEJATI'),
    (['PT MANDIRI CIPTA SOLUTION', 'PT MANDIRI CIPTA SOLUTI N'], 1,
     'PT MANDIRI CIPTA SOLUTION'),
    (['PT AEROTRANS SERVICES INDONESIA', 'PT AEROTRANS SERVICES I DONESIA'], 1,
     'PT AEROTRANS SERVICES INDONESIA'),
    (['PT. AEROFOOD INDONESIA', 'AEROFOOD INDONESIA, PT'], 1,
     'PT. AEROFOOD INDONESIA'),
    (['CV CIPTA SAUDARA', 'CIPTA SAUDARA CV', 'CIPTA SAUDARA'], 2,
     'CV CIPTA SAUDARA'),

    # Tahap 1 — gelar akademik di ekor nama, daftar tertutup.
    (['DUDUNG MULYADI', 'DUDUNG MULYADI, M.'], 1, 'DUDUNG MULYADI, M.'),
    (['Sagirin', 'SAGIRIN, ST'], 1, 'SAGIRIN, ST'),
    (['IIN RAJUDIN', 'IIN RAJUDIN,S.PD', 'IIN RAJUDIN, S.PD, M.M'], 2,
     'IIN RAJUDIN, S.PD, M.M'),

    # Tahap 1 — SATU NAMA, DUA KUNCI. Ketiga penulisan ini ada di satu
    # berkas referensi dan ketiganya satu orang. "SAGIRIN, ST" punya penanda
    # koma sehingga gelarnya bisa dipastikan; "SAGIRIN ST" tidak punya
    # penanda apa pun. Yang bergelar karena itu membawa dua kunci, dan ia
    # yang menjembatani keduanya.
    (['Sagirin', 'SAGIRIN, ST', 'SAGIRIN ST'], 2, 'SAGIRIN ST'),
    (['DINA MAULIDAH, S', 'DINA MAULIDAH S'], 1, 'DINA MAULIDAH S'),
    (['Lili Muniri S', 'Lili Muniri Ssi', 'Lili Muniri S Si'], 2,
     'Lili Muniri Ssi'),

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
# Gelar akademik sudah ditangani (daftar tertutup di engine/kemiripan_entitas.py),
# tapi hanya ketika gelarnya UTUH atau punya penanda. Yang tersisa: gelar
# yang ikut TERPOTONG dokumennya sampai tinggal satu huruf tanpa penanda
# apa pun.
#
#     Lili Muniri S      ← "S" ini sisa "S.Si"? atau inisial nama?
#     Lili Muniri S Si
#
# Membuang "S" di posisi itu berarti juga membuang ekor nama yang kebetulan
# satu huruf, dan tidak ada di teks yang bisa memutuskan mana. Di data
# referensi keduanya tetap menyatu karena ada penulisan ketiga ("Lili Muniri
# Ssi") yang menjembatani — tapi berdua saja, mereka tidak bisa.
BELUM_DITANGANI_GELAR = [
    ['Lili Muniri S', 'Lili Muniri S Si'],
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
    """
    Gelar yang ikut terpotong sampai tinggal satu huruf belum ditangani —
    dan ketidaktanganannya disengaja.
    """
    masalah = []
    for nama in BELUM_DITANGANI_GELAR:
        h = satukan(nama)
        if h.jumlah_digabung:
            masalah.append(
                f'{nama} TERGABUNG. Kalau penanganan gelar satu huruf memang '
                f'baru ditambahkan, pindahkan kasus ini ke HARUS_MENYATU dan '
                f'perbarui garis dasar di tests/palsu.py')
    return masalah


def periksa_gelar_tidak_memakan_potongan() -> list:
    """
    Daftar gelar tidak boleh memakan ekor nama yang kebetulan sama.

    "PT SUMBER SE" jauh lebih mungkin potongan "PT SUMBER SEJAHTERA"
    daripada gelar Sarjana Ekonomi, dan membuang "SE" di situ memotong nama
    pihak. Karena itu gelar dua huruf TANPA koma maupun titik sengaja tidak
    dibuang — dan pasangan di bawah wajib tetap menyatu lewat aturan
    potongan, bukan pecah karena normalisasi yang kelewat rajin.
    """
    masalah = []
    for nama, harap in ((['PT SUMBER SE', 'PT SUMBER SEJAHTERA'], 1),
                        (['MUHAMMAD ILHAM FA', 'MUHAMMAD ILHAM FAUZI'], 1)):
        h = satukan(nama)
        if h.jumlah_digabung != harap:
            masalah.append(f'{nama} → {h.jumlah_digabung} menyatu, harusnya '
                           f'{harap} — gelar memakan ekor nama?')
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


def periksa_rusak_tanpa_pasangan_dilaporkan() -> list:
    """
    Bentuk rusak yang tidak bisa dipulihkan wajib MUNCUL di daftar kandidat.

    Huruf yang hilang tidak tersimpan di mana pun, jadi tanpa ejaan utuh
    sebagai pembanding ia memang tidak bisa dipulihkan. Yang tidak boleh
    adalah diam: dibiarkan senyap, pemeriksa membaca "BRI MULTIFINANCE
    INDONE IA" sebagai nama yang memang begitu.

    Diuji sekalian kebalikannya — batas kata yang sah di posisi ke-24 TIDAK
    boleh ikut dilaporkan, karena daftar yang penuh alarm palsu berhenti
    dibaca.
    """
    masalah = []

    h = satukan(['BRI MULTIFINANCE INDONE IA', 'PT LAIN YANG TIDAK TERKAIT'])
    dilaporkan = [a for a, b, _ in h.kandidat if not b]
    if 'BRI MULTIFINANCE INDONE IA' not in dilaporkan:
        masalah.append('bentuk rusak tanpa pasangan tidak dilaporkan: '
                       f'{h.kandidat}')

    # Spasi di posisi ke-24 yang memang batas kata.
    h = satukan(['SIMSEM GI RTGS/ KLIRING CAB', 'SIMSEM ONLINE TRANSFER BNID'])
    palsu = [a for a, b, r in h.kandidat if not b and 'karakter ke-24' in r]
    if palsu:
        masalah.append(f'batas kata yang sah ikut dilaporkan rusak: {palsu}')

    # Sudah punya pasangan → pertanyaannya sudah terjawab, jangan dilaporkan.
    h = satukan(['PT MANDIRI CIPTA SOLUTION', 'PT MANDIRI CIPTA SOLUTI N'])
    ganda = [a for a, b, r in h.kandidat if not b and 'karakter ke-24' in r]
    if ganda:
        masalah.append(f'yang sudah menyatu masih dilaporkan rusak: {ganda}')

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
        ('celah gelar satu huruf masih disengaja', periksa_celah_gelar),
        ('gelar tidak memakan ekor nama yang terpotong',
         periksa_gelar_tidak_memakan_potongan),
        ('penggabungan tidak menjembatani', periksa_tidak_menjembatani),
        ('satu saudara asing tidak meracuni rantai potongan',
         periksa_rantai_tidak_teracuni),
        ('uraian asli tetap utuh', periksa_uraian_asli_utuh),
        ('pemotongan daftar kandidat dilaporkan',
         periksa_pemotongan_kandidat_dilaporkan),
        ('bentuk rusak tanpa pasangan dilaporkan',
         periksa_rusak_tanpa_pasangan_dilaporkan),
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
