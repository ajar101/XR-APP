"""
palsu.py — Ukur SEBERAPA SERING penyatuan nama salah menggabungkan.

    python tests/palsu.py                # angka ringkas
    python tests/palsu.py --rinci         # + daftar tiap penggabungan salah
    python tests/palsu.py --seed 7        # populasi & mutasi yang berbeda
    python tests/palsu.py --ambang 0.85,0.80,0.80    # setelan ambang lain

Kenapa ada, dan kenapa ini yang paling penting dari seluruh tes penyatuan:
seluruh keputusan rancangan sejauh ini — ambang, aturan potongan, validasi
konteks — diambil dengan MEMERIKSA pasangan satu per satu. Satu penilai,
tanpa label independen. Cara itu tidak bisa dipakai terus: ia tidak bisa
membandingkan dua usulan, tidak bisa memberi tahu kalau ada pola cacat yang
belum terpikir, dan tidak bisa membedakan "aturan ini benar" dari "aturan ini
kebetulan cocok pada contoh yang saya lihat".

Yang dibutuhkan angka yang BERGERAK tiap kali kodenya berubah. Itu butuh
label yang pasti, dan label yang pasti hanya bisa didapat dengan MEMBUAT
variannya sendiri:

  1. Ambil nama-nama dari 44 PDF referensi yang PASTI pihak berbeda —
     saling tidak mirip, tidak berelasi awalan, kuncinya tidak sama.
  2. Terapkan pola cacat yang memang terbukti ada di data itu (potong lebar
     tetap, spasi tersisip, bentuk badan usaha dibalik, sapaan, gelar, ekor
     keterangan, salah ketik).
  3. Jalankan penyatuan, lalu bandingkan hasilnya dengan label yang sudah
     kita ketahui sejak awal.

Dua angka yang keluar, dan keduanya perlu:

  RECALL         berapa bagian varian yang ketemu induknya. Kalau turun,
                 satu pihak kembali pecah jadi beberapa baris Rekap dan HHI
                 melaporkan rekening lebih terdiversifikasi dari kenyataan.

  FALSE-MERGE    berapa bagian penggabungan yang MELEBURKAN DUA PIHAK
  RATE (FMR)     BERBEDA. Ini yang lebih berbahaya: HHI salah ke arah
                 sebaliknya, dan laporannya tetap terlihat rapi.

APA YANG TES INI TIDAK BUKTIKAN

Variannya sintetis. Ia meniru pola yang sudah terlihat di 44 PDF referensi,
jadi ia bisa membuktikan sebuah perubahan MERUSAK penanganan pola yang
diketahui — tapi tidak bisa membuktikan tidak ada pola kedelapan yang belum
pernah kita lihat. Untuk itu tetap perlu data baru, bukan tes ini.

Karena itu polanya dipisah tiga golongan, dan pemisahan ini yang membuat
angkanya bisa dibaca:

  DISTRAKTOR         varian yang labelnya PIHAK BERBEDA, bukan varian
                     penulisan: satu kata di tengah diganti ("TIGA BERSAMA
                     LOGISTIK" → "TIGA PERMATA LOGISTIK"), atau angkanya
                     diganti. Inilah yang memberi FMR gigi — tanpa
                     distraktor, populasi yang saling tidak mirip hampir
                     tidak mungkin salah digabung, dan angka 0% tidak
                     membuktikan apa-apa.
  DITARGETKAN        pola yang aturan sekarang memang dirancang menangani.
                     Recall di sini HARUS tinggi; turunnya = regresi.
  BELUM DITANGANI    pola yang sengaja belum ditangani (gelar akademik,
                     salah ketik). Recall rendah di sini BUKAN kegagalan —
                     ia mengukur besar celahnya, supaya keputusan menutup
                     celah itu punya angka.
  SENGAJA DITAHAN    pola yang memang diserahkan ke pemeriksa (ekor
                     keterangan transaksi). Di sini yang diharapkan justru
                     TIDAK digabung.

Tes ini MENGUKUR, dan hanya gagal (kode keluar 1) kalau FMR melewati batas
yang tercatat di FMR_MAKS atau recall pola yang ditargetkan jatuh di bawah
RECALL_MIN — dua angka yang ikut direkam di sini supaya perubahan yang
memperburuknya tidak bisa lewat diam-diam.
"""

