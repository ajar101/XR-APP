"""
kemiripan_entitas.py — Ukur kemiripan dua nama lawan transaksi, dan putuskan
seberapa jauh angka itu boleh dipercaya.

Modul ini HANYA MENGUKUR dan MENGGOLONGKAN. Yang memutuskan penggabungan
adalah `engine/penyatu_nama.py`, dan fuzzy adalah tahap TERAKHIR di sana —
sesudah normalisasi dan sesudah aturan potongan. Pemisahan ini penting:
angka kemiripan mudah dipercaya berlebihan, sedangkan akibat salah gabung
tidak kelihatan di laporan. HHI Score dihitung dari pangsa tiap nama, jadi
meleburkan dua pihak yang berbeda membuat rekening tampak lebih terpusat
daripada kenyataannya, dan menyplit satu pihak membuatnya tampak
terdiversifikasi. Dua-duanya salah, dan dua-duanya tetap terlihat rapi.

KENAPA ENAM UKURAN, BUKAN SATU

Tiap ukuran tuli pada satu jenis perbedaan yang justru lazim di rekening
koran, jadi satu ukuran saja selalu bisa dikelabui:

  Jarak edit (Levenshtein)  Peka pada salah ketik satu-dua huruf.
                            Buta pada kata yang bertukar tempat:
                            "HINO FINANCE INDONESIA PT" vs
                            "PT HINO FINANCE INDONESIA" jaraknya jauh
                            padahal pihaknya sama.

  Jaro-Winkler             Memberi bobot lebih pada AWALAN yang sama —
                            tepat untuk potongan mesin, yang selalu
                            memangkas ekor. Tapi justru karena itu ia
                            memberi nilai tinggi pada dua nama berbeda yang
                            kebetulan berawalan sama ("TIGA BERSAMA
                            LOGISTIK" vs "TIGA PERMATA LOGISTIK").

  Kemiripan token          Melihat nama sebagai himpunan kata, jadi urutan
                            tidak lagi penting. Tapi buta pada kata yang
                            terpotong: "LESTARI" dan "LES" dianggap dua
                            kata yang sama sekali berbeda.

  N-gram karakter          Menambal kelemahan di atas: "LES" dan "LESTARI"
                            berbagi n-gram, dan urutan kata tidak
                            menggesernya seluruhnya.

  Tumpang tindih token     Berapa banyak kata yang benar-benar sama persis.
                            Bukti terkuat yang murah dihitung.

  Selisih panjang ternormalisasi
                            Pagar kewarasan. Nama yang panjangnya berbeda
                            jauh hampir selalu dua hal yang berbeda, dan
                            ukuran lain bisa lupa itu.

Nilai gabungannya bukan probabilitas. Ia peringkat kasar, dan pada data
referensi peringkat itu TERBUKTI bisa menyesatkan: pasangan bernilai
tertinggi di rentang tinjau justru pasangan yang HARUS tetap terpisah
(topup kartu dengan nominal berbeda). Karena itu ambang saja tidak pernah
menjadi keputusan — selalu ada `validasi_konteks` sesudahnya.

JANGAN BANDINGKAN SEMUA LAWAN SEMUA

Nama orang dan nama badan usaha tidak dibandingkan. "PT HINO FINANCE
INDONESIA" dan "Sdr HENDRI" tidak boleh bertemu di perhitungan kemiripan,
berapa pun nilainya — bukan karena nilainya rendah, melainkan karena
pertanyaannya salah. Golongan UNKNOWN tetap boleh dibandingkan dengan
keduanya: potongan mesin sering membuang justru bagian yang menandai
golongan ("AEROTRANS SERVICES I" sudah kehilangan "PT"-nya), jadi menolak
UNKNOWN berarti membuang kasus yang paling perlu ditangani.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

# Satu laporan bisa memuat 771 nama unik — itu 297 ribu pasangan, dan tiap
# nama muncul di ratusan di antaranya. Normalisasi dan pemecahan token
# karena itu diingat, bukan dihitung ulang tiap pasangan. Batasnya cukup
# besar untuk beberapa laporan sekaligus (satu worker melayani banyak
# permintaan) tapi tidak dibiarkan tak terbatas, supaya proses yang hidup
# lama tidak menumpuk nama selamanya.
UKURAN_INGATAN = 50_000

# ── Bentuk badan usaha ───────────────────────────────────────────────────
# Di AWAL nama. Pemisah (titik atau spasi) wajib ada, supaya nama yang
# kebetulan berawalan huruf yang sama ("PTX SEJAHTERA", "CVITO MANDIRI")
# tidak ikut terpotong.
BADAN_AWAL = re.compile(
    r'^(PT|CV|UD|PD|NV|FA|KOPERASI|KOP|YAYASAN|PERUM|PERSERO)'
    r'(?:\s*\.\s*|\s+)',
    re.IGNORECASE)

# Di AKHIR nama. Sebagian kanal mencetak bentuk badan usaha di belakang
# ("AEROFOOD INDONESIA, PT", "CIPTA SAUDARA CV"), dan pihak yang sama bisa
# muncul dalam dua urutan di satu rekening koran yang sama. Pada 44 PDF
# referensi ada 5 baris yang terpecah hanya karena ini.
#
# Tanda baca dan spasi di depan ikut dimakan supaya sisa "AEROFOOD
# INDONESIA," tidak menyisakan koma yang membuat kuncinya berbeda.
#
# Daftarnya JAUH lebih pendek daripada daftar di awal nama, dan itu bukan
# kelalaian. Di ekor nama, singkatan yang sama berarti lain:
#
#     Armanto S Pd · IIN RAJUDIN,S.PD    "PD" = gelar Sarjana Pendidikan
#     MUHAMMAD ILHAM FA                  "FA" = bagian nama orang
#
# Membuangnya berarti memotong nama orang. Yang masuk hanya bentuk yang
# benar-benar muncul sebagai bentuk badan usaha di ekor pada 44 PDF
# referensi (PT 39 kali, CV 8, PERSERO 1) dan tidak punya arti lain di
# posisi itu. "TBK" sengaja TIDAK masuk walau sah secara hukum: ia tidak
# pernah muncul di data, dan membuangnya membuat "PT GARUDA INDONESIA TBK"
# berkunci sama dengan "GARUDA INDONESIA" — lalu potongan "PT GARUDA INDON"
# jadi punya dua induk yang sama-sama masuk akal ("...INDONESIA" dan
# "...INDONESIA CARGO"), yaitu tebakan yang justru ingin dihindari.
BADAN_AKHIR = re.compile(
    r'[\s.,;(\-]*\b(PT|CV|PERSERO)\b[\s.,;)]*$',
    re.IGNORECASE)

# ── Sapaan di depan nama orang ───────────────────────────────────────────
# Bank mencetaknya tidak konsisten: "Ibu ANI ROHIMAH" dan "ANI ROHIMAH"
# adalah orang yang sama, dan pada data referensi pasangan itu benar-benar
# ada. Sapaan dibuang HANYA untuk membandingkan; nama yang ditampilkan di
# laporan tetap apa adanya, karena sapaan itu memang tercetak di dokumen dan
# pemeriksa berhak melihat uraian aslinya.
#
# Daftarnya sengaja sama dengan yang dipakai extractors/bni_nama.py supaya
# tidak ada dua pengertian "sapaan" yang bisa berbeda diam-diam.
# "H" sendirian TIDAK masuk daftar: ia bisa gelar haji ("H SITI AIROH")
# dan bisa inisial nama ("H HERI BUDI"), dan tidak ada di teks yang bisa
# memutuskan mana. Membuangnya berarti menyamakan "H HERI BUDI AN" dengan
# "HERI BUDI AN" secara pasti di tahap 1, padahal validasi konteks justru
# menolak selisih inisial satu huruf di tahap 3 — dua perlakuan berbeda
# untuk hal yang sama, dan celah seperti itulah yang meloloskan salah
# gabung. Jadi kasus itu dibiarkan jatuh ke KANDIDAT.
SAPAAN = ('SDR', 'SDRI', 'BPK', 'BP', 'BAPAK', 'IBU', 'SAUDARA', 'SAUDARI',
          'TN', 'NY', 'HJ')
SAPAAN_AWAL = re.compile(r'^(?:' + '|'.join(SAPAAN) + r')\s*\.?\s+',
                         re.IGNORECASE)

BUKAN_ALFANUMERIK = re.compile(r'[^A-Z0-9]')
PEMISAH_KATA = re.compile(r'[^A-Z0-9]+')

# Kata yang menandai badan usaha walau bentuk badannya sendiri tidak
# tercetak. Dipakai hanya untuk MENGGOLONGKAN, bukan untuk menghapus apa pun.
# Daftarnya pendek dan sengaja hanya memuat kata yang tidak pernah menjadi
# nama orang di Indonesia — "JAYA" dan "MANDIRI" TIDAK masuk, karena
# keduanya nama orang yang lazim.
PENANDA_BADAN = re.compile(
    r'\b(PT|CV|UD|PD|NV|FA|TBK|PERSERO|KOPERASI|YAYASAN|PERUM|BANK|'
    r'INDONESIA|NUSANTARA|LOGISTIK|FINANCE|MULTI\s*FINANCE|SERVICES|'
    r'INDUSTRI|INTERNATIONAL|GROUP|TRADING|KARGO|CARGO|EKSPRES|EKSPEDISI)\b',
    re.IGNORECASE)

# Golongan entitas.
COMPANY = 'COMPANY'
PERSON = 'PERSON'
UNKNOWN = 'UNKNOWN'


# ── Tingkat keyakinan ────────────────────────────────────────────────────
# Diletakkan di satu tempat supaya bisa disetel tanpa menyentuh algoritmanya
# sama sekali — dan angkanya di bawah bukan selera, melainkan hasil sapuan
# atas 44 PDF referensi (`python tests/ambang.py`, yang bisa dijalankan ulang
# kapan pun datanya bertambah).
#
# APA YANG SEBENARNYA DIATUR TIAP ANGKA, sesudah bukti jadi penentu:
#
#   mungkin   batas bawah nama boleh DIGABUNG — tapi menggabungkannya masih
#             butuh bukti potongan dan lolos validasi konteks. Angka ini
#             sendiri tidak pernah cukup.
#   tinjau    batas bawah pasangan ikut DILAPORKAN sebagai kandidat, dan
#             sekaligus batas saringan `layak_dihitung`.
#   tinggi    tinggal label. Sejak penggabungan butuh bukti, tindakan
#             GABUNG dan GABUNG_BILA_KONTEKS melewati pagar yang sama, jadi
#             ia hanya menamai tingkat di `KemiripanEntitas.tingkat`.
#
# Pada nilai bawaan, `mungkin` dan `tinjau` kebetulan sama, sehingga tingkat
# REVIEW tidak pernah tercapai. Itu BUKAN cacat: apa pun yang mencapai
# `tinjau` tapi tidak lolos bukti/konteks tetap dilaporkan sebagai kandidat
# — pelaporannya tidak bergantung pada tingkat.
#
# TITIK AWALNYA 0.95 / 0.90 / 0.80. Yang ditemukan sapuan itu:
#
#   Ambang GABUNG. Pada 0.95/0.90 tahap kemiripan huruf menggabungkan NOL
#   pasangan di seluruh 3.523 nama — satu-satunya pasangan >= 0.95 sudah
#   tuntas di tahap normalisasi (sapaan "Ibu"), dan rentang 0.90-0.949
#   kosong. Diturunkan ke 0.90/0.85 muncul 3 penggabungan, dan ketiganya
#   diperiksa satu per satu dan BENAR — semuanya hal yang tidak bisa
#   dijangkau aturan struktural:
#
#       DUDUNG MULYADI                 · DUDUNG MULYADI, M.       (gelar terpotong)
#       Lili Muniri S                  · Lili Muniri S Si         (gelar terpotong)
#       INDOMOBIL FINANCE INDONE/BCA   · PT INDOMOBIL FINANCE INDONESIA/BCA
#
#   Yang terakhir itu potongan di TENGAH teks gabungan, bukan di ekornya —
#   karena ada "/BCA" menempel, kuncinya bukan awalan dari kunci nama
#   penuhnya, jadi tahap 2 memang tidak bisa melihatnya.
#
#   Diturunkan lagi ke 0.85/0.80 muncul 8 lagi, dan di situ tebakan mulai
#   masuk: "Depari Mujeham Naska" digabung ke "...Naska Pratama" dan "MIRNA
#   HASANAH" ke "MIRNA HASANAH KOTO" — keduanya berhenti di batas kata,
#   persis pola yang aturan tahap 2 sengaja tolak. Jadi 0.90/0.85 adalah
#   batas terakhir yang masih bisa dipertanggungjawabkan pada data ini.
#
#   Di 0.80/0.75 ia bahkan meleburkan "TIGA BERSAMA LOGISTIK PT" dengan
#   "TIGA PERMATA LOGISTIK PT" — dua badan usaha berbeda.
#
#   Ambang TINJAU. Di sini yang menentukan bukan benar/salah tapi apakah
#   daftarnya masih terbaca. Jumlah kandidat tahap 3 atas 44 laporan:
#
#       0.90 →     9        0.75 →   743  (laporan terbanyak: 377 kandidat)
#       0.85 →    33        0.70 →  3872  (laporan terbanyak: 2148)
#       0.80 →    60        0.60 → 20700
#
#   0.80 tepat di lekuk kurva itu. Diukur dengan pemotongan daftar yang
#   sekarang ikut dilaporkan: pada 0.80 hanya SATU laporan yang daftarnya
#   terpotong (1 kandidat), sedangkan pada 0.78 sudah tiga laporan dengan
#   366 kandidat terpotong, dan pada 0.75 empat laporan dengan 605. Daftar
#   yang lebih banyak disembunyikan daripada ditampilkan bukan daftar.
#
# SESUDAH BUKTI JADI PENENTU, ambang gabung diukur ULANG — kali ini dengan
# tests/palsu.py sebagai penilai, bukan pemeriksaan mata. Hasilnya:
#
#   Pada harness sintetis, keempat setelan (0.95/0.90, 0.90/0.85, 0.85/0.80,
#   0.80/0.75) memberi angka yang SAMA PERSIS: recall pola yang ditargetkan
#   94.6%, FMR 0.00%. Ambang tidak lagi mengubah apa pun di situ — yang
#   memutuskan stage 1-2 dan bukti potongan, bukan nilainya.
#
#   Di 44 PDF referensi ambang masih berpengaruh, dan ke arah yang benar:
#
#       0.95/0.90   100 menyatu, tahap 3 menggabungkan 0
#       0.90/0.85   101 menyatu, tahap 3 menggabungkan 1
#       0.85/0.80   103 menyatu, tahap 3 menggabungkan 3   ← dipakai
#
#   Ketiga penggabungan tahap 3 pada 0.85/0.80 seluruhnya potongan mesin
#   berbukti: "GARUDA INDONESI" ↔ "PT GARUDA INDONESIA", "INDOMOBIL FINANCE
#   INDONE/BCA" ↔ "PT INDOMOBIL FINANCE INDONESIA/BCA", dan "PT AEROTRANS
#   SERVICES I DONESIA" ↔ "PT AEROTRANS SERVICES INDONESIA". Jumlah
#   kandidatnya justru turun (176 → 174) karena yang menyatu keluar dari
#   daftar tinjau.
#
#   Menurunkannya lagi ke 0.80/0.75 menambah satu penggabungan ("PT GARUDA
#   INDON" ↔ "GARUDA INDONESI") tapi ikut menurunkan ambang tinjau, dan di
#   situ daftar kandidatnya mulai dipotong besar-besaran. Jadi berhenti di
#   0.85/0.80.
@dataclass(frozen=True)
class AmbangKemiripan:
    """Batas antar tingkat keyakinan."""
    tinggi: float = 0.85     # HIGH CONFIDENCE  → gabung
    mungkin: float = 0.80    # PROBABLE MATCH   → gabung bila konteks mendukung
    tinjau: float = 0.80     # REVIEW           → jangan gabung otomatis
    #                          < tinjau         → SEPARATE

    # Bobot tiap ukuran dalam nilai gabungan. Karakter dan token diberi
    # bobot sama besar karena keduanya saling menambal (lihat docstring
    # modul); awalan lebih kecil karena ia yang paling mudah menipu pada
    # nama berawalan sama; panjang sekadar pagar.
    bobot_karakter: float = 0.35
    bobot_token: float = 0.35
    bobot_awalan: float = 0.20
    bobot_panjang: float = 0.10

    def __post_init__(self):
        """
        Pagar untuk knob yang memang dimaksudkan disetel orang.

        Dua kekeliruan di bawah tidak meledak dengan sendirinya — keduanya
        cuma membuat keputusan penggabungan bergeser diam-diam, dan itu
        jenis kesalahan yang tidak kelihatan di laporan:

          · urutan ambang terbalik membuat satu tingkat mustahil tercapai;
          · bobot yang tidak berjumlah 1 membuat nilai gabungan keluar dari
            rentang 0..1, sehingga angka ambang tidak lagi berarti apa pun.
        """
        if not self.tinggi >= self.mungkin >= self.tinjau:
            raise ValueError(
                'ambang harus menurun: tinggi >= mungkin >= tinjau '
                f'(diberi {self.tinggi}, {self.mungkin}, {self.tinjau})')
        jumlah = (self.bobot_karakter + self.bobot_token
                  + self.bobot_awalan + self.bobot_panjang)
        if abs(jumlah - 1.0) > 1e-9:
            raise ValueError(f'jumlah bobot harus 1.0, bukan {jumlah}')


AMBANG = AmbangKemiripan()

# Tindakan yang menempel pada tiap tingkat. Namanya sengaja kata kerja:
# yang dipakai pemanggil adalah tindakannya, bukan angkanya.
GABUNG = 'GABUNG'                          # auto group
GABUNG_BILA_KONTEKS = 'GABUNG_BILA_KONTEKS'  # auto group only if context supports
TINJAU = 'TINJAU'                          # review, do not auto merge
PISAH = 'PISAH'                            # separate

TINGKAT_TINGGI = 'HIGH_CONFIDENCE'
TINGKAT_MUNGKIN = 'PROBABLE_MATCH'
TINGKAT_TINJAU = 'REVIEW'
TINGKAT_PISAH = 'SEPARATE'

_TINDAKAN = {
    TINGKAT_TINGGI: GABUNG,
    TINGKAT_MUNGKIN: GABUNG_BILA_KONTEKS,
    TINGKAT_TINJAU: TINJAU,
    TINGKAT_PISAH: PISAH,
}


# ── Normalisasi ──────────────────────────────────────────────────────────

@lru_cache(maxsize=UKURAN_INGATAN)
def tanpa_hiasan(nama: str) -> str:
    """
    Nama dengan sapaan dan bentuk badan usaha dibuang, tapi kata-katanya
    masih utuh dan terpisah spasi.

    Dipakai sebagai dasar semua ukuran, supaya perbedaan yang sudah punya
    aturan pasti tidak ikut dihitung sebagai ketidakmiripan. Bentuk badan
    usaha dibuang berulang: "PT. AEROFOOD INDONESIA PT" bisa mencetaknya di
    dua tempat sekaligus.
    """
    teks = (nama or '').strip().upper()
    teks = SAPAAN_AWAL.sub('', teks)
    for pola in (BADAN_AWAL, BADAN_AKHIR):
        sebelum = None
        while sebelum != teks:
            sebelum = teks
            teks = pola.sub('' if pola is BADAN_AWAL else '', teks).strip()
    return teks.strip()


@lru_cache(maxsize=UKURAN_INGATAN)
def kunci_banding(nama: str) -> str:
    """
    Bentuk paling ringkas untuk dibandingkan: huruf besar, tanpa sapaan,
    tanpa bentuk badan usaha, tanpa spasi dan tanda baca.

    Spasi ikut dibuang supaya "WIRAKARYA" dan "WIRA KARYA" — yang jelas
    pihak yang sama — tidak dianggap berbeda.
    """
    return BUKAN_ALFANUMERIK.sub('', tanpa_hiasan(nama))


@lru_cache(maxsize=UKURAN_INGATAN)
def token(nama: str) -> tuple:
    """Kata-kata nama, sesudah sapaan dan bentuk badan usaha dibuang."""
    return tuple(t for t in PEMISAH_KATA.split(tanpa_hiasan(nama)) if t)


@lru_cache(maxsize=UKURAN_INGATAN)
def himpunan_token(nama: str) -> frozenset:
    return frozenset(token(nama))


# ── Penggolongan entitas ─────────────────────────────────────────────────

@lru_cache(maxsize=UKURAN_INGATAN)
def klasifikasi_entitas(nama: str) -> str:
    """
    COMPANY, PERSON, atau UNKNOWN.

    Sengaja pelit: hanya menyimpulkan kalau ada penandanya di teks. UNKNOWN
    bukan kegagalan melainkan jawaban yang jujur — "AEROTRANS SERVICES I"
    memang tidak memuat cukup keterangan untuk dipastikan, dan menebaknya
    COMPANY hanya memindahkan risiko ke tempat yang tidak terlihat.
    """
    teks = (nama or '').strip().upper()
    if not teks:
        return UNKNOWN
    if BADAN_AWAL.match(teks) or BADAN_AKHIR.search(teks):
        return COMPANY
    if SAPAAN_AWAL.match(teks):
        return PERSON
    if PENANDA_BADAN.search(teks):
        return COMPANY
    return UNKNOWN


def boleh_dibandingkan(a: str, b: str) -> bool:
    """
    Bolehkah dua nama ini diukur kemiripannya sama sekali?

    Satu-satunya pasangan yang ditolak adalah COMPANY lawan PERSON. Itu
    bukan soal nilai: "PT HINO FINANCE INDONESIA" dan "Sdr HENDRI" tidak
    perlu dihitung, karena tidak ada nilai yang bisa membuat keduanya pihak
    yang sama. UNKNOWN tetap boleh bertemu keduanya — justru golongan itu
    yang paling sering muncul akibat pemotongan.
    """
    ta, tb = klasifikasi_entitas(a), klasifikasi_entitas(b)
    return not {ta, tb} == {COMPANY, PERSON}


# ── Ukuran-ukuran ────────────────────────────────────────────────────────

def jarak_edit(a: str, b: str) -> int:
    """
    Levenshtein. Dua baris berjalan, bukan matriks penuh: nama bisa panjang
    (satu nama di data referensi 100+ karakter) dan pasangannya banyak.
    """
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    baris = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        baru = [i]
        for j, cb in enumerate(b, 1):
            baru.append(min(baris[j] + 1,          # hapus
                            baru[j - 1] + 1,       # sisip
                            baris[j - 1] + (ca != cb)))  # ganti
        baris = baru
    return baris[-1]


def rasio_edit(a: str, b: str) -> float:
    """Jarak edit dinormalkan ke 0..1 (1 = identik)."""
    if not a and not b:
        return 1.0
    panjang = max(len(a), len(b))
    return 1.0 - jarak_edit(a, b) / panjang if panjang else 1.0


@lru_cache(maxsize=UKURAN_INGATAN)
def _ngram(teks: str, n: int = 3) -> frozenset:
    if len(teks) < n:
        return frozenset({teks}) if teks else frozenset()
    return frozenset(teks[i:i + n] for i in range(len(teks) - n + 1))


def kemiripan_ngram(a: str, b: str, n: int = 3) -> float:
    """
    Dice atas n-gram karakter. Tidak peduli urutan kata, dan masih memberi
    nilai pada kata yang terpotong ("LES" berbagi n-gram dengan "LESTARI").
    """
    ga, gb = _ngram(a, n), _ngram(b, n)
    if not ga and not gb:
        return 1.0
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    jendela = max(0, max(la, lb) // 2 - 1)
    cocok_a = [False] * la
    cocok_b = [False] * lb
    cocok = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - jendela), min(lb, i + jendela + 1)):
            if not cocok_b[j] and b[j] == ca:
                cocok_a[i] = cocok_b[j] = True
                cocok += 1
                break
    if not cocok:
        return 0.0
    transposisi = 0
    j = 0
    for i in range(la):
        if not cocok_a[i]:
            continue
        while not cocok_b[j]:
            j += 1
        if a[i] != b[j]:
            transposisi += 1
        j += 1
    transposisi //= 2
    return (cocok / la + cocok / lb + (cocok - transposisi) / cocok) / 3


def jaro_winkler(a: str, b: str, p: float = 0.1, maks_awalan: int = 4) -> float:
    """
    Jaro dengan bonus untuk awalan yang sama. Cocok untuk potongan mesin,
    yang selalu memangkas ekor dan mempertahankan awalan utuh.
    """
    nilai = jaro(a, b)
    awalan = 0
    for x, y in zip(a, b):
        if x != y:
            break
        awalan += 1
        if awalan == maks_awalan:
            break
    return nilai + awalan * p * (1 - nilai)


def tumpang_tindih_token(a: str, b: str) -> float:
    """Jaccard atas himpunan kata: berapa bagian kata yang sama persis."""
    ta, tb = himpunan_token(a), himpunan_token(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def penjajaran_token(a: str, b: str) -> float:
    """
    Tiap kata dicarikan pasangan termiripnya di sisi lain, lalu dirata-rata
    dua arah.

    Ini yang menangkap "WIRA KARYA" vs "WIRAKARYA" dan "BARASENTOSA LES" vs
    "BARASENTOSA LESTARI", yang oleh Jaccard dianggap nyaris tidak mirip
    karena kata-katanya tidak sama persis.
    """
    ta, tb = token(a), token(b)
    if not ta or not tb:
        return 0.0

    def searah(kiri, kanan):
        return sum(max(rasio_edit(x, y) for y in kanan) for x in kiri) / len(kiri)

    return (searah(ta, tb) + searah(tb, ta)) / 2


def selisih_panjang(a: str, b: str) -> float:
    """
    1 - selisih panjang ternormalisasi. Pagar kewarasan: nama yang
    panjangnya berbeda jauh hampir selalu dua hal yang berbeda.
    """
    ka, kb = kunci_banding(a), kunci_banding(b)
    panjang = max(len(ka), len(kb))
    if not panjang:
        return 1.0
    return 1.0 - abs(len(ka) - len(kb)) / panjang


# ── Hasil ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KemiripanEntitas:
    """
    Hasil pengukuran sepasang nama.

    overall_score    nilai gabungan 0..1 — peringkat kasar, bukan peluang
    character_score  tingkat huruf: rasio jarak edit dan n-gram
    token_score      tingkat kata: tumpang tindih dan penjajaran
    prefix_score     Jaro-Winkler, berbobot pada awalan yang sama
    length_score     1 - selisih panjang ternormalisasi
    tingkat          HIGH_CONFIDENCE / PROBABLE_MATCH / REVIEW / SEPARATE
    tindakan         GABUNG / GABUNG_BILA_KONTEKS / TINJAU / PISAH
    tipe_a, tipe_b   golongan entitas masing-masing
    sebanding        False bila pasangannya memang tidak layak dibandingkan
    alasan           keterangan singkat bila tindakannya diturunkan
    rincian          sub-ukuran, untuk menyetel bobot dengan data nyata
    """
    overall_score: float
    character_score: float
    token_score: float
    prefix_score: float
    length_score: float
    tingkat: str
    tindakan: str
    tipe_a: str
    tipe_b: str
    sebanding: bool = True
    alasan: str = ''
    rincian: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            'overall_score': self.overall_score,
            'character_score': self.character_score,
            'token_score': self.token_score,
            'prefix_score': self.prefix_score,
            'length_score': self.length_score,
        }


def tingkat_dari_nilai(nilai: float, ambang: AmbangKemiripan = AMBANG) -> str:
    if nilai >= ambang.tinggi:
        return TINGKAT_TINGGI
    if nilai >= ambang.mungkin:
        return TINGKAT_MUNGKIN
    if nilai >= ambang.tinjau:
        return TINGKAT_TINJAU
    return TINGKAT_PISAH


def batas_atas_kemiripan(a: str, b: str,
                         ambang: AmbangKemiripan = AMBANG) -> float:
    """
    Nilai tertinggi yang MASIH MUNGKIN dicapai sepasang nama, dihitung hanya
    dari ukuran-ukuran yang murah.

    Ini bukan perkiraan melainkan batas atas, dan itu yang membuatnya boleh
    dipakai menyaring: pasangan yang batas atasnya sudah di bawah ambang
    tinjau tidak mungkin lolos, jadi melewatinya tidak menghilangkan satu
    pasangan pun. Yang dilewati justru mayoritas — dua nama yang tidak
    berhubungan hampir tidak berbagi n-gram maupun kata.

    Perlu, bukan penghias: satu laporan bisa memuat 771 nama unik alias 297
    ribu pasangan. Tanpa saringan ini penyatuan nama pada laporan itu makan
    33 detik, dan penyatuan dijalankan di dalam pembuatan laporan.

    Dua kenyataan yang dipakai, keduanya tidak bisa dilanggar:

      · rasio jarak edit tidak mungkin melebihi perbandingan panjang
        terpendek dengan terpanjang — yaitu length_score itu sendiri;
      · ukuran yang belum dihitung paling banyak bernilai 1.

    Jadi dengan L = length_score, D = n-gram Dice, dan J = tumpang tindih
    token:

        batas = bobot_karakter * (L + D)/2
              + bobot_token    * (J + 1)/2
              + bobot_awalan   * 1
              + bobot_panjang  * L
    """
    ka, kb = kunci_banding(a), kunci_banding(b)
    panjang = max(len(ka), len(kb))
    L = 1.0 if not panjang else 1.0 - abs(len(ka) - len(kb)) / panjang
    D = kemiripan_ngram(ka, kb)
    J = tumpang_tindih_token(a, b)
    return (ambang.bobot_karakter * (min(1.0, L) + D) / 2
            + ambang.bobot_token * (J + 1) / 2
            + ambang.bobot_awalan
            + ambang.bobot_panjang * L)


def layak_dihitung(a: str, b: str, ambang: AmbangKemiripan = AMBANG) -> bool:
    """
    Perlukah pasangan ini dihitung penuh?

    Menolak dua hal saja: pasangan yang golongannya bertentangan, dan
    pasangan yang TERBUKTI tidak mungkin mencapai ambang tinjau (lihat
    `batas_atas_kemiripan`). Tidak ada pasangan yang hilang karena saringan
    ini — yang hilang hanya waktu menghitungnya.
    """
    if not boleh_dibandingkan(a, b):
        return False
    return batas_atas_kemiripan(a, b, ambang) >= ambang.tinjau


def calculate_entity_similarity(a: str, b: str,
                                ambang: AmbangKemiripan = AMBANG) -> KemiripanEntitas:
    """
    Ukur kemiripan dua nama lawan transaksi.

    Fungsi ini TIDAK memutuskan penggabungan dan tidak tahu apa pun tentang
    laporan. Ia mengukur, menggolongkan, lalu berhenti. `tindakan` yang
    dikembalikannya adalah tindakan yang DIIZINKAN oleh nilai — pemanggil
    masih wajib melewatkannya ke `validasi_konteks`, karena pada data nyata
    pasangan bernilai tertinggi di rentang tinjau justru pasangan yang harus
    tetap terpisah.

    Nama yang golongannya bertentangan (COMPANY lawan PERSON) tetap diukur
    dan angkanya tetap dilaporkan apa adanya — supaya kelihatan kalau
    nilainya memang tinggi — tapi tindakannya langsung PISAH.
    """
    ka, kb = kunci_banding(a), kunci_banding(b)

    rasio = rasio_edit(ka, kb)
    ngram = kemiripan_ngram(ka, kb)
    overlap = tumpang_tindih_token(a, b)
    jajar = penjajaran_token(a, b)

    character = (rasio + ngram) / 2
    token_nilai = (overlap + jajar) / 2
    prefix = jaro_winkler(ka, kb)
    length = selisih_panjang(a, b)

    overall = (ambang.bobot_karakter * character
               + ambang.bobot_token * token_nilai
               + ambang.bobot_awalan * prefix
               + ambang.bobot_panjang * length)

    tipe_a, tipe_b = klasifikasi_entitas(a), klasifikasi_entitas(b)
    sebanding = not {tipe_a, tipe_b} == {COMPANY, PERSON}
    tingkat = tingkat_dari_nilai(overall, ambang)
    tindakan = _TINDAKAN[tingkat]
    alasan = ''
    if not sebanding:
        tindakan = PISAH
        alasan = (f'golongan berbeda ({tipe_a} vs {tipe_b}) — tidak '
                  f'dibandingkan walau nilainya {overall:.3f}')

    return KemiripanEntitas(
        overall_score=round(overall, 4),
        character_score=round(character, 4),
        token_score=round(token_nilai, 4),
        prefix_score=round(prefix, 4),
        length_score=round(length, 4),
        tingkat=tingkat,
        tindakan=tindakan,
        tipe_a=tipe_a,
        tipe_b=tipe_b,
        sebanding=sebanding,
        alasan=alasan,
        rincian={
            'rasio_edit': round(rasio, 4),
            'ngram': round(ngram, 4),
            'tumpang_tindih_token': round(overlap, 4),
            'penjajaran_token': round(jajar, 4),
            'jarak_edit': jarak_edit(ka, kb),
        },
    )


# ── Validasi konteks ─────────────────────────────────────────────────────
# Tahap terakhir, dan yang paling menentukan. Nilai kemiripan tidak tahu
# APA yang membuat dua nama berbeda — ia hanya tahu SEBERAPA BANYAK. Di
# rekening koran, justru jenis perbedaannya yang memutuskan:
#
#   FLAZZ BCA TOPUP08111441280 200,000.00
#   FLAZZ BCA TOPUP08111441280 300,000.00   → nilai 0.912, tapi dua nominal
#                                             berbeda: bukan pihak yang sama
#
#   DEXTRATAMA NITYA SANJAYA PT - HT002
#   DEXTRATAMA NITYA SANJAYA PT - HT003     → dua nomor kontrak berbeda
#
#   Sdr M ABDINTA TARIGAN
#   Sdr ABDINTA TARIGAN                     → bisa orang yang sama, bisa
#                                             ayah dan anak. Tidak ada
#                                             buktinya di teks.
#
# Semua itu bernilai TINGGI dan semuanya harus tetap terpisah. Karena itu
# ambang tidak pernah menjadi keputusan akhir.

# Kata yang menempel di ekor nama sebagai KETERANGAN transaksi, bukan
# bagian nama pihak. Kalau dibiarkan, "ERWINSYAH HARAHAP THR" dan
# "ERWINSYAH HARAHAP" tampak seperti dua pihak — tapi menggabungkannya di
# sini juga bukan urusan modul ini: yang bisa dipastikan hanyalah bahwa
# selisihnya BUKAN potongan mesin, jadi keputusannya diserahkan pemeriksa.
KETERANGAN_EKOR = {'DP', 'THR', 'INV', 'BB', 'PELUNASAN', 'CICILAN', 'ANGSURAN',
                   'BONUS', 'GAJI', 'PAYROLL', 'TRANSFER', 'FEE', 'PINBUK'}

RE_BERANGKA = re.compile(r'\d')


def _token_berangka(nama: str) -> set:
    return {t for t in token(nama) if RE_BERANGKA.search(t)}


def _saling_berawalan(x: str, y: str) -> bool:
    return x != y and (x.startswith(y) or y.startswith(x))


def _angka_terpotong(a: str, b: str, angka_a: set, angka_b: set) -> bool:
    """
    Benarkah selisih angka kedua nama ini karena angkanya TERPOTONG?

    Syarat pertama, dan yang paling menentukan: kunci nama yang PENDEK harus
    merupakan awalan kunci nama yang panjang. Artinya seluruh kepala namanya
    sama persis dan hanya ekornya yang hilang — bentuk potongan, bukan
    penggantian. Ini yang menolak "200,000.00" lawan "300,000.00" dan
    "HT002" lawan "HT003": angkanya berbeda di tengah, jadi kuncinya tidak
    saling berawalan.

    Sesudah itu, angka yang berbeda di sisi PENDEK wajib punya pasangan yang
    saling berawalan di sisi panjang — angka yang setengah tertulis:

        329011051625   dari  32901105162505

    Angka yang hanya ada di sisi PANJANG tidak perlu berpasangan, karena ia
    memang jatuh SESUDAH titik potong dan karena itu hilang seluruhnya:

        PT.SILKARGO IND   dari  PT.SILKARGO INDONESIA - 022

    Perlu diingat pengecualian ini tidak berdiri sendiri: pemanggilnya sudah
    lebih dulu menuntut potongan di tengah kata (tahap 2) atau bukti
    potongan (tahap 3). Jadi "GARUDA INDONESIA" lawan "GARUDA INDONESIA
    2026" tetap tidak digabung — bukan oleh aturan ini, melainkan karena
    potongannya jatuh di batas kata.
    """
    ka, kb = kunci_banding(a), kunci_banding(b)
    pendek, panjang = ((a, b) if len(ka) <= len(kb) else (b, a))
    k_pendek, k_panjang = ((ka, kb) if len(ka) <= len(kb) else (kb, ka))
    if not k_panjang.startswith(k_pendek):
        return False

    beda_pendek = _token_berangka(pendek) - _token_berangka(panjang)
    beda_panjang = _token_berangka(panjang) - _token_berangka(pendek)
    return all(any(_saling_berawalan(x, y) for y in beda_panjang)
               for x in beda_pendek)


def validasi_konteks(a: str, b: str):
    """
    Apakah ada alasan KONKRET untuk tidak menggabungkan sepasang nama yang
    nilainya sudah tinggi?

    Returns:
        (True, '')        tidak ada penghalang yang bisa dipastikan
        (False, alasan)   ada, beserta alasannya dalam bahasa manusia —
                          alasan itu ikut dicetak di daftar KANDIDAT pada
                          sheet Rekap, jadi pemeriksa tahu apa yang dilihat
                          sistem dan mengapa ia memilih tidak memutuskan.

    Semua aturannya menolak, tidak ada yang meluluskan: kalau tidak satu pun
    berlaku, pasangannya lolos. Ini disengaja — bukti untuk MEMBEDAKAN dua
    pihak biasanya tercetak di nama, sedangkan bukti bahwa keduanya sama
    tidak pernah ada di teks.
    """
    ta, tb = klasifikasi_entitas(a), klasifikasi_entitas(b)
    if {ta, tb} == {COMPANY, PERSON}:
        return False, f'golongan berbeda ({ta} vs {tb})'

    # Angka di dalam nama hampir selalu membedakan: nominal, nomor kontrak,
    # nomor rekening, kode cabang. Beda angka = beda hal, walau seluruh sisa
    # namanya sama persis.
    #
    # KECUALI angkanya sendiri yang TERPOTONG. Pemotongan lebar tetap tidak
    # peduli isi kolomnya, jadi ia memotong angka sama saja dengan memotong
    # kata:
    #
    #     329011051625   dari  32901105162505              (nomor rekening)
    #     SPBU 34.1580   dari  SPBU 34.15802,CURUG TANGERANG SLTID
    #
    # Diukur dengan tests/palsu.py, veto tanpa pengecualian ini menolak 19
    # dari 33 potongan yang tidak ketemu induknya — penyebab tunggal
    # terbesar celah recall pola potongan lebar tetap.
    #
    # Pengecualiannya sengaja sempit: angka yang berbeda harus SALING
    # BERAWALAN, dan kunci kedua namanya juga harus saling berawalan. Yang
    # kedua itu memastikan bentuknya benar-benar potongan (seluruh kepala
    # nama sama persis, ekornya hilang) dan bukan penggantian — "200,000.00"
    # lawan "300,000.00" dan "HT002" lawan "HT003" tetap ditolak, karena
    # angkanya tidak saling berawalan.
    #
    # RISIKO YANG DITERIMA: angka yang memang berbeda tapi kebetulan saling
    # berawalan — "INV 100" lawan "INV 1001" — kini digabung. Bentuk itu
    # tidak muncul di 44 PDF referensi, sedangkan bentuk yang muncul
    # (potongan nomor rekening dan kode SPBU) memang satu pihak. Kalau suatu
    # hari bentuk itu muncul, di sinilah tempat memperketatnya.
    angka_a, angka_b = _token_berangka(a), _token_berangka(b)
    if angka_a != angka_b and not _angka_terpotong(a, b, angka_a, angka_b):
        beda = sorted((angka_a | angka_b) - (angka_a & angka_b))[:3]
        return False, ('memuat angka yang berbeda (' + ', '.join(beda) +
                       ') — nominal/nomor, bukan varian penulisan')

    set_a, set_b = himpunan_token(a), himpunan_token(b)
    tambahan = (set_a | set_b) - (set_a & set_b)

    # Kata keterangan transaksi yang menempel di ekor nama.
    ekor = sorted(tambahan & KETERANGAN_EKOR)
    if ekor:
        return False, ('selisihnya kata keterangan transaksi (' +
                       ', '.join(ekor) + '), bukan varian nama')

    # Inisial satu huruf. "M ABDINTA TARIGAN" dan "ABDINTA TARIGAN" bisa
    # orang yang sama — dan bisa juga dua orang dengan nama keluarga sama.
    # Tidak ada di teks yang bisa memutuskannya.
    #
    # Dikecualikan: huruf yang ternyata sisa kata yang terpotong mesin, bukan
    # inisial nama. Ada dua bentuknya, dan keduanya dari data nyata:
    #
    #   PT TEBO MULTI A  vs  PT TEBO MULTI AGRO     huruf "A" = awalan "AGRO"
    #   PT GARUDA INDON  vs  GARUDA INDONESIA ( P   huruf "P" = potongan
    #                                               "(PERSERO)", dan salah satu
    #                                               kunci awalan yang lain
    #
    # Bentuk kedua perlu dikecualikan bukan supaya digabung — tahap 2 masih
    # menilai rantainya — melainkan supaya ALASAN yang dicetak ke pemeriksa
    # jujur. Menyebutnya "inisial satu huruf" di situ menyesatkan.
    ka, kb = kunci_banding(a), kunci_banding(b)
    berawalan_sama = ka.startswith(kb) or kb.startswith(ka)
    ekor = {token(n)[-1] for n in (a, b) if token(n)}

    def _potongan_kata(huruf):
        if any(lain.startswith(huruf) and lain != huruf for lain in tambahan):
            return True
        return berawalan_sama and huruf in ekor

    inisial = sorted(t for t in tambahan
                     if len(t) == 1 and not _potongan_kata(t))
    if inisial:
        return False, ('selisihnya inisial satu huruf (' + ', '.join(inisial) +
                       ') — bisa orang yang berbeda')

    return True, ''


def bukti_potongan(a: str, b: str):
    """
    Adakah bukti POSITIF bahwa selisih dua nama ini kata yang TERPOTONG,
    bukan kata yang ditambahkan atau diganti?

    Kenapa ini perlu padahal sudah ada ambang dan validasi konteks: ambang
    hanya tahu seberapa BANYAK dua nama berbeda. Diukur pada 44 PDF
    referensi, dua bentuk di bawah nilainya nyaris sama — dan yang satu
    harus digabung, yang lain tidak:

        GARUDA INDONESI      ↔ PT GARUDA INDONESIA     0.846  → satu pihak
        MIRNA HASANAH        ↔ MIRNA HASANAH KOTO      0.802  → bisa dua orang

    Yang membedakan bukan nilainya, melainkan BENTUK selisihnya: "INDONESI"
    adalah "INDONESIA" yang terpotong, sedangkan "KOTO" kata baru yang utuh.
    Selama yang menahan keduanya cuma selisih 0,04 dari ambang, yang
    menahannya adalah keberuntungan.

    Dua bentuk diterima sebagai bukti, keduanya dari cacat yang memang
    terlihat di data:

      POTONGAN KATA   Tiap kata yang berbeda harus berpasangan dengan kata
                      di sisi lain yang SALING BERAWALAN — "INDONE" /
                      "INDONESIA". Syarat "tiap kata" itu penting: kalau
                      cukup SATU pasangan yang berawalan, maka "PT ABC
                      INDONESIA" dan "PT XYZ INDONESI" akan lolos lewat
                      INDONESIA/INDONESI padahal ABC dan XYZ tidak ada
                      hubungannya sama sekali.

      SPASI TERSISIP  "PT AEROTRANS SERVICES I DONESIA" vs "...INDONESIA":
                      jumlah katanya berbeda, kuncinya beda paling banyak
                      satu huruf, DAN sisi yang katanya lebih banyak
                      kuncinya tidak lebih panjang. Syarat terakhir yang
                      memisahkannya dari gelar yang DITAMBAHKAN: "DUDUNG
                      MULYADI, M." juga berbeda satu huruf dari "DUDUNG
                      MULYADI" dan jumlah katanya juga berbeda — tapi
                      kuncinya bertambah panjang, bukan berkurang. Huruf
                      yang hilang adalah tanda kata yang terbelah; huruf
                      yang bertambah adalah kata baru.

    Returns:
        (True, bukti)     ada bukti, beserta bentuknya dalam bahasa manusia
        (False, alasan)   tidak ada — dan alasannya ikut tercetak di daftar
                          KANDIDAT, supaya pemeriksa tahu apa yang dilihat
                          sistem

    Sengaja TIDAK dipakai tahap 2: di sana relasi awalan atas seluruh nama
    plus potongan di tengah kata sudah menjadi buktinya sendiri, dan
    aturannya dijaga tesnya sendiri.

    YANG DIUKUR LALU DITOLAK: VETO TOKEN UMUM

    Sebelum aturan di atas ada, empat pasangan berikut lolos ambang dan
    validasi konteks, dan semuanya harus tetap terpisah:

        AJL LOGISTIK INDONESIA    ↔ PT TAPANULI LOGISTIK INDONESIA
        Siti Aminah               ↔ Siti Alinah
        NI WAYAN ARTINI           ↔ NI WAYAN SUGIARTINI
        PT GARUDA INDONESIA CARGO ↔ PT GARUDA INDONESIA

    Semuanya punya pola sama: yang mereka bagi hanyalah kata yang UMUM
    (INDONESIA muncul di 71 nama, SITI di 28, LOGISTIK di 15). Jadi
    dicobalah satu veto: tolak kalau seluruh token bersama termasuk 77 token
    ber-df >= 8 yang diukur dari korpus.

    Veto itu diukur dan DIBUANG, karena sesudah aturan bukti di atas ia
    tidak menambah apa pun dan justru merusak:

      · keempat pasangan itu kini ditolak oleh aturan bukti — "AJL" dan
        "TAPANULI", "AMINAH" dan "ALINAH", "ARTINI" dan "SUGIARTINI" tidak
        saling berawalan, dan "CARGO" adalah kata utuh yang ditambahkan;
      · sebaliknya, ia MEMBATALKAN penggabungan yang benar: "GARUDA
        INDONESI" ↔ "PT GARUDA INDONESIA" cuma berbagi satu kata, "GARUDA",
        yang termasuk umum — padahal itu potongan mesin yang jelas.

    Diukur pada 44 PDF referensi di tiga setelan ambang, veto itu
    membatalkan 0, 1, dan 2 penggabungan — dan SELURUHNYA penggabungan yang
    benar. Ia juga akan membuat nilai satu pasangan bergantung pada isi
    laporan lain, yang tidak bisa diuji. Bentuk selisih ternyata bukti yang
    lebih kuat daripada kelangkaan kata.
    """
    ta, tb = list(token(a)), list(token(b))
    sa, sb = set(ta), set(tb)
    beda_a, beda_b = sa - sb, sb - sa

    # ── Spasi tersisip di tengah kata ──
    if len(ta) != len(tb):
        ka, kb = kunci_banding(a), kunci_banding(b)
        banyak, sedikit = ((ka, kb) if len(ta) > len(tb) else (kb, ka))
        if len(banyak) <= len(sedikit) and jarak_edit(ka, kb) <= 1:
            return True, (f'jumlah kata berbeda tapi kuncinya hampir sama '
                          f'({jarak_edit(ka, kb)} huruf) — spasi tersisip di '
                          f'tengah kata')

    # ── Potongan kata ──
    if not beda_a or not beda_b:
        return False, ('selisihnya kata utuh yang ditambahkan, bukan kata '
                       'yang terpotong')

    berpasangan = set()
    bukti = []
    for x in sorted(beda_a):
        for y in sorted(beda_b):
            if y.startswith(x) or x.startswith(y):
                berpasangan |= {x, y}
                bukti.append(f'{x} / {y}')

    sisa = sorted((beda_a | beda_b) - berpasangan)
    if sisa:
        return False, ('kata yang berbeda tidak saling berawalan (' +
                       ', '.join(sisa[:3]) + ') — bukan potongan')

    return True, ', '.join(bukti) + ' — kata yang sama, satu terpotong'
