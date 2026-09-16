"""
bri_statement.py — Extractor rekening BRI format "LAPORAN TRANSAKSI FINANSIAL"
(Statement of Financial Transaction), yaitu e-statement yang diunduh lewat
BRImo/BRI Internet Banking.

Satu format ini dipakai untuk SEMUA jenis rekening BRI — Giro Umum, BritAma,
BritAma Bisnis, BritAma X, Simpedes, sampai varian SME-nya. Yang berbeda
antar jenis rekening hanyalah isi baris "Nama Produk" di kepala laporan;
tata letak tabel, kepala, dan kaki ringkasannya identik. Karena itu tidak
ada sub-extractor per jenis rekening: jenis rekening dibaca sebagai DATA
(dipakai untuk '_jenis_rekening' dan jadwal biaya admin), bukan sebagai
cabang kode.

Tiga sifat dokumen ini yang menentukan cara membacanya:

1. TABELNYA BERGARIS. Setiap baris transaksi digambar sebagai enam ruas
   garis mendatar (satu per kolom), jadi batas baris DAN batas kolom bisa
   diambil dari geometri dokumennya sendiri, bukan ditebak dari koordinat
   teks. Ini penting karena kolom Uraian Transaksi membungkus ke 2-3 baris
   teks sementara tanggal & nominalnya hanya dicetak sekali: mengelompokkan
   per baris teks akan memecah satu transaksi jadi beberapa.

2. SATU PDF BISA MEMUAT BEBERAPA LAPORAN BULANAN. Berkas gabungan memuat
   beberapa blok "Periode Transaksi : ...", masing-masing dengan kepala
   sendiri, nomor halaman yang mulai lagi dari 1, dan kaki ringkasannya
   sendiri (Saldo Awal / Total Transaksi Debet / Total Transaksi Kredit /
   Saldo Akhir). Blok-blok itu TIDAK selalu urut dan TIDAK selalu
   bersambung — pada PDF referensi ada berkas yang melompati satu bulan
   penuh. Tiap blok karena itu diperiksa sendiri, lalu sambungan antar blok
   (tanggal & saldo) ikut diperiksa supaya periode yang hilang tidak lolos
   diam-diam.

3. KAKI RINGKASAN BISA TERPISAH HALAMAN. Baris judul kaki ("Saldo Awal …")
   dan baris angkanya dicetak sebagai tabel EMPAT kolom tersendiri, dan pada
   sebagian berkas angkanya jatuh ke halaman berikutnya. Kakinya karena itu
   dicari sebagai tabel empat kolom di seluruh halaman blok, bukan sebagai
   baris teks di bawah judulnya.

Halaman yang bukan bagian laporan BRI (mis. PDF bank lain yang ikut
ter-merge dalam satu berkas) dilewati dan DILAPORKAN sebagai peringatan,
bukan dibuang diam-diam.

Extractor ini hanya menghasilkan data mentah sesuai kontrak BaseExtractor —
tidak tahu apa pun soal Excel/styling.
"""

import collections
import re
from datetime import date, timedelta

import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor
from extractors.peringatan import PencatatPeringatan, pencatat_laporan


BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]

# Urutan kolom tabel transaksi, kiri ke kanan.
KOLOM = ('tanggal', 'uraian', 'teller', 'debet', 'kredit', 'saldo')

# Urutan kolom tabel kaki ringkasan, kiri ke kanan.
KOLOM_KAKI = ('saldo_awal', 'total_debet', 'total_kredit', 'saldo_akhir')

# Judul yang tercetak di kepala SETIAP halaman laporan BRI. Dipakai untuk
# memisahkan halaman milik laporan ini dari halaman bank lain yang ikut
# tergabung di berkas yang sama.
PENANDA_BRI = 'LAPORAN TRANSAKSI FINANSIAL'

# Nominal BRI berformat Inggris: "1,234,567.89".
RE_NOMINAL = re.compile(r'^-?[\d,]+\.\d{2}$')

# Sel tanggal: "01/02/26 07:51:03" (tanggal dan jam selalu satu sel).
RE_SEL_TANGGAL = re.compile(r'^(\d{2})/(\d{2})/(\d{2})\s+(\d{2}:\d{2}:\d{2})$')

RE_REKENING = re.compile(r'No\.\s*Rekening\s*:\s*(\d+)')
RE_PRODUK = re.compile(r'Nama\s*Produk\s*:\s*(.+?)(?:\s+Alamat\s*Unit\s*Kerja\s*:|$)',
                       re.MULTILINE)
RE_PERIODE = re.compile(
    r'Periode\s*Transaksi\s*:\s*(\d{2})/(\d{2})/(\d{2})\s*-\s*(\d{2})/(\d{2})/(\d{2})')
RE_HALAMAN = re.compile(r'Halaman\s*(\d+)\s*dari\s*(\d+)')

# Nominal dianggap sama kalau selisihnya di bawah satu sen. Semua angka di
# format ini bersen dua digit, jadi tidak ada pembulatan yang perlu
# ditoleransi.
TOLERANSI = 0.005

# Jadwal pendebetan biaya administrasi rekening, per keluarga produk.
#
# Dikirim ke engine sebagai metadata '_biaya_admin' HANYA untuk keluarga
# produk yang jadwalnya terbukti berulang di PDF referensi:
#
#   BritAma — biaya administrasi didebet tanggal 20 (2 rekening berbeda,
#   4 bulan, seluruhnya tanggal 20; batch biaya/bunga/pajak bulanan
#   rekening BritAma lain di referensi juga jatuh di tanggal yang sama).
#
# Keluarga lain sengaja TIDAK dikirim, jadi pemeriksaannya DILEWATI, bukan
# ditebak:
#   - Giro: tidak satu pun rekening Giro di referensi punya baris biaya
#     administrasi rekening (yang ada hanya biaya bulanan kartu ATM), jadi
#     tidak ada bukti tanggal berapa biaya itu semestinya didebet.
#   - Simpedes: hanya satu rekening di referensi, dan biaya adminnya jatuh
#     di tanggal yang berbeda dari bunga/pajaknya (16 vs 15). Satu contoh
#     terlalu sedikit untuk dijadikan aturan.
JADWAL_BIAYA_ADMIN_BRI = {
    'BRITAMA': (20, 'tanggal 20 tiap bulan (ketentuan BRI untuk rekening BritAma)'),
}