import argparse
import glob
import json
import os
import random
import re
import sys
from collections import defaultdict

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from engine.kemiripan_entitas import (   # noqa: E402
    AMBANG,
    AmbangKemiripan,
    BADAN_AWAL,
    PERSON,
    batas_atas_kemiripan,
    calculate_entity_similarity,
    klasifikasi_entitas,
    kunci_banding,
)
from engine.penyatu_nama import satukan   # noqa: E402

SEED = 20260917

# Batas yang dijaga. Angkanya hasil pengukuran, bukan target yang dikarang:
# diisi dari garis dasar yang sudah diperiksa, lalu hanya boleh DIPERKETAT.
#
# GARIS DASAR pada seed bawaan, sesudah empat tahap penyatuan dengan ambang
# 0.90/0.85/0.80 (dicatat supaya "normal" itu punya angka):
#
#     DITARGETKAN      95.0%   (potong lebar 78.3% — yang paling lemah)
#     BELUM DITANGANI  20.5%   (gelar 22.1%, salah ketik 18.6%)
#     SENGAJA DITAHAN   0.7%
#     DISTRAKTOR        0.8%
#     FMR              0.47%   (5 dari 1.053 pasangan yang melebur)
#
# Batasnya diberi kelonggaran dari garis dasar itu — cukup lapang supaya
# perubahan kecil tidak menimbulkan alarm palsu, cukup rapat supaya regresi
# nyata tidak lewat.
FMR_MAKS = 0.01        # bagian penggabungan yang meleburkan dua pihak berbeda
RECALL_MIN = 0.90      # recall gabungan seluruh pola yang DITARGETKAN

# Satu "laporan sintetis" dibuat sebesar laporan nyata (rata-rata 93 nama
# unik per PDF referensi) supaya jumlah pasangan yang bersaing — dan karena
# itu peluang salah gabung — sebanding dengan keadaan sebenarnya.
PER_LAPORAN = 100
JUMLAH_LAPORAN = 8

# Lebar potong yang benar-benar terlihat di data referensi.
LEBAR_POTONG = (12, 15, 20, 24)
SAPAAN_UJI = ('Sdr ', 'Sdri ', 'Bpk ', 'Ibu ')
GELAR_UJI = (', S.Si', ', S.Pd', ', ST', ', M.M', ' S Sos')
EKOR_UJI = (' THR', ' DP', ' PINBUK KE BNI OPS', ' BB SPSI')

# Label kategori, bukan lawan transaksi — tidak boleh masuk populasi.
BUKAN_PIHAK = re.compile(
    r'^(biaya|bunga|pajak|total|saldo|transaksi lainnya|tidak teridentifikasi)',
    re.IGNORECASE)


# ── Populasi: nama yang PASTI pihak berbeda ──────────────────────────────

def nama_korpus() -> list:
    nama = set()
    for f in sorted(glob.glob(os.path.join(SESAT, 'tests', 'snapshot', '*.json'))):
        with open(f) as fh:
            d = json.load(fh)
        nama |= {t[4] for t in d['transaksi'] if t[4] and str(t[4]).strip()}
    return sorted(n for n in nama
                  if len(kunci_banding(n)) >= 8 and not BUKAN_PIHAK.match(n.strip()))


def pasti_berbeda(kandidat: list, maks: int) -> list:
    """
    Saring jadi populasi yang label "pihak berbeda"-nya bisa dipertanggungjawabkan.

    Korpus nyata SENDIRI memuat varian penulisan dari pihak yang sama — itu
    justru masalah yang ditangani modul ini. Kalau semuanya dianggap pihak
    berbeda, penggabungan yang BENAR akan terhitung sebagai salah dan
    angkanya jadi bohong. Jadi sebuah nama hanya masuk populasi kalau
    terhadap SEMUA nama yang sudah masuk ia:

      · kuncinya tidak sama,
      · tidak berelasi awalan (bukan potongan satu sama lain),
      · dan kemiripannya di bawah 0.60 — jauh di bawah ambang tinjau,
        jadi tidak ada pasangan ambigu yang ikut terbawa.
    """
    terpilih = []
    kunci_terpilih = []
    for n in kandidat:
        k = kunci_banding(n)
        if not k:
            continue
        aman = True
        for m, km in zip(terpilih, kunci_terpilih):
            if k == km or k.startswith(km) or km.startswith(k):
                aman = False
                break
            # batas_atas murah dan merupakan batas ATAS, jadi kalau ia sudah
            # di bawah 0.60 tidak perlu hitungan penuh.
            if batas_atas_kemiripan(n, m) < 0.60:
                continue
            if calculate_entity_similarity(n, m).overall_score >= 0.60:
                aman = False
                break
        if aman:
            terpilih.append(n)
            kunci_terpilih.append(k)
            if len(terpilih) >= maks:
                break
    return terpilih


