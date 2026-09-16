"""
penyatu_nama.py — Satukan varian penulisan lawan transaksi yang sama.

Satu lawan transaksi kerap muncul dengan beberapa penulisan berbeda di satu
rekening koran yang sama:

    PT BARASENTOSA LESTARI · BARASENTOSA LESTARI · BARASENTOSA LES
    PT WIRAKARYA SAKTI · PT WIRA KARYA SAKTI · PT WIRAKARYA SA

Tanpa disatukan, satu pihak pecah jadi beberapa baris di sheet Rekap — dan
akibatnya bukan sekadar tidak rapi. **HHI Score dihitung dari pangsa tiap
nama** (`excel_builder._build_sheet8_summary`), jadi memecah satu pihak besar
jadi tiga baris menurunkan HHI dan membuat rekening yang sebenarnya terpusat
tampak terdiversifikasi. Pada simulasi dengan pola seperti di atas, HHI turun
dari 3350 (Concentrated) ke 1708 (Moderate) — salah ke arah yang justru
berbahaya untuk laporan yang dipakai menilai rekening.

DUA TAHAP, KEDUANYA DETERMINISTIK

Modul ini TIDAK memakai kemiripan huruf (jarak edit/fuzzy). Pada data
referensi, penyebabnya bukan salah ketik melainkan dua hal yang punya aturan
pasti:

  TAHAP 1  Perbedaan format. "PT BARASENTOSA LESTARI" vs "BARASENTOSA
           LESTARI" vs "PT.TRI PUTRA ERGUNA" — beda bentuk badan usaha,
           spasi, atau tanda baca. Disamakan lewat normalisasi, nol risiko.

  TAHAP 2  Pemotongan sistem. Sebagian kanal memotong nama pada panjang
           tetap, sehingga potongannya menjadi AWALAN dari nama penuhnya.

Singkatan ("WKS" untuk Wira Karya Sakti) sengaja TIDAK ditangani: tidak ada
hubungan huruf antara singkatan dan kepanjangannya, jadi tidak ada aturan
yang bisa menyimpulkannya. Itu butuh daftar alias yang diisi manusia.

APA YANG MEMBEDAKAN POTONGAN DARI NAMA YANG MEMANG LEBIH PENDEK

Relasi awalan saja TIDAK cukup, dan ini pelajaran dari mencobanya pada data
nyata. "CITRA PERISAI" adalah awalan "CITRA PERISAI LINTASINDO", tapi
menggabungkannya berarti menebak — bisa jadi memang dua pihak berbeda.

Tanda khas pemotongan mesin adalah potongannya jatuh **di tengah kata**:

    BARASENTOSA LES | TARI      ← terpotong di tengah "LESTARI"  → potongan
    CITRA PERISAI   | LINTASINDO ← berhenti di batas kata        → BUKAN

Mesin memotong pada hitungan karakter, tidak peduli batas kata. Nama yang
kebetulan lebih pendek justru hampir selalu berhenti di batas kata. Diuji
pada sembilan pasangan dari data nyata, aturan ini memisahkan keduanya
dengan tepat — tanpa perlu menebak berapa panjang batas potong tiap bank.

Percobaan sebelumnya menebak batas potong dari sebaran panjang nama.
Pendekatan itu dibuang: pada satu berkas Mandiri dengan 341 nama, ia
"mendeteksi" 16 batas potong berbeda padahal Mandiri tidak memotong sama
sekali (nama terpanjangnya 110 karakter), dan akibatnya 26 nama digabung
tanpa dasar.

PENGGABUNGAN YANG SALAH LEBIH BURUK DARIPADA TIDAK DIGABUNG

Kalau dua pihak yang sebenarnya berbeda disatukan, HHI ikut salah ke arah
sebaliknya — dan tidak ada jejaknya di laporan. Yang tidak memenuhi syarat
karena itu tidak dibuang melainkan dilaporkan sebagai KANDIDAT, lengkap
dengan alasannya, supaya pemeriksa yang memutuskan.
"""

