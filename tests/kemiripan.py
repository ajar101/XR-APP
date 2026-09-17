"""
kemiripan.py — Tes pengukuran kemiripan nama entitas.

    python tests/kemiripan.py

Kenapa perlu tes sendiri, terpisah dari tests/penyatuan.py: yang diuji di
sini bukan "apa yang digabung" melainkan apakah ANGKANYA jujur dan apakah
PENOLAKANNYA bekerja. Dua-duanya gagal secara senyap. Nilai kemiripan yang
salah kalibrasi tetap menghasilkan laporan yang terlihat rapi, dan satu
aturan veto yang mati membuat topup kartu dengan nominal berbeda melebur
jadi satu "lawan transaksi" — lalu HHI Score salah tanpa jejak.

Kasusnya diambil dari data nyata (44 PDF referensi) dan dari contoh yang
memang ingin dipastikan perilakunya.

Keluar dengan kode 1 kalau ada yang meleset, jadi bisa dipakai di CI.
"""

import os
import sys

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from engine.kemiripan_entitas import (     # noqa: E402
    AMBANG,
    COMPANY,
    GABUNG,
    PERSON,
    PISAH,
    TINGKAT_PISAH,
    TINGKAT_TINGGI,
    TINGKAT_TINJAU,
    UNKNOWN,
    AmbangKemiripan,
    boleh_dibandingkan,
    bukti_potongan,
    calculate_entity_similarity,
    klasifikasi_entitas,
    kunci_banding,
    layak_dihitung,
    tingkat_dari_nilai,
    validasi_konteks,
)

MEDAN = ('overall_score', 'character_score', 'token_score', 'prefix_score',
         'length_score')


def periksa_bentuk_keluaran() -> list:
    """Kelima nilai harus selalu ada, selalu angka, selalu di rentang 0..1."""
    masalah = []
    for a, b in (('PT BARASENTOSA LESTARI', 'BARASENTOSA LES'),
                 ('', 'PT APA SAJA'),
                 ('PT A', 'PT A'),
                 ('Sdr HENDRI', 'PT HINO FINANCE INDONESIA')):
        hasil = calculate_entity_similarity(a, b).as_dict()
        if set(hasil) != set(MEDAN):
            masalah.append(f'{a!r}/{b!r} → medan {sorted(hasil)}, '
                           f'harusnya {sorted(MEDAN)}')
            continue
        for nama, nilai in hasil.items():
            if not isinstance(nilai, float) or not 0.0 <= nilai <= 1.0:
                masalah.append(f'{a!r}/{b!r} → {nama} = {nilai!r}, '
                               f'harusnya angka 0..1')
    return masalah


def periksa_simetri() -> list:
    """
    Urutan argumen tidak boleh mengubah apa pun.

    Kalau kemiripan(a,b) != kemiripan(b,a), hasil penyatuan bisa berubah
    hanya karena urutan nama di dalam laporan berubah — dan laporan yang
    sama wajib menghasilkan berkas yang sama.
    """
    pasangan = [('PT AEROFOOD INDONESIA', 'AEROFOOD INDONESIA, PT'),
                ('BARASENTOSA LES', 'PT BARASENTOSA LESTARI'),
                ('TIGA BERSAMA LOGISTIK PT', 'TIGA PERMATA LOGISTIK PT'),
                ('Sdr M ABDINTA TARIGAN', 'Sdr ABDINTA TARIGAN')]
    return [f'kemiripan({a!r},{b!r}) tidak simetris'
            for a, b in pasangan
            if calculate_entity_similarity(a, b).as_dict()
            != calculate_entity_similarity(b, a).as_dict()]


