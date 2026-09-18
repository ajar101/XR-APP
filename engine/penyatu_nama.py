"""
penyatu_nama.py — Satukan varian penulisan lawan transaksi yang sama.

Satu lawan transaksi kerap muncul dengan beberapa penulisan berbeda di satu
rekening koran yang sama:

    PT BARASENTOSA LESTARI · BARASENTOSA LESTARI · BARASENTOSA LES
    PT WIRAKARYA SAKTI · PT WIRA KARYA SAKTI · PT WIRAKARYA SA
    AEROFOOD INDONESIA, PT · PT. AEROFOOD INDONESIA

Tanpa disatukan, satu pihak pecah jadi beberapa baris di sheet Rekap — dan
akibatnya bukan sekadar tidak rapi. **HHI Score dihitung dari pangsa tiap
nama** (`excel_builder._build_sheet8_summary`), jadi memecah satu pihak besar
jadi tiga baris menurunkan HHI dan membuat rekening yang sebenarnya terpusat
tampak terdiversifikasi. Pada simulasi dengan pola seperti di atas, HHI turun
dari 3350 (Concentrated) ke 1708 (Moderate) — salah ke arah yang justru
berbahaya untuk laporan yang dipakai menilai rekening.

EMPAT TAHAP, DARI YANG PALING PASTI KE YANG PALING RAGU

Urutannya bukan selera. Tiap tahap hanya menerima apa yang TIDAK tuntas di
tahap sebelumnya, sehingga kasus yang punya aturan pasti tidak pernah
diserahkan pada tebakan:

  TAHAP 1  Kunci identik sesudah normalisasi. Perbedaan format — bentuk
  (pasti)  badan usaha di depan atau di belakang, sapaan, spasi, tanda baca.
           "PT HINO FINANCE INDONESIA" dan "HINO FINANCE INDONESIA PT"
           menjadi kunci yang sama persis, jadi langsung satu kelompok.
           Tidak ada perhitungan kemiripan sama sekali, dan tidak perlu.

  TAHAP 2  Kecocokan struktural: potongan mesin. Sebagian kanal memangkas
  (rendah) nama pada panjang tetap, sehingga potongannya menjadi AWALAN dari
           nama penuhnya. Yang membedakannya dari nama yang memang lebih
           pendek dijelaskan di bawah.

  TAHAP 3  Kemiripan huruf (`engine/kemiripan_entitas.py`). Tahap paling
  (ragu)   lemah, dan dijalankan terakhir justru karena itu.

  TAHAP 4  Validasi konteks. Bukan tahap yang MENGGABUNG, melainkan yang
  (veto)   MEMBATALKAN: tiap calon dari tahap 2 dan 3 harus melewatinya
           dulu. Nilai kemiripan hanya tahu SEBERAPA BANYAK dua nama
           berbeda, bukan APA yang membedakannya — dan di rekening koran
           justru jenis perbedaannya yang memutuskan.

APA YANG DIUKUR PADA DATA NYATA (45 PDF REFERENSI, 3.794 NAMA)

Angka ini yang menentukan urutan di atas, bukan sebaliknya:

  · Tahap 1 dan 2 menuntaskan seluruh penggabungan yang benar.
  · Di seluruh 3.794 nama, hanya SATU pasangan mencapai kemiripan >= 0.95 —
    dan pasangan itu ("ANI ROHIMAH" vs "Ibu ANI ROHIMAH") sudah tuntas di
    tahap 1 lewat normalisasi sapaan. Rentang 0.90-0.949: KOSONG.
  · Sebaliknya, pasangan bernilai TERTINGGI di rentang 0.80-an justru
    pasangan yang HARUS tetap terpisah — topup kartu dengan nominal
    berbeda (0.91), dua nomor kontrak berbeda (0.88) — sementara potongan
    mesin yang benar-benar satu pihak ("BARASENTOSA LES" vs "PT
    BARASENTOSA LESTARI") hanya bernilai 0.74.

Jadi pada data ini nilai kemiripan tidak sekadar lemah, ia URUTANNYA
TERBALIK terhadap kebenaran. Itulah sebabnya tahap 3 nyaris tidak pernah
menggabung apa pun sendiri, dan sebabnya tahap 4 ada.

Singkatan ("WKS" untuk Wira Karya Sakti) tetap TIDAK ditangani: tidak ada
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
kebetulan lebih pendek justru hampir selalu berhenti di batas kata.

Aturan yang sama dipakai untuk memilih SATU induk ketika satu awalan cocok
ke beberapa nama. Ini yang dulu membuat satu pihak tetap pecah jadi enam
baris: "PT AEROTRANS SERVICES IND", "...INDON", "...INDONESIA" adalah satu
rantai potongan, tapi ada saudara keempat, "AEROTRANS SERVICES INDON PINBUK
KE BNI OPS" — nama yang berekor keterangan transaksi. Dulu kehadirannya
membatalkan seluruh keluarga itu ("cocok ke beberapa nama yang berbeda").
Sekarang ia disingkirkan lebih dulu, karena ekornya menyambung di BATAS
KATA (`INDON| PINBUK`), bukan menyelesaikan kata yang terpotong
(`INDON|ESIA`) — lalu sisanya menyatu seperti seharusnya, dan si saudara
keempat dilaporkan sebagai kandidat.

Yang tetap dibatalkan adalah percabangan yang dua-duanya menyelesaikan kata
terpotong: "PT GARUDA INDON" cocok ke "...INDONESIA CARGO" dan "...INDONESIA
TBK", dan tidak ada dasar memilih salah satunya.

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

from engine.kemiripan_entitas import (
    AMBANG,
    BADAN_AWAL,
    kunci_varian,
    GABUNG,
    GABUNG_BILA_KONTEKS,
    bukti_potongan,
    calculate_entity_similarity,
    kunci_banding,
    layak_dihitung,
    tanpa_hiasan,
    validasi_konteks,
)

# Panjang kunci minimum sebelum sebuah awalan boleh dianggap potongan.
# Awalan pendek cocok ke terlalu banyak nama untuk bisa dipercaya.
PANJANG_KUNCI_MIN = 8

# HARGA YANG DIBAYAR ATURAN TAHAP 2, DIUKUR
#
# Relasi awalan + potongan di tengah kata adalah satu-satunya sinyal yang
# tersedia, dan ia TIDAK bisa membedakan potongan mesin dari nama lain yang
# kebetulan lebih pendek dan berawalan sama:
#
#     SUKENDAR  ←  potongan "SUKENDARININGTYAS"?  atau nama tersendiri?
#
# Pasangan itu ada di data referensi, dan tidak ada aturan yang bisa
# memastikan. tests/palsu.py mengukur seberapa besar paparannya lewat
# golongan AMBIGU: pada pasangan berbentuk begitu, sistem memutuskan
# menggabungkan 98% kasus. Angka itu BUKAN tingkat kesalahan — ia tingkat
# "keputusan tanpa bukti", dan sengaja dilaporkan di luar FMR supaya
# "keliru" tidak tercampur dengan "tidak mungkin diketahui".
#
# SATU MITIGASI DIUJI LALU DIBUANG: mewajibkan potongan menyisakan minimal
# satu KATA UTUH yang sama ("BARASENTOSA LES" menyisakan "BARASENTOSA";
# "SUKENDAR" tidak menyisakan apa pun). Diukur, ia menurunkan keputusan
# tanpa bukti dari 98,1% ke 87,2% — tapi ikut menghapus 6 penggabungan yang
# BENAR di 44 PDF referensi (105 → 99) dan menurunkan recall potongan dari
# 97,4% ke 95,1%. Sebabnya: mayoritas pasangan ambigu tetap punya kata utuh
# yang sama, jadi aturan itu hampir tidak menyentuh kasus yang dituju
# sementara harganya nyata.
#
# Jadi paparan ini diterima secara sadar, dan ditangani dengan MENAMPAKKAN
# alih-alih menebak: tiap baris Rekap mencantumkan kolom "Varian Nama
# Digabung", dan `python tests/ambang.py` mencetak daftar penggabungan
# paling renggang untuk diaudit manusia.

# Berapa banyak kandidat dari tahap 3 yang ikut dilaporkan. Tahap 1 dan 2
# melaporkan semuanya karena jumlahnya selalu sedikit dan tiap barisnya
# menyangkut relasi awalan yang konkret. Kemiripan huruf lain ceritanya:
# pada satu berkas saja bisa muncul puluhan pasangan bernilai 0.8-an yang
# sebagian besar memang pihak berbeda, dan daftar sepanjang itu justru
# membuat pemeriksa berhenti membaca daftarnya. Yang ditampilkan yang
# tertinggi nilainya.
MAKS_KANDIDAT_FUZZY = 25


def kunci(nama: str) -> str:
    """
    Bentuk baku sebuah nama untuk dibandingkan.

    Huruf besar, tanpa sapaan ("Sdr", "Ibu"), tanpa bentuk badan usaha di
    depan MAUPUN di belakang, tanpa spasi dan tanda baca. Spasi ikut dibuang
    supaya "WIRAKARYA" dan "WIRA KARYA" — yang jelas pihak yang sama — tidak
    dianggap berbeda.

    Aturannya satu-satunya sumber di `engine/kemiripan_entitas.py`, supaya
    normalisasi yang dipakai menggabungkan dan yang dipakai mengukur
    kemiripan tidak bisa berbeda diam-diam.
    """
    return kunci_banding(nama)


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
    teks = tanpa_hiasan(nama_panjang)
    hitung = 0
    for i, ch in enumerate(teks):
        if not ch.isalnum():
            continue
        hitung += 1
        if hitung == panjang_kunci_pendek:
            ekor = teks[i + 1:]
            return bool(ekor) and ekor[0].isalnum()
    return False


# Posisi karakter yang dikosongkan dokumen BNI tertentu. Angkanya absolut —
# tidak tergantung panjang nama — karena sebabnya batas kolom pada field
# lebar tetap di sistem penerbitnya, bukan pemotongan.
POSISI_HILANG = 23          # indeks 0, yaitu karakter ke-24


def rusak_posisi24(nama: str) -> str:
    """
    Bentuk yang muncul kalau karakter ke-24 sebuah nama dikosongkan.

    CACAT YANG DIMODELKAN. Pada sebagian transaksi BNI, karakter ke-24 nama
    lawan transaksi diganti spasi di dokumennya sendiri — bukan oleh
    pembacaan PDF. Diperiksa sampai level glif: spasi itu ada di content
    stream, lebarnya 2,78pt, persis selebar glif "I" yang hilang di
    tempatnya:

        'S'  x0=422.56 x1=429.23
        ' '  x0=429.23 x1=432.01    <- spasi literal selebar satu huruf
        'A'  x0=432.01 x1=438.68

    Jadi "PT HINO FINANCE INDONESIA" tercetak "PT HINO FINANCE INDONES A" —
    sama panjang, satu huruf hilang di posisi tetap.

    Kalau karakter yang terhapus itu bertetangga dengan spasi yang sudah
    ada, keduanya menyatu dan nama justru MEMENDEK satu karakter: "HINO
    FINANCE INDONESIA PT" menjadi "HINO FINANCE INDONESIA T". Bentuk itulah
    yang paling menyesatkan — ia tampak seperti potongan padahal bukan, dan
    tahap 2 benar menolaknya karena memang tidak berelasi awalan.

    KENAPA INI BUKTI, BUKAN KEMIRIPAN. Fungsi ini dipakai dengan menuntut
    kecocokan PERSIS terhadap nama lain yang benar-benar muncul di laporan
    yang sama. Artinya 23 karakter pertama harus identik, seluruh ekor
    sesudah posisi 24 harus identik, dan satu-satunya yang tidak diketahui —
    karakter yang terhapus — dipasok oleh nama utuhnya. Hipotesisnya
    tunggal, jadi tempatnya di tahap yang PASTI, bukan di tahap kemiripan.

    Diukur pada 3.794 nama dari 45 PDF referensi yang sengaja digabung jadi
    satu kolam lintas bank dan lintas dokumen — jauh lebih keras daripada
    keadaan nyata, karena pasangan yang tidak pernah bertemu di satu laporan
    pun ikut diuji: aturan ini menyala TEPAT SEKALI, pada "PT AEROTRANS
    SERVICES I DONESIA" lawan "PT AEROTRANS SERVICES INDONESIA", dan nol
    kasus ambigu. Pasangan itu sebelumnya digabung tahap 3 dengan kemiripan
    0,831 — jadi aturan ini bukan cuma menambah penggabungan, ia MENUKAR
    satu penggabungan berbasis kemiripan dengan penggabungan berbasis
    mekanisme.
    """
    if len(nama) <= POSISI_HILANG:
        return ''
    return re.sub(r'\s+', ' ',
                  nama[:POSISI_HILANG] + ' ' + nama[POSISI_HILANG + 1:]).strip()


def mungkin_rusak(nama: str) -> bool:
    """
    Benarkah nama ini BERBENTUK seperti korban cacat posisi-24?

    Dipakai hanya untuk MELAPORKAN, tidak pernah untuk menggabungkan. Tanpa
    ejaan utuh sebagai pembanding, tidak ada yang bisa MEMASTIKAN huruf apa
    yang terhapus — jadi yang dicari di sini bukan kepastian melainkan
    bentuk yang khas.

    Spasi di posisi ke-24 saja tidak cukup: "SIMSEM GI RTGS/ KLIRING CAB"
    memenuhinya dan sama sekali tidak rusak. Diukur, syarat selonggar itu
    menyala pada 12 dari 12 nama sintetis di tes pemotongan kandidat —
    seluruhnya keliru, dan daftarnya membengkak sampai melewati batas cetak.

    Syarat selanjutnya dibaca dari bentuk sisa yang ditinggalkan cacat ini:
    kata sesudah spasi itu tinggal SATU ATAU DUA HURUF dan merupakan kata
    TERAKHIR ("INDONE IA", "SEJA I", "SOLUTI N"), sementara kata
    sebelumnya masih utuh — minimal empat huruf, seluruhnya huruf.

    Tiap syarat itu ditambahkan karena ada yang disaringnya, dan semuanya
    diukur di 44 PDF referensi. Tanpa ketiganya, laporan ini menghasilkan
    13 baris yang nyaris seluruhnya keliru:

      kata terakhir      membuang "SETIAWA BB SPSI" dan "Sifa Ul Qulub" —
                         di situ potongan pendeknya masih diikuti kata lain,
                         jadi ia kata sungguhan, bukan sisa
      seluruhnya huruf   membuang "PBB P2", "CG 897376-897425",
                         "SOLIKHIN, ST" (koma penanda gelar ikut terbawa)
      kiri minimal 4     membuang "- 032" dan "CK 459654-..."

    Sesudah ketiganya: 1 laporan dari 44 PDF ("The Negotiator Services D"),
    dan "BRI MULTIFINANCE INDONE IA" di berkas BNI baru tetap tertangkap.

    Yang tetap bisa keliru: nama orang berinisial di ekor yang inisialnya
    kebetulan jatuh persis di posisi ke-24. Itu diterima — ia memang
    ambigu, dan ambigu adalah alasan MELAPORKAN, bukan alasan diam. Salah
    lapor di sini harganya satu baris yang perlu dibaca pemeriksa; ia
    tidak bisa merusak HHI, karena fungsi ini tidak pernah menggabungkan.
    """
    if len(nama) <= POSISI_HILANG + 1 or nama[POSISI_HILANG] != ' ':
        return False
    kiri = nama[:POSISI_HILANG].rsplit(' ', 1)[-1]
    ekor = nama[POSISI_HILANG + 1:].split(' ')
    return (len(ekor) == 1 and len(ekor[0]) <= 2 and ekor[0].isalpha()
            and len(kiri) >= 4 and kiri.isalpha())


def _tahap1b_karakter_hilang(per_kunci, kandidat):
    """
    Tahap 1b — karakter yang terhapus di posisi tetap.

    Masih tahap PASTI: yang digabung hanya yang rekonstruksinya cocok
    persis, dan percabangan diserahkan ke pemeriksa alih-alih ditebak.

    Mengembalikan (induk, rusak):
        induk   {kunci kelompok rusak -> kunci kelompok utuh}
        rusak   himpunan nama yang TERBUKTI bentuk rusak — dipakai `_wakil`
                supaya ejaan yang cacat tidak pernah jadi nama resmi
                kelompoknya.
    """
    dari_nama = {n: k for k, anggota in per_kunci.items() for n in anggota}

    # Arahnya dari nama UTUH ke bentuk rusaknya, bukan sebaliknya: yang
    # hilang tidak bisa ditebak dari bentuk rusaknya sendiri, hanya bisa
    # dicocokkan dari kandidat utuh yang memang ada di laporan ini.
    calon = {}
    for utuh in dari_nama:
        r = rusak_posisi24(utuh)
        if r and r != utuh and r in dari_nama:
            calon.setdefault(r, []).append(utuh)

    induk, rusak = {}, set()
    for bentuk_rusak, daftar in sorted(calon.items()):
        if len(daftar) > 1:
            # Dua ejaan utuh berbeda menghasilkan bentuk rusak yang sama.
            # Tidak ada di data referensi (nol kasus dari 3.794 nama), tapi
            # kalau terjadi ia harus tampak, bukan dipilih sembarang.
            kandidat.append((
                bentuk_rusak, ' / '.join(sorted(daftar)),
                'karakter ke-24 tampak terhapus, tapi lebih dari satu ejaan '
                'utuh cocok — tidak bisa dipastikan yang mana'))
            continue

        utuh = daftar[0]
        ka, kb = dari_nama[bentuk_rusak], dari_nama[utuh]
        rusak.add(bentuk_rusak)
        if _akar(ka, induk) != _akar(kb, induk):
            induk[_akar(ka, induk)] = _akar(kb, induk)

    return induk, rusak


def _wakil(varian, rusak=frozenset()) -> str:
    """
    Penulisan mana yang mewakili satu kelompok.

    Ejaan yang TERBUKTI rusak (lihat `rusak_posisi24`) selalu kalah lebih
    dulu, apa pun panjang kuncinya. Tanpa aturan ini kelompok HINO diwakili
    "HINO FINANCE INDONESIA T": hilangnya "P" menyisakan "T" yang bukan
    bentuk badan usaha, sehingga kuncinya justru paling panjang dan ejaan
    yang cacat menang — nama resmi di Rekap jadi salah cetak.

    Diurutkan menurut panjang KUNCI lebih dulu, bukan panjang teks mentah.
    Keduanya bisa berbeda kesimpulan: "SRI NOFITHA TARIGA" dan
    "SRINOFITHA TARIGAN" sama-sama 18 karakter, tapi yang kedua memuat satu
    huruf lebih banyak — ia nama utuhnya, yang pertama terpotong. Panjang
    mentah menganggapnya seri lalu memilih menurut abjad, dan kebetulan
    memilih yang terpotong.

    Sesudah itu bentuk badan usaha di DEPAN lebih dipilih daripada di
    belakang: "PT. AEROFOOD INDONESIA" dan "AEROFOOD INDONESIA, PT" sekarang
    satu kelompok (kuncinya sama), dan yang pertama adalah cara nama badan
    usaha lazim ditulis di Indonesia. Tanpa aturan ini pilihannya jatuh ke
    abjad, yang kebetulan memilih bentuk terbalik.

    Terakhir panjang teks mentah (lebih lengkap tanda bacanya), lalu abjad
    supaya hasilnya sama persis tiap kali dijalankan — laporan yang sama
    harus menghasilkan berkas yang sama.
    """
    def urutan(n):
        return (1 if n in rusak else 0,
                -len(kunci(n)),
                0 if BADAN_AWAL.match(n.strip().upper()) else 1,
                -len(n),
                n)

    return sorted(varian, key=urutan)[0]


class Penyatuan:
    """
    Hasil penyatuan nama.

    Atribut:
        peta      {nama asli -> nama wakil} untuk SEMUA nama yang masuk
        varian    {nama wakil -> daftar nama asli} hanya kelompok >1 varian
        kandidat  [(pendek, panjang, alasan)] pasangan mirip yang TIDAK
                  digabung, beserta alasannya
        kandidat_dipotong
                  berapa kandidat kemiripan huruf yang TIDAK masuk daftar
                  karena MAKS_KANDIDAT_FUZZY. Wajib ikut dilaporkan: daftar
                  yang dipotong tanpa diberi tahu lebih buruk daripada tidak
                  punya daftar, karena pemeriksa tidak tahu ada yang hilang.
    """

    def __init__(self, peta, varian, kandidat, kandidat_dipotong=0):
        self.peta = peta
        self.varian = varian
        self.kandidat = kandidat
        self.kandidat_dipotong = kandidat_dipotong

    def __call__(self, nama: str) -> str:
        return self.peta.get(nama, nama)

    @property
    def jumlah_digabung(self) -> int:
        return sum(len(v) - 1 for v in self.varian.values())


def _tahap2_potongan(per_kunci, kandidat):
    """
    Tahap 2 — awalan yang terpotong di tengah kata.

    Mengembalikan {kunci anak -> kunci induk}. Yang tidak memenuhi syarat
    dicatat ke `kandidat` beserta alasannya, tidak dibuang diam-diam.
    """
    kunci_urut = sorted(per_kunci, key=len)
    induk = {}

    for i, pendek in enumerate(kunci_urut):
        cocok = [p for p in kunci_urut[i + 1:] if p.startswith(pendek)]
        if not cocok:
            continue

        contoh_pendek = min(per_kunci[pendek], key=len)

        def catat(pasangan_kunci, alasan):
            kandidat.append((contoh_pendek, _wakil(per_kunci[pasangan_kunci]),
                             alasan))

        # Awalan sangat pendek tetap dilaporkan, bukan ditolak diam-diam,
        # supaya pemeriksa tahu sistem melihatnya dan memilih tidak memutuskan.
        if len(pendek) < PANJANG_KUNCI_MIN:
            catat(max(cocok, key=len),
                  f'kunci hanya {len(pendek)} huruf — terlalu pendek untuk '
                  f'dipastikan')
            continue

        # Hanya kecocokan yang MENYELESAIKAN kata terpotong yang dianggap
        # satu keluarga. Yang menyambung di batas kata adalah hal lain —
        # nama berekor keterangan transaksi, atau pihak yang memang berbeda.
        # Itulah yang memperbaiki kasus AEROTRANS di docstring modul: satu
        # saudara berekor keterangan ("...INDON| PINBUK KE BNI OPS") dulu
        # membatalkan seluruh keluarganya.
        #
        # Penyaringnya HARUS aturan potong-di-tengah-kata, bukan validasi
        # konteks. Pernah dicoba memakai validasi konteks di sini, dan
        # akibatnya terukur sebagai salah gabung di tests/palsu.py:
        # menyingkirkan satu dari dua calon induk MENGHAPUS sinyal
        # ambiguitasnya, sehingga yang ambigu jadi tampak tunggal lalu
        # digabung. Validasi konteks kembali ke tempatnya sebagai VETO di
        # bawah, bukan penyaring di sini.
        sekeluarga = [p for p in cocok
                      if terpotong_di_tengah_kata(len(pendek),
                                                  _wakil(per_kunci[p]))]
        for p in cocok:
            if p not in sekeluarga:
                catat(p, 'berhenti di batas kata, bukan terpotong di tengah '
                         'kata — bisa jadi memang pihak yang berbeda')
        if not sekeluarga:
            continue

        # Induknya adalah kecocokan TERDEKAT, dan ia hanya sah kalau seluruh
        # kecocokan lain merupakan perpanjangan darinya — artinya ia leluhur
        # bersama, dan percabangan apa pun terjadi lebih dalam (di sana ia
        # dinilai lagi sebagai simpulnya sendiri).
        #
        # Kalau ada dua kecocokan yang langsung bercabang, potongan ini punya
        # dua induk yang sama-sama masuk akal dan tidak ada dasar memilih:
        # "PT GARUDA INDON" cocok ke "...INDONESIA TBK" dan "...INDONESIA
        # CARGO", dan "129001286730" cocok ke dua nomor rekening yang sama
        # panjang tapi berbeda ekornya.
        sekeluarga.sort(key=len)
        terdekat = sekeluarga[0]
        if not all(p.startswith(terdekat) for p in sekeluarga[1:]):
            catat(sekeluarga[-1],
                  f'awalan cocok ke {len(sekeluarga)} nama yang langsung '
                  f'bercabang — tidak ada dasar memilih induknya')
            continue

        # TAHAP 4 sebagai veto, atas induk yang sudah terpilih.
        lolos, alasan = validasi_konteks(contoh_pendek,
                                         _wakil(per_kunci[terdekat]))
        if not lolos:
            catat(terdekat, alasan)
            continue

        induk[pendek] = terdekat

    return induk


def _tahap3_kemiripan(kelompok, kandidat, ambang=AMBANG):
    """
    Tahap 3 — kemiripan huruf, untuk apa yang belum tuntas di tahap 1 dan 2.

    Penggabungan di sini butuh TIGA hal sekaligus, dan nilai kemiripan cuma
    yang pertama:

      1. nilainya mengizinkan (tingkat GABUNG / GABUNG_BILA_KONTEKS),
      2. ada BUKTI bahwa selisihnya kata terpotong (`bukti_potongan`),
      3. tidak ada bukti sebaliknya (`validasi_konteks`).

    Syarat kedua yang membuat tahap ini bisa dipertanggungjawabkan. Tanpanya,
    yang memisahkan "GARUDA INDONESI ↔ PT GARUDA INDONESIA" (0.846, satu
    pihak) dari "MIRNA HASANAH ↔ MIRNA HASANAH KOTO" (0.802, bisa dua orang)
    hanyalah selisih 0,04 dari ambang — yaitu keberuntungan, bukan alasan.

    Lalu ada syarat KEEMPAT yang bukan tentang sepasang nama, melainkan
    tentang akibat menggabungkannya: penggabungan tidak boleh MENJEMBATANI.

    JEMBATAN

    Ketiga syarat di atas menilai satu pasangan. Tapi penggabungan bersifat
    menular: kalau A digabung ke B dan B ke C, maka A dan C berakhir di satu
    baris Rekap — padahal pasangan A-C mungkin tidak pernah lolos syarat apa
    pun. Itulah jembatan, dan bahayanya sama dengan salah gabung biasa:
    satu baris Rekap memuat dua pihak, HHI ikut salah, dan tidak ada
    jejaknya.

    Tahap 2 tidak punya masalah ini karena tiap mata rantainya wajib relasi
    awalan PLUS potongan di tengah kata, jadi seluruh anggota satu kelompok
    pasti serantai. Tahap 3 tidak punya jaminan itu, jadi di sini
    dipasang eksplisit: dua kelompok hanya menyatu kalau SELURUH pasangan
    silang di antaranya lolos sendiri-sendiri. Rantai potongan yang sah
    ("...INDON" ⊂ "...INDONESI" ⊂ "...INDONESIA") tetap boleh menyatu,
    karena tiap pasangannya memang lolos.

    Pasangan diproses dari yang nilainya tertinggi supaya hasilnya tidak
    bergantung urutan nama di dalam laporan — laporan yang sama harus
    menghasilkan berkas yang sama.

    Mengembalikan ({kunci wakil kelompok -> kunci wakil kelompok induk},
    berapa kandidat yang tidak masuk daftar karena batas cetak).
    """
    wakil_kelompok = {k: _wakil(v) for k, v in kelompok.items()}
    urut = sorted(wakil_kelompok)
    tinjau = []
    calon = []

    for i, ka in enumerate(urut):
        for kb in urut[i + 1:]:
            a, b = wakil_kelompok[ka], wakil_kelompok[kb]
            if not layak_dihitung(a, b, ambang):
                continue
            nilai = calculate_entity_similarity(a, b, ambang)
            if nilai.tindakan not in (GABUNG, GABUNG_BILA_KONTEKS):
                # Dilaporkan berdasarkan NILAINYA, bukan berdasarkan tingkat.
                # Pada ambang bawaan `mungkin` dan `tinjau` sama, sehingga
                # tingkat REVIEW tidak pernah tercapai — dan pelaporan yang
                # bersandar pada tingkat itu akan diam-diam berhenti bekerja.
                if nilai.overall_score >= ambang.tinjau:
                    tinjau.append((nilai.overall_score, a, b,
                                   nilai.alasan or
                                   'kemiripan di rentang tinjau'))
                continue

            berbukti, catatan = bukti_potongan(a, b)
            if not berbukti:
                tinjau.append((nilai.overall_score, a, b, catatan))
                continue

            lolos, alasan = validasi_konteks(a, b)
            if not lolos:
                tinjau.append((nilai.overall_score, a, b, alasan))
                continue

            calon.append((nilai.overall_score, ka, kb))

    # ── Penyatuan bertahap, dengan pagar jembatan ──
    berpasangan = {frozenset((ka, kb)) for _n, ka, kb in calon}
    klaster = {k: {k} for k in urut}

    for nilai, ka, kb in sorted(calon, key=lambda t: (-t[0], t[1], t[2])):
        ga, gb = klaster[ka], klaster[kb]
        if ga is gb:
            continue
        belum_diuji = sorted(
            (x, y) for x in ga for y in gb
            if frozenset((x, y)) not in berpasangan)
        if belum_diuji:
            x, y = belum_diuji[0]
            tinjau.append((
                nilai, wakil_kelompok[ka], wakil_kelompok[kb],
                f'menggabungkannya akan sekalian menyatukan '
                f'{wakil_kelompok[x]!r} dengan {wakil_kelompok[y]!r}, yang '
                f'tidak lolos syaratnya sendiri'))
            continue
        satu = ga | gb
        for k in satu:
            klaster[k] = satu

    induk = {}
    sudah = set()
    for k in urut:
        satu = frozenset(klaster[k])
        if len(satu) < 2 or satu in sudah:
            continue
        sudah.add(satu)
        # Kunci terpanjang menjadi induk: pada potongan mesin, yang panjang
        # adalah nama utuhnya.
        akar = max(satu, key=lambda x: (len(x), x))
        for anggota in satu:
            if anggota != akar:
                induk[anggota] = akar

    urut_tinjau = sorted(tinjau, reverse=True)
    for nilai, a, b, alasan in urut_tinjau[:MAKS_KANDIDAT_FUZZY]:
        kandidat.append((a, b, f'kemiripan {nilai:.3f} — {alasan}; '
                              f'di bawah ambang gabung otomatis '
                              f'({ambang.mungkin:.2f})'))

    return induk, max(0, len(urut_tinjau) - MAKS_KANDIDAT_FUZZY)


def _akar(kunci_awal, induk):
    terlihat = {kunci_awal}
    k = kunci_awal
    while k in induk:
        k = induk[k]
        if k in terlihat:      # jaga-jaga, seharusnya tak mungkin
            break
        terlihat.add(k)
    return k


def satukan(nama_unik, abaikan=frozenset(), ambang=AMBANG) -> Penyatuan:
    """
    Kelompokkan varian penulisan yang merujuk pihak yang sama.

    Args:
        nama_unik: seluruh nama lawan transaksi yang muncul di laporan.
        abaikan:   nama yang tidak boleh disentuh — label kategori seperti
                   "Biaya Administrasi" atau "Tidak Teridentifikasi". Itu
                   bukan lawan transaksi, dan meleburnya membuang informasi.
        ambang:    `AmbangKemiripan` untuk tahap 3. Sengaja bisa diganti per
                   pemanggilan, bukan cuma lewat mengedit modul: menyetel
                   ambang adalah hal yang perlu DIUKUR pada data nyata
                   (lihat tests/ambang.py), dan pengukuran itu harus lewat
                   jalur kode yang sama dengan produksi — bukan lewat
                   menambal nilai bawaan dari luar.

    Returns:
        Penyatuan
    """
    nama_unik = [n for n in dict.fromkeys(nama_unik) if n and n.strip()]
    ikut = [n for n in nama_unik if n not in abaikan]
    kandidat = []

    # ── TAHAP 1: kunci identik sesudah normalisasi ──
    #
    # Satu nama bisa membawa lebih dari satu kunci kalau ia bergelar (lihat
    # kunci_varian). Yang berbagi kunci apa pun masuk satu kelompok, dan
    # kelompoknya diwakili kunci TERPENDEK — yaitu bentuk yang paling
    # ternormalisasi, dan itu memang arti "kunci" di modul ini.
    #
    # Pilihan ini terukur, bukan selera. Memakai kunci terpanjang membuat
    # kelompok bergelar berkunci "...SSI", dan potongan mesin dari nama
    # dasarnya tidak lagi berelasi awalan dengannya — tahap 2 jadi buta
    # terhadapnya. Diukur di tests/palsu.py, itu menurunkan penggabungan
    # pasangan AMBIGU dari 98% ke 57%, yang terdengar bagus sampai
    # disadari bahwa yang hilang juga potongan yang BENAR.
    milik = {}
    for n in ikut:
        semua = [k for k in kunci_varian(n) if k]
        if not semua:
            continue
        gabungan = {n}
        kunci_gabungan = set(semua)
        for k in semua:
            lain = milik.get(k)
            if lain is not None:
                gabungan |= lain[0]
                kunci_gabungan |= lain[1]
        pasangan = (gabungan, kunci_gabungan)
        for k in kunci_gabungan:
            milik[k] = pasangan

    per_kunci = {}
    for gabungan, kunci_gabungan in {id(v): v for v in milik.values()}.values():
        utama = min(kunci_gabungan, key=lambda k: (len(k), k))
        per_kunci[utama] = sorted(gabungan, key=lambda n: (-len(n), n))

    # ── TAHAP 1B: karakter yang terhapus di posisi tetap ──
    #
    # Masih tahap pasti, dan dijalankan SEBELUM tahap 2 justru karena bentuk
    # rusaknya bisa menyamar jadi potongan: "HINO FINANCE INDONESIA T"
    # memendek satu karakter, persis seperti nama yang terpangkas kanal.
    # Kalau tahap 2 yang menanganinya lebih dulu, yang didapat penggabungan
    # tanpa bukti; di sini ia dapat bukti rekonstruksi yang persis.
    induk_hilang, rusak = _tahap1b_karakter_hilang(per_kunci, kandidat)
    if induk_hilang:
        digabung = {}
        for k, anggota in per_kunci.items():
            digabung.setdefault(_akar(k, induk_hilang), []).extend(anggota)
        per_kunci = {k: sorted(v, key=lambda n: (-len(n), n))
                     for k, v in digabung.items()}

    # ── TAHAP 2: awalan yang terpotong di tengah kata (+ TAHAP 4) ──
    induk = _tahap2_potongan(per_kunci, kandidat)

    kelompok = {}
    for k, anggota in per_kunci.items():
        kelompok.setdefault(_akar(k, induk), []).extend(anggota)

    # ── TAHAP 3: kemiripan huruf (+ TAHAP 4) ──
    induk_fuzzy, dipotong = _tahap3_kemiripan(kelompok, kandidat, ambang)
    if induk_fuzzy:
        gabungan = {}
        for k, anggota in kelompok.items():
            gabungan.setdefault(_akar(k, induk_fuzzy), []).extend(anggota)
        kelompok = gabungan

    # ── Susun kelompok akhir ──
    peta, varian = {}, {}
    for anggota in kelompok.values():
        w = _wakil(anggota, rusak)
        for n in anggota:
            peta[n] = w
        if len(anggota) > 1:
            varian[w] = sorted(anggota, key=lambda n: (-len(n), n))

    for n in nama_unik:
        peta.setdefault(n, n)

    # ── Rusak tanpa pasangan: dilaporkan, tidak digabung ──
    #
    # Nama yang berbentuk korban cacat posisi-24 tapi ejaan utuhnya tidak
    # ada di laporan ini. Tidak ada yang bisa memulihkannya — huruf yang
    # hilang tidak tersimpan di mana pun — jadi yang bisa dilakukan hanya
    # memberitahu. Dibiarkan senyap, pemeriksa akan membaca "BRI
    # MULTIFINANCE INDONE IA" sebagai nama yang memang begitu.
    #
    # Yang disembunyikan HANYA yang sudah terbukti rusak lalu dipulihkan
    # (`rusak`) — di situ pertanyaannya sudah terjawab dan nama resminya
    # sudah ejaan yang utuh. Menyatu dengan ejaan lain saja TIDAK cukup:
    # "BRI MULTIFINANCE INDONE IA" menyatu dengan potongannya "BRI
    # MULTIFINANCE IND", tapi ia tetap ejaan paling lengkap di kelompok
    # itu — jadi nama yang tercetak di Rekap tetap kehilangan satu huruf,
    # dan itu justru yang perlu diketahui pemeriksa.
    for n in sorted(ikut):
        if mungkin_rusak(n) and n not in rusak:
            kandidat.append((
                n, '',
                'ada spasi tepat di karakter ke-24 — bisa jadi huruf di posisi '
                'itu terhapus dokumen (lihat rusak_posisi24), tapi ejaan utuhnya '
                'tidak muncul di laporan ini; bisa juga batas kata yang sah'))

    return Penyatuan(peta, varian, kandidat, dipotong)