# Label baris biaya/bunga/pajak yang dibakukan (lihat _nama_lawan).
LABEL_BIAYA_ADMIN = 'Biaya Administrasi'
LABEL_BUNGA = 'Bunga Rekening'
LABEL_PAJAK = 'Pajak'

# BRI mencetak baris bank-nya sendiri kadang dalam bahasa Inggris, kadang
# Indonesia — pada SATU rekening yang sama pun bisa berganti antar bulan
# ("Interest on Account" Maret, "Bunga Rekening" April). Keduanya kejadian
# yang sama, jadi namanya dibakukan supaya tidak pecah jadi dua baris di
# Rekap dan supaya bisa dicocokkan persis oleh pemeriksaan bunga/pajak &
# biaya admin di engine.
LABEL_BAKU = {
    'INTEREST ON ACCOUNT': LABEL_BUNGA,
    'BUNGA REKENING': LABEL_BUNGA,
    'TAX': LABEL_PAJAK,
    'PAJAK': LABEL_PAJAK,
    'PAJAK BUNGA SIMPANAN': LABEL_PAJAK,
    'ADMIN FEE': LABEL_BIAYA_ADMIN,
    'BIAYA ADMINISTRASI': LABEL_BIAYA_ADMIN,
    'MONTHLY FEE ATM': 'Biaya Bulanan ATM',
    'BIAYA BULANAN ATM': 'Biaya Bulanan ATM',
}


def _angka(teks):
    """'1,234,567.00' -> 1234567.0. None kalau bukan nominal yang utuh."""
    teks = (teks or '').strip()
    if not RE_NOMINAL.match(teks):
        return None
    try:
        return float(teks.replace(',', ''))
    except ValueError:
        return None


def _tanggal(hari: str, bulan: str, tahun2: str):
    """'01', '02', '26' -> date(2026, 2, 1). None kalau tidak terbaca."""
    try:
        return date(2000 + int(tahun2), int(bulan), int(hari))
    except ValueError:
        return None