def periksa_normalisasi() -> list:
    """
    Perbedaan yang punya aturan pasti harus hilang SEBELUM diukur.

    Ini yang membuat tahap 1 bisa menuntaskan kasusnya tanpa fuzzy sama
    sekali: kalau kuncinya sudah sama, tidak ada yang perlu ditebak.
    """
    sama = [
        # Bentuk badan usaha di depan atau di belakang.
        ('PT HINO FINANCE INDONESIA', 'HINO FINANCE INDONESIA PT'),
        ('PT. AEROFOOD INDONESIA', 'AEROFOOD INDONESIA, PT'),
        ('CV CIPTA SAUDARA', 'CIPTA SAUDARA CV'),
        # Sapaan di depan nama orang.
        ('Sdr ROLAND GAROS HUTABARAT', 'ROLAND GAROS HUTABARAT'),
        ('Ibu ANI ROHIMAH', 'ANI ROHIMAH'),
        ('Bpk. SUCIPTO', 'SUCIPTO'),
        # Spasi dan tanda baca.
        ('PT WIRAKARYA SAKTI', 'PT WIRA KARYA SAKTI'),
    ]
    beda = [
        # Huruf bentuk badan usaha tanpa pemisah bukan bentuk badan usaha.
        ('PTX SEJAHTERA', 'X SEJAHTERA'),
        ('CVITO MANDIRI', 'ITO MANDIRI'),
        # "PD" di EKOR nama orang adalah gelar (Sarjana Pendidikan), bukan
        # Perusahaan Daerah. Membuangnya berarti memotong nama orang.
        ('Armanto S Pd', 'Armanto S'),
        ('MUHAMMAD ILHAM FA', 'MUHAMMAD ILHAM'),
    ]
    masalah = [f'kunci({a!r}) != kunci({b!r}): {kunci_banding(a)!r} vs '
               f'{kunci_banding(b)!r}'
               for a, b in sama if kunci_banding(a) != kunci_banding(b)]
    masalah += [f'kunci({a!r}) == kunci({b!r}) = {kunci_banding(a)!r}, '
                f'padahal harus berbeda'
                for a, b in beda if kunci_banding(a) == kunci_banding(b)]
    return masalah


def periksa_golongan() -> list:
    """Penggolongan entitas, dan pasangan yang tidak boleh dibandingkan."""
    kasus = [('PT HINO FINANCE INDONESIA', COMPANY),
             ('AEROFOOD INDONESIA, PT', COMPANY),
             ('CIPTA SAUDARA CV', COMPANY),
             ('BUKIT ASAM (PERSERO)', COMPANY),
             ('Sdr HENDRI', PERSON),
             ('Ibu ANI ROHIMAH', PERSON),
             ('MAHYONO', UNKNOWN),
             # Potongan mesin yang sudah kehilangan penanda golongannya.
             # UNKNOWN di sini bukan kegagalan melainkan jawaban jujur.
             ('BARASENTOSA LES', UNKNOWN),
             # Masih tertinggal satu kata penanda, jadi masih bisa
             # disimpulkan walau "PT"-nya sudah terpotong.
             ('AEROTRANS SERVICES I', COMPANY)]
    masalah = [f'klasifikasi_entitas({n!r}) = {klasifikasi_entitas(n)}, '
               f'harusnya {t}' for n, t in kasus if klasifikasi_entitas(n) != t]

    # COMPANY lawan PERSON tidak dibandingkan — berapa pun nilainya.
    if boleh_dibandingkan('PT HINO FINANCE INDONESIA', 'Sdr HENDRI'):
        masalah.append('COMPANY vs PERSON masih dibandingkan')
    hasil = calculate_entity_similarity('PT HINO FINANCE INDONESIA',
                                        'Sdr HENDRI')
    if hasil.tindakan != PISAH or hasil.sebanding:
        masalah.append(f'PT HINO vs Sdr HENDRI → {hasil.tindakan}, harus PISAH')
    if layak_dihitung('PT HINO FINANCE INDONESIA', 'Sdr HENDRI'):
        masalah.append('PT HINO vs Sdr HENDRI masih masuk perhitungan')

    # UNKNOWN tetap boleh bertemu keduanya: potongan mesin sering justru
    # membuang bagian yang menandai golongan.
    for a, b in (('BARASENTOSA LES', 'PT BARASENTOSA LESTARI'),
                 ('MAHYONO', 'Sdr MAHYONO')):
        if not boleh_dibandingkan(a, b):
            masalah.append(f'{a!r} vs {b!r} ditolak, padahal UNKNOWN boleh')
    return masalah