import re

# Bentuk badan usaha di awal nama. Dibuang saat menormalkan karena dokumen
# kerap mencetaknya tidak konsisten ("PT X", "PT. X", "PT.X", "X") untuk
# pihak yang sama. HANYA di awal — "ASIA JAYA LESTARI PT" tidak terpotong.
#
# Pemisah (titik atau spasi) WAJIB ada, supaya nama yang kebetulan berawalan
# huruf yang sama ("PTX SEJAHTERA", "CVITO MANDIRI") tidak ikut terpotong.
BENTUK_BADAN = re.compile(
    r'^(PT|CV|UD|PD|NV|FA|KOPERASI|KOP|YAYASAN|PERUM|PERSERO)'
    r'(?:\s*\.\s*|\s+)',
    re.IGNORECASE)

BUKAN_ALFANUMERIK = re.compile(r'[^A-Z0-9]')

# Panjang kunci minimum sebelum sebuah awalan boleh dianggap potongan.
# Awalan pendek cocok ke terlalu banyak nama untuk bisa dipercaya.
PANJANG_KUNCI_MIN = 8


def kunci(nama: str) -> str:
    """
    Bentuk baku sebuah nama untuk dibandingkan.

    Huruf besar, tanpa bentuk badan usaha di depan, tanpa spasi dan tanda
    baca. Spasi ikut dibuang supaya "WIRAKARYA" dan "WIRA KARYA" — yang jelas
    pihak yang sama — tidak dianggap berbeda.
    """
    tanpa_badan = BENTUK_BADAN.sub('', (nama or '').strip().upper())
    return BUKAN_ALFANUMERIK.sub('', tanpa_badan)


def terpotong_di_tengah_kata(panjang_kunci_pendek: int, nama_panjang: str) -> bool:
    """
    Benarkah nama panjang ini terpotong di TENGAH KATA pada posisi itu?

    Inilah pembeda antara potongan mesin dan nama yang memang lebih pendek.
    Ditelusuri pada teks ASLI (bukan kunci) karena yang dicari justru batas
    katanya, dan kunci sudah membuang seluruh spasi.

    Mengembalikan True bila karakter tepat SESUDAH awalan bersama masih
    alfanumerik — artinya kata di posisi itu terpotong di tengah.
    """
    if panjang_kunci_pendek <= 0:
        return False
    teks = BENTUK_BADAN.sub('', (nama_panjang or '').strip().upper())
    hitung = 0
    for i, ch in enumerate(teks):
        if not ch.isalnum():
            continue
        hitung += 1
        if hitung == panjang_kunci_pendek:
            ekor = teks[i + 1:]
            return bool(ekor) and ekor[0].isalnum()
    return False


def _wakil(varian) -> str:
    """
    Penulisan mana yang mewakili satu kelompok.

    Diurutkan menurut panjang KUNCI lebih dulu, bukan panjang teks mentah.
    Keduanya bisa berbeda kesimpulan: "SRI NOFITHA TARIGA" dan
    "SRINOFITHA TARIGAN" sama-sama 18 karakter, tapi yang kedua memuat satu
    huruf lebih banyak — ia nama utuhnya, yang pertama terpotong. Panjang
    mentah menganggapnya seri lalu memilih menurut abjad, dan kebetulan
    memilih yang terpotong.

    Sesudah itu baru panjang teks mentah (lebih lengkap tanda bacanya), lalu
    abjad supaya hasilnya sama persis tiap kali dijalankan — laporan yang
    sama harus menghasilkan berkas yang sama.
    """
    return sorted(varian, key=lambda n: (-len(kunci(n)), -len(n), n))[0]