class BRIStatementExtractor(PencatatPeringatan, BaseExtractor):

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Peringatan yang terkumpul selama parsing (dibaca app.py / pemanggil).
        self.warnings: list[str] = []
        # Bentuk terstruktur dari peringatan yang sama, untuk metadata
        # '_peringatan' (lihat extractors/peringatan.py).
        self.peringatan: list[dict] = []
        self._cache = None

    def get_file_prefix(self) -> str:
        return 'BRI'

    # ------------------------------------------------------------------ #
    #  GEOMETRI: RUAS GARIS -> BARIS & KOLOM                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _pita(page, jumlah_kolom: int):
        """
        Kolom & garis-garis pembatas baris dari satu tabel di halaman ini.

        Tiap garis mendatar tabel digambar sebagai beberapa ruas — satu ruas
        per kolom — sehingga susunan ruas pada satu garis adalah TANDA TANGAN
        tabelnya. Tabel yang dicari adalah yang tanda tangannya paling sering
        muncul dengan jumlah ruas = `jumlah_kolom`.

        Koordinat dibulatkan ke titik penuh: ruas garis yang sama digambar
        dengan selisih sepersepuluh titik antar halaman (32,2 vs 32,3),
        dan tanpa pembulatan garis pertama & terakhir tiap halaman tidak
        dikenali satu tabel dengan sisanya — baris teratas halaman hilang
        tanpa jejak.

        Mengembalikan (kolom, ys) atau (None, []) kalau halaman ini tidak
        memuat tabel berkolom sekian.
        """
        kasar = collections.defaultdict(set)
        for l in page.lines:
            if abs(l['top'] - l['bottom']) < 0.6:
                kasar[round(l['top'], 1)].add((round(l['x0']), round(l['x1'])))

        # Satu garis tabel bisa tergambar sebagai beberapa ruas yang tepi
        # atasnya beda sepersepuluh titik (756,4 dan 757,0) — lazimnya pada
        # garis PENUTUP tiap halaman. Kalau tidak disatukan dulu, tidak satu
        # pun kelompok itu memuat tanda tangan kolom yang utuh, dan baris
        # TERAKHIR tiap halaman hilang tanpa jejak: mutasinya tidak terhitung
        # padahal saldonya tetap menyambung di halaman berikutnya.
        # Jarak antar baris tabel sendiri belasan titik, jadi penyatuan
        # sedekat ini tidak mungkin menggabungkan dua baris berbeda.
        ruas: dict[float, set] = {}
        wakil = None
        for y in sorted(kasar):
            if wakil is not None and y - wakil <= 1.5:
                ruas[wakil] |= kasar[y]
            else:
                wakil = y
                ruas[wakil] = set(kasar[y])

        # Calon tanda tangan: susunan ruas pada garis yang ruasnya PERSIS
        # sebanyak kolom yang dicari.
        calon = {frozenset(segmen) for segmen in ruas.values()
                 if len(segmen) == jumlah_kolom}
        if not calon:
            return None, []

        # Garis PENUTUP tabel kerap digambar bersama bingkai luar, sehingga
        # ruasnya LEBIH BANYAK daripada tanda tangannya. Yang disyaratkan
        # karena itu MEMUAT tanda tangan, bukan sama persis dengannya — kalau
        # tidak, baris terakhir tiap tabel (termasuk baris angka kaki
        # ringkasan, yang pada sebagian berkas berdiri sendiri di halaman
        # terakhir) tidak pernah terbentuk.
        def garis(tanda):
            return sorted(y for y, segmen in ruas.items() if tanda <= segmen)

        tanda = max(calon, key=lambda t: len(garis(t)))
        ys = garis(tanda)
        # Satu garis saja tidak membentuk baris apa pun (butuh atas & bawah).
        if len(ys) < 2:
            return None, []
        return sorted(tanda), ys

    @staticmethod
    def _sel(words, kolom, nama_kolom, y0: float, y1: float) -> dict:
        """
        Isi tiap kolom untuk satu baris tabel (antara garis y0 dan y1).

        Kata ditempatkan lewat TITIK TENGAHnya, bukan tepi kiri/atas: glyph
        bisa sedikit melewati garis tanpa berarti pindah kolom. Urutan kata
        dalam sel mengikuti (baris teks, kiri-ke-kanan), supaya uraian yang
        membungkus ke beberapa baris teks tersambung sesuai cetakannya.
        """
        isi = {k: [] for k in nama_kolom}
        for w in words:
            cy = (w['top'] + w['bottom']) / 2
            if not (y0 <= cy < y1):
                continue
            cx = (w['x0'] + w['x1']) / 2
            for nama, (x0, x1) in zip(nama_kolom, kolom):
                if x0 <= cx < x1:
                    isi[nama].append((round(w['top'], 1), w['x0'], w['text']))
                    break
        return {k: ' '.join(t for _, _, t in sorted(v)) for k, v in isi.items()}

    # ------------------------------------------------------------------ #
    #  PEMBACAAN DOKUMEN                                                 #
    # ------------------------------------------------------------------ #

    def _parse_document(self) -> dict:
        """
        Baca seluruh PDF sekali, kembalikan {'blok': [...], 'halaman': [...]}.

        Satu blok = satu laporan periode: kepala (identitas + periode), baris
        transaksinya, dan kaki ringkasannya. Baris transaksi dimiliki kepala
        laporan TERAKHIR sebelum halaman itu — bukan ditebak dari bulannya,
        supaya laporan yang periodenya melintasi bulan tetap utuh.
        """
        if self._cache is not None:
            return self._cache

        blok: list[dict] = []
        halaman: list[dict] = []
        asing: list[int] = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for urut, page in enumerate(pdf.pages, start=1):
                teks = page.extract_text() or ''
                if PENANDA_BRI not in teks.upper():
                    asing.append(urut)
                    continue

                kepala = self._parse_kepala(page, teks)
                if kepala:
                    blok.append({
                        'meta': kepala,
                        'periode': kepala['periode'],
                        'baris': [],
                        'ringkasan': None,
                        'halaman': [],
                    })

                m_hal = RE_HALAMAN.search(teks)
                jejak = {
                    'urut': urut,
                    'no_tercetak': int(m_hal.group(1)) if m_hal else None,
                    'total_tercetak': int(m_hal.group(2)) if m_hal else None,
                    'periode': (blok[-1]['periode']['label']
                                if blok and blok[-1]['periode'] else None),
                    'ada_header_kolom': 'Uraian Transaksi' in teks,
                    'jumlah_baris': 0,
                }

                words = page.extract_words()
                baris_halaman = self._baris_halaman(page, words)
                jejak['jumlah_baris'] = len(baris_halaman)
                halaman.append(jejak)

                if baris_halaman and not blok:
                    # Baris transaksi tanpa kepala laporan mana pun di
                    # atasnya: halaman awal berkas hilang/terpotong.
                    self._catat(
                        'Tinggi',
                        'Ada baris transaksi sebelum kepala laporan mana pun',
                        f'Halaman {urut} memuat {len(baris_halaman)} baris transaksi '
                        f'padahal kepala laporan (No. Rekening & Periode Transaksi) '
                        f'belum pernah muncul — halaman awal berkas kemungkinan '
                        f'tidak ikut.',
                        halaman=str(urut),
                    )
                    continue

                for posisi, mentah in enumerate(baris_halaman, start=1):
                    baris = self._jadikan_baris(mentah, urut, posisi, blok[-1])
                    if baris is not None:
                        blok[-1]['baris'].append(baris)

                if blok:
                    blok[-1]['halaman'].append(urut)
                    kaki = self._kaki_halaman(page, words)
                    if kaki and blok[-1]['ringkasan'] is None:
                        blok[-1]['ringkasan'] = kaki

        if asing:
            self._catat(
                'Tinggi',
                'Berkas memuat halaman yang bukan laporan rekening BRI ini',
                f'{len(asing)} halaman tanpa judul "{PENANDA_BRI}" '
                f'(halaman {self._ringkas_halaman(asing)}). Halaman itu tidak '
                f'ikut diekstrak. Periksa apakah PDF rekening lain ikut '
                f'tergabung di berkas yang sama.',
                halaman=self._ringkas_halaman(asing),
            )

        self._cache = {'blok': blok, 'halaman': halaman, 'asing': asing}
        return self._cache

    def _baris_halaman(self, page, words) -> list[dict]:
        """Sel-sel tiap baris tabel transaksi di satu halaman, urut cetakan."""
        kolom, ys = self._pita(page, len(KOLOM))
        if not kolom:
            return []
        hasil = []
        for y0, y1 in zip(ys, ys[1:]):
            sel = self._sel(words, kolom, KOLOM, y0, y1)
            if RE_SEL_TANGGAL.match(sel['tanggal'].strip()):
                hasil.append(sel)
        return hasil

    def _kaki_halaman(self, page, words) -> dict | None:
        """
        Kaki ringkasan resmi blok laporan (Saldo Awal / Total Transaksi Debet
        / Total Transaksi Kredit / Saldo Akhir).

        Dicari sebagai tabel empat kolom, bukan sebagai baris teks di bawah
        judulnya: pada sebagian berkas judul kaki tercetak di akhir satu
        halaman sementara angkanya jatuh ke halaman berikutnya.
        """
        kolom, ys = self._pita(page, len(KOLOM_KAKI))
        if not kolom:
            return None
        for y0, y1 in zip(ys, ys[1:]):
            sel = self._sel(words, kolom, KOLOM_KAKI, y0, y1)
            nilai = {k: _angka(sel[k]) for k in KOLOM_KAKI}
            if all(v is not None for v in nilai.values()):
                return nilai
        return None

    def _jadikan_baris(self, sel: dict, halaman: int, posisi: int,
                       blok: dict) -> dict | None:
        """Satu baris tabel mentah -> satu transaksi."""
        m = RE_SEL_TANGGAL.match(sel['tanggal'].strip())
        if not m:
            return None
        tanggal = _tanggal(m.group(1), m.group(2), m.group(3))
        debet = _angka(sel['debet'])
        kredit = _angka(sel['kredit'])
        saldo = _angka(sel['saldo'])
        uraian = ' '.join((sel['uraian'] or '').split())

        if tanggal is None or debet is None or kredit is None:
            self._catat(
                'Tinggi', 'Ada baris bertanggal yang tidak bisa dibaca utuh',
                f'Halaman {halaman} baris ke-{posisi}: '
                f'tanggal="{sel["tanggal"]}", debet="{sel["debet"]}", '
                f'kredit="{sel["kredit"]}".',
                halaman=str(halaman),
            )
            return None

        arah = 'K' if kredit > 0 else 'D'
        nominal = kredit if arah == 'K' else debet
        return {
            'tanggal': tanggal,
            'waktu': m.group(4),
            'uraian': uraian,
            'teller': ' '.join((sel['teller'] or '').split()),
            'arah': arah,
            'nominal': nominal,
            'mutasi': kredit - debet,
            'saldo': saldo,
            'halaman': halaman,
            'urut': posisi,
            'periode': blok['periode']['label'] if blok['periode'] else None,
        }

    def _parse_kepala(self, page, teks: str) -> dict | None:
        """
        Identitas & periode dari kepala laporan. None kalau halaman ini bukan
        halaman pembuka sebuah blok laporan.
        """
        m_rek = RE_REKENING.search(teks)
        m_per = RE_PERIODE.search(teks)
        if not m_rek or not m_per:
            return None

        mulai = _tanggal(m_per.group(1), m_per.group(2), m_per.group(3))
        selesai = _tanggal(m_per.group(4), m_per.group(5), m_per.group(6))
        m_produk = RE_PRODUK.search(teks)
        return {
            'no_rekening': m_rek.group(1),
            'nama_pemilik': self._nama_pemilik(page),
            'jenis_rekening': (' '.join(m_produk.group(1).split())
                               if m_produk else ''),
            'periode': {
                'mulai': mulai,
                'selesai': selesai,
                'label': (f'{mulai.isoformat()}..{selesai.isoformat()}'
                          if mulai and selesai else None),
            },
        }

    @staticmethod
    def _nama_pemilik(page) -> str:
        """
        Nama pemilik rekening: teks kolom kiri pada baris yang memuat
        "Periode Transaksi".

        Diambil lewat koordinat, bukan lewat pemenggalan teks, karena kedua
        hal itu dicetak pada baris yang SAMA ("RAMOT INTI SELARAS KARGO
        Periode Transaksi : 01/02/26 - 28/02/26") — memenggalnya dari teks
        halaman berarti menebak di mana nama berakhir.
        """
        words = page.extract_words()
        baris = None
        for w in words:
            if w['text'] == 'Periode' and w['x0'] > 300:
                baris = round(w['top'], 1)
                break
        if baris is None:
            return ''
        kiri = [w for w in words
                if abs(round(w['top'], 1) - baris) < 2 and w['x1'] < 300]
        return ' '.join(w['text'] for w in sorted(kiri, key=lambda w: w['x0'])).strip()

    # ------------------------------------------------------------------ #
    #  BARIS GABUNGAN (tanpa duplikat antar blok)                        #
    # ------------------------------------------------------------------ #

    def _baris(self) -> list[dict]:
        """
        Seluruh transaksi dari semua blok, tanpa duplikat antar blok.

        PDF gabungan bisa memuat dua laporan yang periodenya beririsan,
        sehingga transaksi di bagian yang beririsan tercetak dua kali. Baris
        dianggap sama kalau tanggal, jam, nominal, arah, DAN saldo
        berjalannya sama; saldo berjalan berubah di tiap transaksi, jadi
        kunci ini praktis tidak mungkin bentrok. Duplikat DI DALAM satu blok
        tidak pernah dibuang — biaya per-transaksi yang berulang identik
        memang wajar, dan pengulangan yang tidak wajar justru salah satu hal
        yang diperiksa engine.

        Urutannya MENGIKUTI CETAKAN (blok, halaman, posisi baris), bukan
        diurut ulang per tanggal: urutan tanggal yang tidak wajar adalah
        salah satu indikasi yang diperiksa, jadi mengurutkannya ulang di sini
        sama saja menyembunyikannya.
        """
        doc = self._parse_document()
        terlihat: dict = {}
        hasil = []
        for idx, blok in enumerate(doc['blok']):
            for b in blok['baris']:
                kunci = (b['tanggal'], b['waktu'], b['arah'], b['nominal'],
                         b['saldo'])
                sebelumnya = terlihat.get(kunci)
                if sebelumnya is not None and sebelumnya != idx:
                    continue
                terlihat[kunci] = idx
                hasil.append(b)
        return hasil

    def _duplikat_digabung(self) -> int:
        doc = self._parse_document()
        return sum(len(b['baris']) for b in doc['blok']) - len(self._baris())

    # ------------------------------------------------------------------ #
    #  NAMA LAWAN TRANSAKSI                                              #
    # ------------------------------------------------------------------ #

    # "… via BRImo" / "… via BRILink": label kanal di ekor uraian, bukan
    # bagian nama.
    _RE_VIA = re.compile(r'\s+via\s+\S+\s*$', re.IGNORECASE)
    # "Transfer BI-Fast ke BANK X - <norek> - <Nama>" (segmen terakhir = nama;
    # sebagian baris hanya memuat nomor rekening tujuan, tanpa nama).
    _RE_BIFAST = re.compile(r'^Transfer\s+BI-Fast\s+(?:ke|dari)\s+(.+)$', re.IGNORECASE)
    # "Transfer Dari <Nama>" / "Transfer ke <norek>"
    _RE_TRANSFER = re.compile(r'^Transfer\s+(?:dari|ke)\s+(.+)$', re.IGNORECASE)
    # Transfer sesama BRI lewat mobile/internet banking bisnis:
    # "NBMB <PENGIRIM> TO <PENERIMA>" — keduanya dipotong sistem ~16 huruf.
    _RE_NBMB = re.compile(r'^(?:NBMB|IBIZ)\s+(.+?)\s+TO\s+(.+)$')
    # "Pembayaran BRIVA ke <MERCHANT> - <no VA> - <nama pelanggan>"
    _RE_BRIVA = re.compile(r'^Pembayaran\s+BRIVA\s+ke\s+(.+?)\s+-\s', re.IGNORECASE)
    _RE_TOPUP = re.compile(r'^Top\s+Up\s+(\S+)', re.IGNORECASE)
    _RE_QRIS_BAYAR = re.compile(r'^Pembayaran\s+QRIS\s+(.+)$', re.IGNORECASE)
    # "RTGS#PT CHANDRA SAKTI UTA#BMRIIDJA" — nama di antara dua pagar.
    _RE_RTGS = re.compile(r'^RTGS#(.+?)#')
    # "PT BINA USAHA KELUAR ; ESB:INDS:0002800D:…" — nama sebelum titik koma.
    _RE_ESB = re.compile(r'^(.*?)\s*;\s*ESB:', re.IGNORECASE)
    # "ATMSTRPRM 08888 000464106 6557026349 21:31:39 8888116" — transfer
    # lewat kanal ATM. Uraiannya tidak memuat nama siapa pun, tapi segmen
    # ketiga adalah NOMOR REKENING TUJUAN: enam di antaranya terbukti muncul
    # juga sebagai rekening tujuan pada baris BI-Fast di berkas yang sama,
    # lengkap dengan nama pemiliknya. Segmen kedua (9 digit) adalah nomor
    # jejak mesin yang berganti tiap transaksi, jadi bukan itu yang dipakai.
    # Dicocokkan terhadap URAIAN, yang berakhir tepat di nomor rekening —
    # jam posting & teller ID baru ditempelkan belakangan di kolom
    # Keterangan (lihat extract_transaksi), jadi jangan menuntut apa pun
    # sesudah nomornya.
    _RE_ATM_TRANSFER = re.compile(r'^ATMSTRPRM\s+\d+\s+\d+\s+(\d{6,})\s*$')
    # Pindah buku antar rekening: "FROM:<norek> TO:<norek>" dan
    # "FROM <norek> <norek>ATM0".
    _RE_FROM_TO = re.compile(r'^FROM:(\d+)\s+TO:(\d+)')
    _RE_FROM_SPASI = re.compile(r'^FROM\s+(\d+)\s+(\d+)[A-Z]*\d*$')
    # "VANESSA WUTAMI-BANK MANDIRI-" — ekor nama bank tujuan.
    _RE_EKOR_BANK = re.compile(r'\s*-\s*BANK\b.*$', re.IGNORECASE)
    # Token payload: nomor referensi/kode mesin yang berubah tiap transaksi.
    _RE_PAYLOAD = re.compile(r'\d{4,}|[#:_]')
    # Ekor kode alfabet pada payload berpagar: "…#NBMB#TRFMP".
    _RE_KODE_EKOR = re.compile(r'([A-Z]{3,})\s*$')

    def _nama_lawan(self, uraian: str, arah: str) -> str:
        """
        Nama lawan transaksi, dengan jaminan hasilnya selalu bisa dibaca.

        Penguraiannya ada di _nama_lawan_mentah(); di sini hasilnya disaring
        supaya tidak ada token sampah yang lolos jadi "nama". Penyaringan
        ditaruh di satu tempat, bukan ditambal di tiap cabang, karena
        uraian BRI punya banyak bentuk dan cabang baru akan lupa memeriksanya:
        pernah lolos "0" (12 baris) dan ";" (7 baris) sebagai nama, yang lalu
        muncul sebagai entri tersendiri di Rekap seolah lawan transaksi.
        """
        nama = self._nama_lawan_mentah(uraian, arah)
        return nama if self._bermakna(nama) else 'Tidak Teridentifikasi'

    # Nomor rekening terpendek yang masuk akal dipakai sebagai identitas.
    # Di data referensi, nama yang isinya angka saja selalu >= 9 digit
    # (nomor rekening) atau tepat 1 digit (token sampah "0") — tidak ada
    # yang di antaranya, jadi ambangnya tidak memotong data yang sah.
    PANJANG_REKENING_MIN = 6

    @classmethod
    def _bermakna(cls, nama: str) -> bool:
        """
        Benarkah teks ini bisa dibaca sebagai identitas lawan transaksi?

        Ya bila memuat huruf (nama orang/lembaga atau label kanal), atau
        cukup panjang untuk sebuah nomor rekening. Tanda baca dan angka
        satu-dua digit bukan identitas apa pun.
        """
        n = ' '.join((nama or '').split())
        if not n:
            return False
        if re.search(r'[A-Za-z]', n):
            return True
        return len(re.sub(r'\D', '', n)) >= cls.PANJANG_REKENING_MIN

    def _nama_lawan_mentah(self, uraian: str, arah: str) -> str:
        """
        Nama lawan transaksi dari kolom Uraian Transaksi.

        Uraian BRI ditulis mesin pembukuan dengan tata bahasa yang tetap,
        jadi namanya bisa diambil dari strukturnya — bukan ditebak dari
        bentuk huruf. Baris yang memang tidak memuat nama siapa pun (tagihan,
        pembelian token, transaksi kanal ATM/QRIS) diberi label kode
        kanalnya, supaya satu kanal tidak pecah jadi puluhan baris berbeda di
        Rekap hanya karena nomor referensinya berganti tiap transaksi.
        """
        teks = ' '.join((uraian or '').split())
        if not teks:
            return 'Tidak Teridentifikasi'

        # 1. Baris bank sendiri (bunga, pajak, biaya) — namanya dibakukan.
        baku = LABEL_BAKU.get(teks.upper())
        if baku:
            return baku
        if teks.upper().startswith('BIAYA SMS NOTIFIKASI'):
            # "… Sejumlah 7 Notifikasi": jumlahnya berubah tiap baris.
            return 'Biaya SMS Notifikasi'

        # 2. Transfer BI-Fast: segmen terakhir setelah " - ".
        m = self._RE_BIFAST.match(teks)
        if m:
            segmen = [s.strip() for s in m.group(1).split(' - ') if s.strip()]
            if len(segmen) >= 2:
                return self._rapikan(segmen[-1])
            return self._rapikan(segmen[0]) if segmen else 'Tidak Teridentifikasi'

        # 3. Transfer sesama BRI lewat BRImo/BRILink.
        m = self._RE_TRANSFER.match(teks)
        if m:
            return self._rapikan(self._RE_VIA.sub('', m.group(1)))

        # 4. NBMB/IBIZ: arah menentukan sisi mana yang jadi lawan transaksi.
        m = self._RE_NBMB.match(teks)
        if m:
            return self._rapikan(m.group(1) if arah == 'K' else m.group(2))

        # 5. Pembayaran lewat merchant/biller yang namanya tercetak.
        m = self._RE_BRIVA.match(teks)
        if m:
            return self._rapikan(m.group(1))
        m = self._RE_TOPUP.match(teks)
        if m:
            return self._rapikan(m.group(1))
        m = self._RE_QRIS_BAYAR.match(teks)
        if m:
            return self._rapikan(self._RE_VIA.sub('', m.group(1)))
        m = self._RE_RTGS.match(teks)
        if m:
            return self._rapikan(m.group(1))
        m = self._RE_ESB.match(teks)
        if m and self._rapikan(m.group(1)):
            return self._rapikan(m.group(1))

        # 6. Transfer lewat kanal ATM: yang tercetak hanya nomor rekening
        #    tujuan. Nomor itulah identitas lawannya — sama seperti perlakuan
        #    kode terminal pada BNI. Sebelum ini kode kanalnya ("ATMSTRPRM")
        #    yang terpakai, sehingga transfer ke puluhan lawan berbeda
        #    menggumpal jadi satu entri di Rekap.
        m = self._RE_ATM_TRANSFER.match(teks)
        if m:
            return m.group(1)

        # 7. Pindah buku antar rekening: lawannya rekening yang BUKAN
        #    rekening yang sedang diperiksa.
        m = self._RE_FROM_TO.match(teks) or self._RE_FROM_SPASI.match(teks)
        if m:
            return self._lawan_rekening(m.group(1), m.group(2))

        if teks.upper().startswith('PENARIKAN TUNAI'):
            return 'Penarikan Tunai'

        # 8. Tidak ada nama sama sekali: pakai kode kanal/billernya.
        return self._label_kode(teks)

    def _lawan_rekening(self, a: str, b: str) -> str:
        """
        Dari sepasang nomor rekening pindah buku, pilih yang bukan rekening
        yang sedang diperiksa. Dicocokkan lewat awalan karena nomor lawan
        kerap dipotong sistem di digit terakhir.
        """
        blok = self._parse_document()['blok']
        sendiri = blok[0]['meta']['no_rekening'] if blok else ''
        for calon in (a, b):
            if not sendiri or not (calon.startswith(sendiri[:14])
                                   or sendiri.startswith(calon[:14])):
                return calon
        return b

    def _label_kode(self, teks: str) -> str:
        """
        Label untuk baris yang tidak memuat nama siapa pun.

        Diambil bagian TETAP dari uraiannya — kode kanal/biller di depan —
        dengan membuang nomor referensi yang berganti tiap transaksi. Tanpa
        ini, satu kanal (mis. setoran ATM) muncul sebagai puluhan baris
        berbeda di Rekap padahal lawan transaksinya sama-sama tidak diketahui.
        """
        teks = self._RE_VIA.sub('', teks).strip()
        potong = []
        for token in teks.split():
            if self._RE_PAYLOAD.search(token):
                # Kode kanal kerap menempel langsung pada nomor referensinya
                # ("QRIS970780828281#…", "PLN-PRA 1448…"): ambil awalan
                # hurufnya saja.
                awalan = re.match(r'^[A-Za-z][A-Za-z\-]*', token)
                if awalan and not potong:
                    potong.append(awalan.group(0))
                break
            potong.append(token)
        label = self._rapikan(' '.join(potong))
        if label:
            return label
        # Uraian yang seluruhnya payload berpagar ("5326…#1025…#NBMB#TRFMP"):
        # kode di ujung adalah satu-satunya bagian yang tetap.
        m = self._RE_KODE_EKOR.search(teks)
        # Sisanya memang tidak memuat nama siapa pun — hanya nomor referensi
        # yang berganti tiap transaksi. Dibiarkan apa adanya sebagai "tidak
        # teridentifikasi", bukan diisi tebakan: keterangan lengkapnya tetap
        # ada di Sheet Detail Transaksi.
        return m.group(1) if m else 'Tidak Teridentifikasi'

    def _rapikan(self, nama: str) -> str:
        nama = ' '.join((nama or '').split())
        nama = self._RE_EKOR_BANK.sub('', nama)
        nama = nama.strip(' .,-/')
        return ' '.join(nama.split())

    # ------------------------------------------------------------------ #
    #  KONTRAK BaseExtractor                                             #
    # ------------------------------------------------------------------ #

    def extract_no_rekening(self) -> str:
        blok = self._parse_document()['blok']
        return blok[0]['meta']['no_rekening'] if blok else 'unknown'

    def extract_saldo(self) -> dict:
        doc = self._parse_document()
        baris = self._baris()

        hasil = {'_nama_pemilik': '', '_no_rekening': '', '_jenis_rekening': ''}
        if doc['blok']:
            meta = doc['blok'][0]['meta']
            hasil['_nama_pemilik'] = meta['nama_pemilik']
            hasil['_no_rekening'] = meta['no_rekening']
            hasil['_jenis_rekening'] = meta['jenis_rekening']

        # Saldo akhir harian = saldo pada transaksi terakhir hari itu.
        per_hari = {}
        for b in baris:
            if b['saldo'] is not None:
                per_hari[b['tanggal']] = b['saldo']

        # Saldo awal berlaku pada hari pertama periode blok bersangkutan.
        saldo_awal_pada = {}
        tercakup = set()
        for blok in doc['blok']:
            periode = blok['periode']
            if not periode or not periode['mulai'] or not periode['selesai']:
                continue
            if blok['ringkasan']:
                saldo_awal_pada.setdefault(periode['mulai'],
                                           blok['ringkasan']['saldo_awal'])
            hari = periode['mulai']
            while hari <= periode['selesai']:
                tercakup.add(hari)
                hari += timedelta(days=1)

        if not tercakup:
            return hasil

        ember = {}
        awal_bulan = {}
        berjalan = None
        for hari in sorted(tercakup):
            if hari in saldo_awal_pada:
                berjalan = saldo_awal_pada[hari]
            if (hari.year, hari.month) not in awal_bulan:
                awal_bulan[(hari.year, hari.month)] = saldo_awal_pada.get(hari, berjalan)
            if hari in per_hari:
                berjalan = per_hari[hari]
            ember.setdefault((hari.year, hari.month), []).append(
                {'Bulan': BULAN_ORDER[hari.month - 1], 'Tanggal': hari.day,
                 'Saldo Akhir Harian': berjalan})

        for (tahun, bulan), data in ember.items():
            nama_bulan = BULAN_ORDER[bulan - 1]
            hasil[nama_bulan] = {'df': pd.DataFrame(data), 'tahun': str(tahun)}
            if awal_bulan.get((tahun, bulan)) is not None:
                hasil[f'_saldo_awal_{nama_bulan}'] = awal_bulan[(tahun, bulan)]

        jadwal = self._jadwal_biaya_admin(doc)
        if jadwal:
            hasil['_biaya_admin'] = jadwal
        # Baris bunga & pajaknya tidak memuat kode/cabang apa pun, dan
        # namanya sudah dibakukan oleh _nama_lawan — jadi kolom Nama yang
        # dipakai, dicocokkan persis sama.
        hasil['_bunga_pajak'] = {'kolom': 'Nama Pengirim/Penerima',
                                 'bunga': [LABEL_BUNGA],
                                 'pajak': [LABEL_PAJAK]}

        laporan = self.validate()
        if laporan.get('peringatan'):
            hasil['_peringatan'] = laporan['peringatan']
        hasil['_provenance'] = self._provenance(baris)
        hasil['_checksum'] = [
            {'label': p['label'], 'bulan': p['bulan'],
             'expected': p['expected'], 'actual': p['actual'],
             'toleransi': TOLERANSI}
            for p in laporan['periods']
        ]
        return hasil

    def extract_transaksi(self) -> dict:
        """
        Detail transaksi sesuai kontrak BaseExtractor.

        Keterangan Transaksi diakhiri JAM POSTING dan TELLER USER ID yang
        tercetak di baris itu. Keduanya kolom tersendiri di dokumennya dan
        akan hilang sama sekali kalau tidak ikut dibawa — padahal itulah yang
        membedakan dua transaksi yang kebetulan bertanggal, bernominal, dan
        beruraian sama. Tanpa keduanya, pemeriksaan duplikasi di engine
        (tanggal + nominal + keterangan) menandai transaksi sah yang memang
        berulang di hari yang sama, dan pengulangan yang BENAR-BENAR identik
        — yang justru perlu dilihat — tenggelam di antaranya.
        """
        ember = {}
        for b in self._baris():
            nama_bulan = BULAN_ORDER[b['tanggal'].month - 1]
            ember.setdefault(nama_bulan, []).append({
                'Bulan': nama_bulan,
                'Tanggal': b['tanggal'].day,
                'Jenis Mutasi': 'Kredit' if b['arah'] == 'K' else 'Debit',
                'Mutasi': b['nominal'],
                'Nama Pengirim/Penerima': self._nama_lawan(b['uraian'], b['arah']),
                'Keterangan Transaksi': ' '.join(
                    x for x in (b['uraian'], b['waktu'], b['teller']) if x),
            })
        return {b: pd.DataFrame(v) for b, v in ember.items() if v}

    def _jadwal_biaya_admin(self, doc: dict) -> dict | None:
        """
        Jadwal pendebetan biaya administrasi rekening, per bulan yang dicakup
        laporan. None untuk jenis rekening yang jadwalnya belum terbukti di
        data referensi — pemeriksaannya lebih baik DILEWATI daripada ditebak
        (lihat JADWAL_BIAYA_ADMIN_BRI).
        """
        produk = ''
        if doc['blok']:
            produk = (doc['blok'][0]['meta']['jenis_rekening'] or '').upper()
        cocok = next((v for k, v in JADWAL_BIAYA_ADMIN_BRI.items() if k in produk),
                     None)
        if not cocok:
            return None
        tanggal, aturan = cocok

        jadwal = {}
        for blok in doc['blok']:
            periode = blok['periode']
            if not periode or not periode['mulai']:
                continue
            for bulan in self._bulan_periode(periode):
                jadwal[BULAN_ORDER[bulan - 1]] = {'tanggal': tanggal,
                                                  'aturan': aturan}
        return {'label_nama': LABEL_BIAYA_ADMIN, 'jadwal': jadwal} if jadwal else None

    @staticmethod
    def _bulan_periode(periode: dict) -> list[int]:
        """Nomor bulan yang disentuh satu periode laporan."""
        bulan = []
        hari = periode['mulai']
        while hari <= periode['selesai']:
            if hari.month not in bulan:
                bulan.append(hari.month)
            hari += timedelta(days=1)
        return bulan

    def _provenance(self, baris: list) -> dict:
        """
        Jejak cetak dokumen (lihat kontrak '_provenance' di extractors/base.py).

        'teks_mentah' sengaja tidak dikirim: satu-satunya teks panjang per
        baris di format ini adalah kolom Uraian Transaksi, yang memuat
        muatan dari kanal pembayaran (berita QRIS/biller, nama merchant yang
        diketik pedagang). Angka bergaya Indonesia di sana bukan artefak
        dokumen, dan kalau dikirim akan muncul sebagai temuan format nominal
        yang keliru.
        """
        return {
            'halaman': self._parse_document()['halaman'],
            'baris': [{
                'bulan': BULAN_ORDER[b['tanggal'].month - 1],
                'tanggal': b['tanggal'].day,
                'halaman': b['halaman'],
                'urut': b['urut'],
                'periode': b['periode'],
                'mutasi': b['mutasi'],
                'saldo_tercetak': b['saldo'],
                'teks_mentah': None,
            } for b in baris],
        }

    # ------------------------------------------------------------------ #
    #  VALIDASI OTOMATIS (CHECKSUM)                                      #
    # ------------------------------------------------------------------ #

    def validate(self) -> dict:
        """
        Cocokkan hasil parsing dengan kaki ringkasan resmi tiap blok laporan:
        Total Transaksi Debet, Total Transaksi Kredit, dan Saldo Akhir.

        Selain itu diperiksa hal-hal yang tidak butuh angka ringkasan:
          - rantai saldo berjalan di dalam blok (saldo baris sebelumnya +
            mutasi harus sama dengan saldo baris ini);
          - identitas rekening harus sama di seluruh blok;
          - sambungan antar blok: periode harus bersambung dan saldo akhir
            blok sebelumnya harus sama dengan saldo awal blok berikutnya.
        """
        doc = self._parse_document()
        laporan = {'ok': True, 'periods': [],
                   'warnings': list(self.warnings),
                   'peringatan': list(self.peringatan),
                   'duplikat_digabung': self._duplikat_digabung()}
        catat = pencatat_laporan(laporan)

        if not doc['blok'] or not any(b['baris'] for b in doc['blok']):
            laporan['ok'] = False
            catat('Tinggi', 'Tidak ada transaksi yang terbaca dari dokumen',
                  f'Tabel "{PENANDA_BRI}" tidak ditemukan atau kosong.')
            return laporan

        self._periksa_identitas(doc, catat)

        if laporan['duplikat_digabung']:
            catat('Sedang', 'Berkas memuat periode laporan yang tumpang tindih',
                  f'{laporan["duplikat_digabung"]} transaksi tercetak di lebih dari '
                  f'satu blok laporan dan digabung menjadi satu. Total mutasi '
                  f'laporan karena itu lebih kecil dari penjumlahan mentah tiap '
                  f'blok — ini disengaja.')

        for blok in doc['blok']:
            laporan['periods'].append(self._periksa_blok(blok, catat, laporan))

        self._periksa_sambungan(doc, catat)
        return laporan

    def _periksa_blok(self, blok: dict, catat, laporan: dict) -> dict:
        periode = blok['periode']
        label = periode['label'] if periode and periode['label'] else 'Tanpa periode'
        bulan = (BULAN_ORDER[periode['mulai'].month - 1]
                 if periode and periode['mulai'] else '-')
        baris = blok['baris']
        ringkasan = blok['ringkasan'] or {}

        total_debet = sum(b['nominal'] for b in baris if b['arah'] == 'D')
        total_kredit = sum(b['nominal'] for b in baris if b['arah'] == 'K')
        saldo_akhir = next((b['saldo'] for b in reversed(baris)
                            if b['saldo'] is not None), None)

        hasil = {
            'label': label, 'bulan': bulan,
            'expected': {'total_debit': ringkasan.get('total_debet'),
                         'total_credit': ringkasan.get('total_kredit'),
                         'closing': ringkasan.get('saldo_akhir')},
            'actual': {'total_debit': total_debet,
                       'total_credit': total_kredit,
                       'closing': saldo_akhir},
            'checks': {},
        }
        for kunci in ('total_debit', 'total_credit', 'closing'):
            harapan = hasil['expected'][kunci]
            if harapan is None:
                continue
            nyata = hasil['actual'][kunci]
            cocok = nyata is not None and abs(float(nyata) - float(harapan)) <= TOLERANSI
            hasil['checks'][kunci] = cocok
            if not cocok:
                laporan['ok'] = False

        if not blok['ringkasan']:
            catat('Sedang', 'Kaki ringkasan resmi laporan tidak ditemukan',
                  f'Blok {label}: baris Saldo Awal / Total Transaksi Debet / '
                  f'Total Transaksi Kredit / Saldo Akhir tidak terbaca, jadi hasil '
                  f'ekstraksi blok ini tidak bisa dicocokkan dengan angka resmi PDF.',
                  bulan=bulan)

        self._periksa_rantai_saldo(blok, label, bulan, catat, laporan)
        return hasil

    def _periksa_rantai_saldo(self, blok: dict, label: str, bulan: str,
                              catat, laporan: dict) -> None:
        """
        Saldo berjalan harus menyambung DI DALAM satu blok laporan: saldo
        baris sebelumnya + mutasi baris ini = saldo baris ini. Baris pertama
        disambungkan ke Saldo Awal di kaki ringkasan.
        """
        putus = []
        sebelumnya = (blok['ringkasan'] or {}).get('saldo_awal')
        for b in blok['baris']:
            if b['saldo'] is None:
                continue
            if sebelumnya is not None:
                if abs((sebelumnya + b['mutasi']) - b['saldo']) > TOLERANSI:
                    putus.append(b)
            sebelumnya = b['saldo']
        if putus:
            laporan['ok'] = False
            catat('Tinggi', 'Rantai saldo berjalan terputus di dalam satu laporan',
                  f'{len(putus)} baris pada blok {label} saldonya tidak sama dengan '
                  f'saldo baris sebelumnya ditambah mutasinya, mis. halaman '
                  f'{putus[0]["halaman"]} tanggal {putus[0]["tanggal"].isoformat()}.',
                  bulan=bulan, halaman=str(putus[0]['halaman']))

    def _periksa_identitas(self, doc: dict, catat) -> None:
        """Seluruh blok harus milik rekening yang sama."""
        rekening = {b['meta']['no_rekening'] for b in doc['blok']}
        if len(rekening) > 1:
            catat('Tinggi', 'Berkas memuat lebih dari satu nomor rekening',
                  'Nomor rekening yang terbaca: ' + ', '.join(sorted(rekening)) +
                  '. Hasil ekstraksi menggabungkan semuanya sebagai satu rekening.')

    def _periksa_sambungan(self, doc: dict, catat) -> None:
        """
        Sambungan antar blok laporan di satu berkas: periodenya harus
        bersambung, dan saldo akhir blok sebelumnya harus sama dengan saldo
        awal blok berikutnya.

        Blok diurutkan menurut tanggal mulainya, bukan menurut urutan cetak:
        berkas gabungan kerap menempelkan laporan tidak berurutan.
        """
        blok = sorted(
            [b for b in doc['blok'] if b['periode'] and b['periode']['mulai']],
            key=lambda b: b['periode']['mulai'],
        )
        for sebelum, kini in zip(blok, blok[1:]):
            label = kini['periode']['label']
            bulan = BULAN_ORDER[kini['periode']['mulai'].month - 1]

            akhir = sebelum['periode']['selesai']
            mulai = kini['periode']['mulai']
            if akhir and mulai and mulai != akhir + timedelta(days=1):
                selisih = (mulai - akhir).days - 1
                catat('Tinggi', 'Ada rentang tanggal yang tidak dicakup laporan mana pun',
                      f'Laporan {sebelum["periode"]["label"]} berakhir '
                      f'{akhir.isoformat()}, laporan berikutnya baru mulai '
                      f'{mulai.isoformat()} — {selisih} hari tidak tercakup.',
                      bulan=bulan)

            saldo_akhir = (sebelum['ringkasan'] or {}).get('saldo_akhir')
            saldo_awal = (kini['ringkasan'] or {}).get('saldo_awal')
            if (saldo_akhir is not None and saldo_awal is not None
                    and abs(saldo_akhir - saldo_awal) > TOLERANSI):
                catat('Tinggi', 'Saldo antar laporan tidak bersambung',
                      f'Saldo akhir laporan {sebelum["periode"]["label"]} '
                      f'Rp{saldo_akhir:,.2f}, tapi saldo awal laporan {label} '
                      f'Rp{saldo_awal:,.2f} (selisih '
                      f'Rp{saldo_awal - saldo_akhir:,.2f}).',
                      bulan=bulan)

    # ------------------------------------------------------------------ #
    #  BANTU                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ringkas_halaman(halaman: list, maks: int = 8) -> str:
        urut = sorted(set(halaman))
        if len(urut) <= maks:
            return ', '.join(str(h) for h in urut)
        return (', '.join(str(h) for h in urut[:maks]) +
                f', … (+{len(urut) - maks} halaman)')