def periksa_tingkat() -> list:
    """
    Pemetaan nilai → tingkat, dan bahwa ambangnya benar-benar bisa disetel
    tanpa menyentuh algoritmanya.

    Batasnya diambil dari AMBANG, bukan ditulis ulang di sini: yang diuji
    adalah PERILAKU pemetaannya, dan menyetel ambang tidak boleh
    menggagalkan tes ini. Nilai bawaannya sendiri diperiksa terpisah di
    bawah, karena angka itu hasil pengukuran (`tests/ambang.py`) dan tidak
    boleh bergeser tanpa sengaja.
    """
    masalah = []
    if (AMBANG.tinggi, AMBANG.mungkin, AMBANG.tinjau) != (0.90, 0.85, 0.80):
        masalah.append(
            f'ambang bawaan {AMBANG.tinggi}/{AMBANG.mungkin}/{AMBANG.tinjau} '
            f'bukan 0.90/0.85/0.80 — kalau ini disengaja, ukur dulu dengan '
            f'tests/ambang.py lalu perbarui angka di tes ini')

    eps = 1e-4
    for nilai, harap in ((1.00, TINGKAT_TINGGI),
                         (AMBANG.tinggi, TINGKAT_TINGGI),
                         (AMBANG.tinggi - eps, 'PROBABLE_MATCH'),
                         (AMBANG.mungkin, 'PROBABLE_MATCH'),
                         (AMBANG.mungkin - eps, TINGKAT_TINJAU),
                         (AMBANG.tinjau, TINGKAT_TINJAU),
                         (AMBANG.tinjau - eps, TINGKAT_PISAH),
                         (0.0, TINGKAT_PISAH)):
        if tingkat_dari_nilai(nilai) != harap:
            masalah.append(f'nilai {nilai} → {tingkat_dari_nilai(nilai)}, '
                           f'harusnya {harap}')

    # Setelan awal yang dipakai sebelum diukur harus tetap berlaku kalau
    # diberikan eksplisit — itu inti "configurable".
    awal = AmbangKemiripan(tinggi=0.95, mungkin=0.90, tinjau=0.80)
    for nilai, harap in ((0.96, TINGKAT_TINGGI), (0.92, 'PROBABLE_MATCH'),
                         (0.87, TINGKAT_TINJAU), (0.70, TINGKAT_PISAH)):
        if tingkat_dari_nilai(nilai, awal) != harap:
            masalah.append(f'pada setelan awal 0.95/0.90/0.80, nilai {nilai} '
                           f'→ {tingkat_dari_nilai(nilai, awal)}, harusnya '
                           f'{harap}')

    # Ambang yang disetel harus mengubah keputusan, bukan diabaikan.
    a, b = 'BARASENTOSA LES', 'PT BARASENTOSA LESTARI'
    bawaan = calculate_entity_similarity(a, b)
    longgar = calculate_entity_similarity(a, b, AmbangKemiripan(
        tinggi=0.70, mungkin=0.65, tinjau=0.60))
    if bawaan.tingkat != TINGKAT_PISAH:
        masalah.append(f'{a!r} vs {b!r} pada ambang bawaan → '
                       f'{bawaan.tingkat}, harusnya SEPARATE')
    if longgar.tindakan != GABUNG:
        masalah.append(f'{a!r} vs {b!r} pada ambang longgar → '
                       f'{longgar.tindakan}, harusnya GABUNG')
    if bawaan.overall_score != longgar.overall_score:
        masalah.append('menyetel ambang mengubah NILAI, padahal hanya boleh '
                       'mengubah penggolongannya')
    return masalah