# ── Pola cacat ───────────────────────────────────────────────────────────

def potong_lebar(nama, rng):
    """Potong pada hitungan karakter, dan hanya terima kalau jatuh di TENGAH
    kata — itu tanda khas pemotongan mesin (lihat engine/penyatu_nama.py)."""
    teks = nama.strip()
    for lebar in rng.sample(LEBAR_POTONG, len(LEBAR_POTONG)):
        if lebar >= len(teks) - 1:
            continue
        if teks[lebar - 1].isalnum() and teks[lebar].isalnum():
            return teks[:lebar]
    return None


def sisip_spasi(nama, rng):
    """Spasi tersisip di tengah kata: "INDONESIA" → "I DONESIA"."""
    kata = nama.split()
    urut = [i for i, k in enumerate(kata) if len(k) >= 5 and k.isalnum()]
    if not urut:
        return None
    i = rng.choice(urut)
    potong = rng.randint(1, len(kata[i]) - 2)
    kata[i] = kata[i][:potong] + ' ' + kata[i][potong:]
    return ' '.join(kata)


def balik_badan(nama, rng):
    """"PT X" → "X, PT" — satu pihak bisa muncul dua urutan di satu laporan."""
    m = BADAN_AWAL.match(nama.strip().upper())
    if not m:
        return None
    badan = m.group(1).upper()
    sisa = nama.strip()[m.end():].strip()
    return f'{sisa}, {badan}' if sisa else None


def hapus_badan(nama, rng):
    m = BADAN_AWAL.match(nama.strip().upper())
    if not m:
        return None
    sisa = nama.strip()[m.end():].strip()
    return sisa or None


def tambah_sapaan(nama, rng):
    if BADAN_AWAL.match(nama.strip().upper()):
        return None
    return rng.choice(SAPAAN_UJI) + nama.strip()


def tambah_gelar(nama, rng):
    if BADAN_AWAL.match(nama.strip().upper()):
        return None
    return nama.strip() + rng.choice(GELAR_UJI)


def ekor_keterangan(nama, rng):
    return nama.strip() + rng.choice(EKOR_UJI)


def salah_ketik(nama, rng):
    posisi = [i for i, c in enumerate(nama) if c.isalpha()]
    if len(posisi) < 6:
        return None
    i = rng.choice(posisi[3:])
    ganti = rng.choice('AEIOUBDKMNPRST')
    if ganti == nama[i].upper():
        ganti = 'Z'
    return nama[:i] + ganti + nama[i + 1:]


def _kata_asing(nama, rng, kolam):
    """Satu kata dari nama LAIN, untuk dipakai membuat nama pihak berbeda."""
    for _ in range(12):
        lain = rng.choice(kolam)
        kata = [k for k in lain.split() if len(k) >= 4 and k.isalpha()]
        if kata:
            pilih = rng.choice(kata)
            if pilih.upper() not in nama.upper():
                return pilih
    return None


def ganti_kata(nama, rng, kolam):
    """
    "TIGA BERSAMA LOGISTIK PT" → "TIGA PERMATA LOGISTIK PT".

    Ini bentuk paling berbahaya yang ada di data nyata: satu kata di tengah
    berbeda, sisanya sama persis, dan nilai kemiripannya tinggi. Pasangan
    seperti ini yang harus TETAP terpisah, dan tiap kali ia melebur, HHI
    salah ke arah yang membuat rekening tampak lebih terpusat.
    """
    kata = nama.split()
    urut = [i for i, k in enumerate(kata) if len(k) >= 4 and k.isalpha()]
    if len(kata) < 2 or not urut:
        return None
    asing = _kata_asing(nama, rng, kolam)
    if not asing:
        return None
    kata[rng.choice(urut)] = asing
    hasil = ' '.join(kata)
    return hasil if kunci_banding(hasil) != kunci_banding(nama) else None