class Penyatuan:
    """
    Hasil penyatuan nama.

    Atribut:
        peta      {nama asli -> nama wakil} untuk SEMUA nama yang masuk
        varian    {nama wakil -> daftar nama asli} hanya kelompok >1 varian
        kandidat  [(pendek, panjang, alasan)] awalan yang TIDAK digabung
    """

    def __init__(self, peta, varian, kandidat):
        self.peta = peta
        self.varian = varian
        self.kandidat = kandidat

    def __call__(self, nama: str) -> str:
        return self.peta.get(nama, nama)

    @property
    def jumlah_digabung(self) -> int:
        return sum(len(v) - 1 for v in self.varian.values())


def satukan(nama_unik, abaikan=frozenset()) -> Penyatuan:
    """
    Kelompokkan varian penulisan yang merujuk pihak yang sama.

    Args:
        nama_unik: seluruh nama lawan transaksi yang muncul di laporan.
        abaikan:   nama yang tidak boleh disentuh — label kategori seperti
                   "Biaya Administrasi" atau "Tidak Teridentifikasi". Itu
                   bukan lawan transaksi, dan meleburnya membuang informasi.

    Returns:
        Penyatuan
    """
    nama_unik = [n for n in dict.fromkeys(nama_unik) if n and n.strip()]
    ikut = [n for n in nama_unik if n not in abaikan]

    # ── TAHAP 1: kunci identik sesudah normalisasi ──
    per_kunci = {}
    for n in ikut:
        k = kunci(n)
        if k:
            per_kunci.setdefault(k, []).append(n)

    # ── TAHAP 2: awalan yang terpotong di tengah kata ──
    kunci_urut = sorted(per_kunci, key=len)
    induk = {}
    kandidat = []

    for i, pendek in enumerate(kunci_urut):
        cocok = [p for p in kunci_urut[i + 1:] if p.startswith(pendek)]
        if not cocok:
            continue

        cocok.sort(key=len)
        target = cocok[-1]
        contoh_pendek = min(per_kunci[pendek], key=len)
        wakil_target = _wakil(per_kunci[target])

        def catat(alasan):
            kandidat.append((contoh_pendek, wakil_target, alasan))

        # Awalan sangat pendek tetap dilaporkan, bukan ditolak diam-diam,
        # supaya pemeriksa tahu sistem melihatnya dan memilih tidak memutuskan.
        if len(pendek) < PANJANG_KUNCI_MIN:
            catat(f'kunci hanya {len(pendek)} huruf — terlalu pendek untuk '
                  f'dipastikan')
            continue

        # Beberapa kecocokan masih boleh digabung SELAMA semuanya satu rantai
        # (A awalan B, B awalan C) — ujung terpanjang adalah nama penuhnya.
        # Yang tidak serantai berarti dua pihak berbeda yang kebetulan
        # berawalan sama, dan tidak ada dasar memilih salah satunya.
        if not all(cocok[j + 1].startswith(cocok[j]) for j in range(len(cocok) - 1)):
            catat(f'awalan cocok ke {len(cocok)} nama yang berbeda satu sama lain')
            continue

        # Syarat penentu: potongannya harus jatuh di tengah kata.
        if not terpotong_di_tengah_kata(len(pendek), per_kunci[target][0]):
            catat('berhenti di batas kata, bukan terpotong di tengah kata — '
                  'bisa jadi memang pihak yang berbeda')
            continue

        induk[pendek] = target

    # ── Susun kelompok akhir ──
    def akar(k):
        terlihat = {k}
        while k in induk:
            k = induk[k]
            if k in terlihat:      # jaga-jaga, seharusnya tak mungkin
                break
            terlihat.add(k)
        return k

    kelompok = {}
    for k, anggota in per_kunci.items():
        kelompok.setdefault(akar(k), []).extend(anggota)

    peta, varian = {}, {}
    for anggota in kelompok.values():
        w = _wakil(anggota)
        for n in anggota:
            peta[n] = w
        if len(anggota) > 1:
            varian[w] = sorted(anggota, key=lambda n: (-len(n), n))

    for n in nama_unik:
        peta.setdefault(n, n)

    return Penyatuan(peta, varian, kandidat)