def periksa_bukti_potongan() -> list:
    """
    Bukti potongan harus membedakan hal yang AMBANG TIDAK BISA membedakan.

    Ini inti perubahan dari "gabung karena nilainya tinggi" ke "gabung karena
    ada buktinya". Pasangan di bawah dipilih justru karena nilainya
    berdekatan — kalau yang memisahkannya kembali cuma selisih beberapa
    perseratus dari ambang, tes ini yang gagal.
    """
    berbukti = [
        # Kata yang sama, satu terpotong.
        ('GARUDA INDONESI', 'PT GARUDA INDONESIA'),
        ('INDOMOBIL FINANCE INDONE/BCA', 'PT INDOMOBIL FINANCE INDONESIA/BCA'),
        ('BARASENTOSA LES', 'PT BARASENTOSA LESTARI'),
        # Spasi tersisip di tengah kata.
        ('PT AEROTRANS SERVICES I DONESIA', 'PT AEROTRANS SERVICES INDONESIA'),
        ('Bennyekaputra', 'Benny Eka Putra'),
    ]
    tanpa_bukti = [
        # Kata utuh yang ditambahkan — bisa nama keluarga, bisa keterangan.
        ('MIRNA HASANAH', 'MIRNA HASANAH KOTO'),
        ('Depari Mujeham Naska', 'Depari Mujeham Naska Pratama'),
        ('Wulan Permata', 'Wulan Permata Sar'),
        ('DUDUNG MULYADI', 'DUDUNG MULYADI, M.'),
        # Kata yang berbeda tidak saling berawalan — dua pihak berbeda.
        ('TIGA BERSAMA LOGISTIK PT', 'TIGA PERMATA LOGISTIK PT'),
        # SATU pasangan kata yang berawalan tidak cukup: kalau cukup satu,
        # pasangan ini lolos lewat INDONESIA/INDONESI padahal ABC dan XYZ
        # tidak ada hubungannya.
        ('PT ABC INDONESIA', 'PT XYZ INDONESI'),
        # Gelar DITAMBAHKAN: jumlah kata berbeda dan kuncinya beda satu
        # huruf, sama seperti spasi tersisip — yang membedakan, kuncinya
        # BERTAMBAH panjang, bukan berkurang.
        ('Sagirin', 'SAGIRIN, ST'),
        ('AJL LOGISTIK INDONESIA', 'PT TAPANULI LOGISTIK INDONESIA'),
        ('PT GARUDA INDONESIA CARGO', 'PT GARUDA INDONESIA'),
        ('93497004099102 PT HAIER SALES INDONESIA - 087',
         '93497004099102 PT IRAWAN SALES INDONESIA - 087'),
        ('NI WAYAN ARTINI', 'NI WAYAN SUGIARTINI'),
    ]
    masalah = [f'{a!r} vs {b!r} dianggap TANPA bukti potongan '
               f'({bukti_potongan(a, b)[1]})'
               for a, b in berbukti if not bukti_potongan(a, b)[0]]
    masalah += [f'{a!r} vs {b!r} dianggap BERBUKTI potongan '
                f'({bukti_potongan(a, b)[1]}) — padahal selisihnya kata utuh'
                for a, b in tanpa_bukti if bukti_potongan(a, b)[0]]

    # Tiap bukti maupun penolakan wajib punya keterangan yang bisa dibaca —
    # keterangan itu yang tercetak di kolom alasan pada daftar kandidat.
    for a, b in berbukti + tanpa_bukti:
        if not bukti_potongan(a, b)[1].strip():
            masalah.append(f'{a!r} vs {b!r} tanpa keterangan')
    return masalah


def periksa_ambang_terukur() -> list:
    """
    Pasangan yang penggabungannya bergantung pada ambang, dikunci supaya
    hasil pengukuran (tests/ambang.py, tests/palsu.py) tidak hilang.

    Yang dijaga bukan angka ambangnya, melainkan: pasangan berbukti masih
    boleh digabung, dan pasangan tanpa bukti masih tertahan — apa pun
    ambangnya.
    """
    boleh = [('INDOMOBIL FINANCE INDONE/BCA',
              'PT INDOMOBIL FINANCE INDONESIA/BCA')]
    tertahan = [('Depari Mujeham Naska', 'Depari Mujeham Naska Pratama'),
                ('MIRNA HASANAH', 'MIRNA HASANAH KOTO'),
                ('TIGA BERSAMA LOGISTIK PT', 'TIGA PERMATA LOGISTIK PT')]
    masalah = []
    for a, b in boleh:
        h = calculate_entity_similarity(a, b)
        if h.tindakan not in (GABUNG, 'GABUNG_BILA_KONTEKS'):
            masalah.append(f'{a!r} vs {b!r} → {h.tindakan} pada nilai '
                           f'{h.overall_score:.3f}, harusnya boleh digabung')
        elif not (bukti_potongan(a, b)[0] and validasi_konteks(a, b)[0]):
            masalah.append(f'{a!r} vs {b!r} lolos ambang tapi tertahan '
                           f'bukti/konteks')
    for a, b in tertahan:
        h = calculate_entity_similarity(a, b)
        lolos = (h.tindakan in (GABUNG, 'GABUNG_BILA_KONTEKS')
                 and bukti_potongan(a, b)[0] and validasi_konteks(a, b)[0])
        if lolos:
            masalah.append(f'{a!r} vs {b!r} BOLEH digabung pada nilai '
                           f'{h.overall_score:.3f}, padahal tanpa bukti '
                           f'potongan')
    return masalah