def ganti_angka(nama, rng, kolam):
    """Nominal atau nomor kontrak yang berbeda — pihak/transaksi lain."""
    angka = [i for i, c in enumerate(nama) if c.isdigit()]
    if len(angka) < 3:
        return None
    i = rng.choice(angka)
    baru = str((int(nama[i]) + rng.randint(1, 8)) % 10)
    return nama[:i] + baru + nama[i + 1:]


# Varian yang labelnya PIHAK BERBEDA, bukan varian penulisan. Setiap kali
# salah satunya melebur dengan sumbernya, itu false merge.
DISTRAKTOR = [
    ('kata tengah diganti', ganti_kata),
    ('angka diganti',       ganti_angka),
]

POLA = [
    ('potong lebar tetap',    potong_lebar,    'DITARGETKAN'),
    ('spasi tersisip',        sisip_spasi,     'DITARGETKAN'),
    ('badan usaha dibalik',   balik_badan,     'DITARGETKAN'),
    ('badan usaha dihapus',   hapus_badan,     'DITARGETKAN'),
    ('sapaan ditambah',       tambah_sapaan,   'DITARGETKAN'),
    ('gelar ditambah',        tambah_gelar,    'BELUM DITANGANI'),
    ('salah ketik 1 huruf',   salah_ketik,     'BELUM DITANGANI'),
    ('ekor keterangan',       ekor_keterangan, 'SENGAJA DITAHAN'),
]


# ── Pengukuran ───────────────────────────────────────────────────────────

def satu_laporan(populasi, rng):
    """
    Bangun satu laporan sintetis: nama asli + variannya.

    Returns:
        (daftar string, {string -> id entitas}, [(pola, asli, varian)])
    """
    label = {}
    varian_dibuat = []
    for asli in populasi:
        label[asli] = len(label) if asli not in label else label[asli]
    # id entitas dipakai ulang di bawah, jadi disusun sekali dan tetap
    label = {n: i for i, n in enumerate(populasi)}
    berikutnya = len(populasi)

    for idx, asli in enumerate(populasi):
        # Pola dibagi HANYA dari yang benar-benar berlaku untuk nama ini.
        # Pembagian berputar buta membuat pola khusus badan usaha ("PT X" →
        # "X, PT") hampir tidak kebagian contoh, karena mayoritas nama di
        # populasi adalah nama orang — dan pola yang cuma dapat 6 contoh
        # tidak mengukur apa pun.
        berlaku = []
        for nama_pola, fungsi, _gol in POLA:
            hasil = fungsi(asli, rng)
            if hasil and hasil.strip().upper() != asli.strip().upper():
                berlaku.append((nama_pola, hasil))
        for geser in range(min(2, len(berlaku))):
            nama_pola, hasil = berlaku[(idx + geser) % len(berlaku)]
            if hasil in label:      # bentrok dengan string lain: buang
                continue
            label[hasil] = idx
            varian_dibuat.append((nama_pola, asli, hasil))

        # Distraktor: satu nama mirip yang labelnya PIHAK BERBEDA.
        nama_pola, fungsi = DISTRAKTOR[idx % len(DISTRAKTOR)]
        hasil = fungsi(asli, rng, populasi)
        if hasil and hasil not in label:
            label[hasil] = berikutnya
            berikutnya += 1
            varian_dibuat.append(('* ' + nama_pola, asli, hasil))

    return list(label), label, varian_dibuat