def periksa_veto_konteks() -> list:
    """
    Nilai tinggi tanpa bukti konteks tidak boleh menjadi penggabungan.

    Semua pasangan di bawah ini benar-benar ada di data referensi dan
    semuanya bernilai tinggi. Kalau satu aturan veto mati, pasangan itulah
    yang melebur diam-diam.
    """
    harus_ditolak = [
        # Nominal berbeda — nilainya 0.91, tertinggi di antara semua contoh
        # di sini, dan justru yang paling jelas bukan pihak yang sama.
        ('FLAZZ BCA TOPUP08111441280 200,000.00',
         'FLAZZ BCA TOPUP08111441280 300,000.00'),
        # Nomor kontrak berbeda.
        ('DEXTRATAMA NITYA SANJAYA PT - HT002',
         'DEXTRATAMA NITYA SANJAYA PT - HT003'),
        # Keterangan transaksi menempel di ekor nama.
        ('ERWINSYAH HARAHAP', 'ERWINSYAH HARAHAP THR'),
        ('PANUSUNAN ALAMSAH SRG', 'PANUSUNAN ALAMSAH SRG DP'),
        ('AEROTRANS SERVICES INDON PINBUK KE BNI OPS',
         'PT AEROTRANS SERVICES INDONESIA'),
        # Inisial satu huruf — bisa orang yang sama, bisa dua orang.
        ('Sdr M ABDINTA TARIGAN', 'Sdr ABDINTA TARIGAN'),
        # Golongan bertentangan.
        ('PT HINO FINANCE INDONESIA', 'Sdr HENDRI'),
    ]
    harus_lolos = [
        ('PT HINO FINANCE INDONESIA', 'HINO FINANCE INDONESIA PT'),
        ('Sdr ROLAND GAROS HUTABARAT', 'ROLAND GAROS HUTABARAT'),
        # Huruf tunggal yang ternyata sisa kata terpotong, bukan inisial.
        ('PT TEBO MULTI A', 'PT TEBO MULTI AGRO'),
    ]
    masalah = [f'{a!r} vs {b!r} LOLOS validasi konteks, padahal harus ditolak'
               for a, b in harus_ditolak if validasi_konteks(a, b)[0]]
    masalah += [f'{a!r} vs {b!r} DITOLAK ({validasi_konteks(a, b)[1]}), '
                f'padahal harus lolos'
                for a, b in harus_lolos if not validasi_konteks(a, b)[0]]

    # Tiap penolakan wajib membawa alasan yang bisa dibaca manusia — alasan
    # itu yang dicetak di daftar KANDIDAT pada sheet Rekap.
    for a, b in harus_ditolak:
        if not validasi_konteks(a, b)[1].strip():
            masalah.append(f'{a!r} vs {b!r} ditolak tanpa alasan')
    return masalah