def ukur(seed=SEED, ambang=None):
    ambang = ambang or AMBANG
    rng = random.Random(seed)
    semua = nama_korpus()
    rng.shuffle(semua)
    populasi = pasti_berbeda(semua, PER_LAPORAN * JUMLAH_LAPORAN)

    per_pola = defaultdict(lambda: [0, 0])      # pola -> [ketemu, total]
    salah = []                                  # penggabungan antar-entitas
    jumlah_gabung = 0

    for b in range(JUMLAH_LAPORAN):
        bagian = populasi[b * PER_LAPORAN:(b + 1) * PER_LAPORAN]
        if len(bagian) < 10:
            break
        daftar, label, varian = satu_laporan(bagian, rng)
        h = satukan(daftar, ambang=ambang)

        for nama_pola, asli, v in varian:
            per_pola[nama_pola][1] += 1
            if h(v) == h(asli):
                per_pola[nama_pola][0] += 1

        # Semua pasangan yang BERAKHIR satu kelompok, dihitung per kelompok.
        kelompok = defaultdict(list)
        for s in daftar:
            kelompok[h(s)].append(s)
        for anggota in kelompok.values():
            if len(anggota) < 2:
                continue
            for i, x in enumerate(anggota):
                for y in anggota[i + 1:]:
                    jumlah_gabung += 1
                    if label[x] != label[y]:
                        salah.append((b, x, y))

    return populasi, per_pola, salah, jumlah_gabung


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rinci', action='store_true')
    p.add_argument('--seed', type=int, default=SEED)
    p.add_argument('--ambang', default='',
                   help='tinggi,mungkin,tinjau — untuk membandingkan setelan '
                        'ambang dengan FMR sebagai penilai')
    args = p.parse_args()

    ambang = AMBANG
    if args.ambang:
        tinggi, mungkin, tinjau = (float(x) for x in args.ambang.split(','))
        ambang = AmbangKemiripan(tinggi=tinggi, mungkin=mungkin, tinjau=tinjau)

    populasi, per_pola, salah, jumlah_gabung = ukur(args.seed, ambang)
    print(f'populasi: {len(populasi)} nama yang pasti pihak berbeda, '
          f'{JUMLAH_LAPORAN} laporan sintetis @{PER_LAPORAN} nama '
          f'(seed {args.seed}, ambang {ambang.tinggi}/{ambang.mungkin}/'
          f'{ambang.tinjau})\n')

    print(f'{"pola cacat":<24} {"golongan":<16} {"ketemu":>7} {"dari":>6} {"recall":>8}')
    print('-' * 66)
    ringkas = defaultdict(lambda: [0, 0])
    urut_pola = ([(n, g) for n, _f, g in POLA]
                 + [('* ' + n, 'DISTRAKTOR') for n, _f in DISTRAKTOR])
    for nama_pola, gol in urut_pola:
        ketemu, total = per_pola[nama_pola]
        recall = ketemu / total if total else float('nan')
        ringkas[gol][0] += ketemu
        ringkas[gol][1] += total
        print(f'{nama_pola:<24} {gol:<16} {ketemu:>7} {total:>6} {recall:>7.1%}')
    print('-' * 66)
    for gol in ('DITARGETKAN', 'BELUM DITANGANI', 'SENGAJA DITAHAN',
                'DISTRAKTOR'):
        ketemu, total = ringkas[gol]
        if not total:
            continue
        arah = {'DITARGETKAN': 'harus tinggi',
                'BELUM DITANGANI': 'mengukur celah',
                'SENGAJA DITAHAN': 'harus RENDAH',
                'DISTRAKTOR': 'pihak BERBEDA — harus 0%'}[gol]
        print(f'{gol:<24} {ketemu:>24}/{total:<6} {ketemu/total:>6.1%}  ({arah})')

    fmr = len(salah) / jumlah_gabung if jumlah_gabung else 0.0
    print(f'\nFALSE-MERGE RATE: {len(salah)} dari {jumlah_gabung} pasangan yang '
          f'melebur = {fmr:.2%}')

    if salah:
        print('\nPENGGABUNGAN SALAH (dua pihak berbeda jadi satu baris Rekap):')
        for b, x, y in (salah if args.rinci else salah[:10]):
            n = calculate_entity_similarity(x, y)
            print(f'  [laporan {b}] {n.overall_score:.3f}  {x!r}\n'
                  f'                    ↔ {y!r}')
        if not args.rinci and len(salah) > 10:
            print(f'  ... {len(salah) - 10} lagi (--rinci)')

    ditarget = ringkas['DITARGETKAN']
    recall_target = ditarget[0] / ditarget[1] if ditarget[1] else 0.0
    masalah = []
    if fmr > FMR_MAKS:
        masalah.append(f'FMR {fmr:.2%} melewati batas {FMR_MAKS:.0%}')
    if recall_target < RECALL_MIN:
        masalah.append(f'recall pola yang ditargetkan {recall_target:.1%} '
                       f'di bawah batas {RECALL_MIN:.0%}')
    if masalah:
        print('\n' + '\n'.join(f'  - {m}' for m in masalah))
        return 1
    print(f'\nRingkasan: recall pola yang ditargetkan {recall_target:.1%} '
          f'(batas {RECALL_MIN:.0%}), FMR {fmr:.2%} (batas {FMR_MAKS:.0%}).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