def periksa_urutan_terbalik() -> list:
    """
    Pagar atas temuan yang menentukan seluruh rancangan ini: pada data nyata,
    peringkat kemiripan TERBALIK terhadap kebenaran.

    Pasangan yang harus tetap terpisah bernilai LEBIH TINGGI daripada
    potongan mesin yang benar-benar satu pihak. Selama itu masih benar,
    menurunkan ambang gabung otomatis adalah ide buruk — dan tes ini yang
    akan memberitahu kalau suatu hari keadaannya berubah.
    """
    beda_pihak = calculate_entity_similarity(
        'FLAZZ BCA TOPUP08111441280 200,000.00',
        'FLAZZ BCA TOPUP08111441280 300,000.00').overall_score
    satu_pihak = calculate_entity_similarity(
        'BARASENTOSA LES', 'PT BARASENTOSA LESTARI').overall_score
    if beda_pihak <= satu_pihak:
        return [f'peringkat tidak lagi terbalik (beda pihak {beda_pihak:.3f} '
                f'<= satu pihak {satu_pihak:.3f}) — periksa ulang catatan '
                f'kalibrasi di engine/penyatu_nama.py sebelum mengubah ambang']
    return []


def periksa_saringan_tidak_menghilangkan() -> list:
    """
    `layak_dihitung` cuma boleh melewati pasangan yang MUSTAHIL lolos.

    Kalau batas atasnya keliru, pasangan yang seharusnya masuk daftar
    kandidat hilang tanpa jejak — dan itu justru kegagalan yang tidak
    kelihatan di laporan. Diuji dengan cara paling kasar: hitung semua
    pasangan, bandingkan dengan yang diloloskan saringan.
    """
    nama = ['PT BARASENTOSA LESTARI', 'BARASENTOSA LESTARI', 'BARASENTOSA LES',
            'PT WIRAKARYA SAKTI', 'PT WIRA KARYA SAKTI', 'CITRA PERISAI',
            'CITRA PERISAI LINTASINDO', 'TIGA BERSAMA LOGISTIK PT',
            'TIGA PERMATA LOGISTIK PT', 'AEROFOOD INDONESIA, PT',
            'PT. AEROFOOD INDONESIA', 'Sdr M ABDINTA TARIGAN',
            'Sdr ABDINTA TARIGAN', 'Ibu ANI ROHIMAH', 'ANI ROHIMAH',
            'MAHYONO', 'PT AEROTRANS SERVICES IND',
            'PT AEROTRANS SERVICES INDON', 'PT AEROTRANS SERVICES INDONESIA',
            'FLAZZ BCA TOPUP08111441280 200,000.00',
            'FLAZZ BCA TOPUP08111441280 300,000.00']
    masalah = []
    for i, a in enumerate(nama):
        for b in nama[i + 1:]:
            if not boleh_dibandingkan(a, b):
                continue
            nilai = calculate_entity_similarity(a, b).overall_score
            if nilai >= AMBANG.tinjau and not layak_dihitung(a, b):
                masalah.append(f'saringan membuang {a!r} vs {b!r} padahal '
                               f'nilainya {nilai:.3f}')
    return masalah


def main() -> int:
    masalah = []
    for nama, fungsi in (
        ('kelima nilai keluaran lengkap dan wajar', periksa_bentuk_keluaran),
        ('pengukuran simetris', periksa_simetri),
        ('normalisasi sapaan dan bentuk badan usaha', periksa_normalisasi),
        ('penggolongan entitas dan pasangan yang ditolak', periksa_golongan),
        ('tingkat keyakinan dan ambang yang bisa disetel', periksa_tingkat),
        ('validasi konteks membatalkan nilai tinggi', periksa_veto_konteks),
        ('bukti potongan membedakan yang ambang tidak bisa',
         periksa_bukti_potongan),
        ('ambang hasil pengukuran masih berlaku', periksa_ambang_terukur),
        ('peringkat kemiripan masih terbalik pada data nyata',
         periksa_urutan_terbalik),
        ('saringan tidak menghilangkan pasangan',
         periksa_saringan_tidak_menghilangkan),
    ):
        hasil = fungsi()
        print(f'{"BEDA" if hasil else "OK  "}  {nama}')
        masalah += hasil

    if masalah:
        print('\n' + '\n'.join(f'  - {m}' for m in masalah))
        print(f'\nRingkasan: {len(masalah)} masalah.')
        return 1
    print('\nRingkasan: pengukuran kemiripan entitas berperilaku benar.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
