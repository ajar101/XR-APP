"""
bni_inquiry.py — Extractor rekening BNI format "TRANSACTION INQUIRY".

Format ini dikenali dari judul "TRANSACTION INQUIRY" di kepala tiap halaman
plus tabel berkolom:

    No. | Post Date | Branch | Journal No. |
    Description | Amount | Db/Cr | Balance

Empat sifat dokumen ini yang menentukan cara membacanya:

1. TABELNYA BERGARIS, dan BARIS HEADER-nya menyebutkan nama kolomnya. Tiap
   sel dicetak sebagai kotak (rect) tersendiri, jadi batas baris DAN batas
   kolom diambil dari geometri dokumennya, bukan ditebak dari koordinat
   teks — penting karena satu transaksi memakan 1–4 baris teks (kolom
   Branch dan Description membungkus).

   Yang dipakai HANYA kotak yang berada pada atau di bawah baris header
   kolom. Di atasnya ada blok "Account Information" yang juga digambar
   sebagai kotak-kotak, dengan lebar kolom yang sama sekali berbeda; pada
   halaman yang transaksinya sedikit, kotak blok itu lebih banyak daripada
   kotak tabelnya, sehingga "kotak yang paling sering muncul" saja akan
   memilih kolom yang salah. Baris header dipakai sebagai jangkarnya karena
   ia satu-satunya penanda yang MENYEBUT dirinya tabel transaksi.

2. KOLOM AMOUNT DICETAK BERSIH. Berbeda dengan format ACCOUNT STATEMENT
   (lihat bni_statement.py) yang nominalnya rusak karena efek tebal, di
   sini nominalnya utuh. Karena itu nominal diambil dari kolom Amount —
   angka yang memang dinyatakan dokumen sebagai nilai transaksi — dan
   kolom Balance dipakai sebagai PEMERIKSANYA, bukan sebaliknya. Rantai
   saldo itu diserahkan ke engine lewat metadata '_provenance', jadi baris
   yang nominal dan saldonya tidak sejalan muncul sebagai temuan, bukan
   diam-diam diperbaiki extractor.

   Teks kolom Description kadang meluber melewati batas kolom Amount
   ("TRANSFER DARI | ANDALAN AUTO PRIMA PT -"), jadi yang diambil potongan
   PERTAMA yang berbentuk nominal utuh, bukan potongan pertama begitu saja.

3. SATU PDF BISA MEMUAT BEBERAPA HASIL INQUIRY, DAN URUTANNYA TIDAK
   KRONOLOGIS. Berkas gabungan memuat beberapa blok "Period : ... - ...",
   masing-masing dengan Beginning Balance/Total Debit/Total Credit sendiri
   dan penomoran baris yang mulai lagi dari 1. Satu PDF referensi memuat
   blok Desember 2024, lalu Februari 2025, baru Januari 2025 — wajar, karena
   tiap blok adalah hasil query terpisah yang digabung. Karena itu sambungan
   antar periode diperiksa setelah blok DIURUTKAN kronologis; kalau
   diperiksa menurut urutan cetak, tiap berkas seperti itu akan melaporkan
   "ada rentang tanggal yang tidak dicakup" yang sebenarnya tidak ada.

4. PENOMORAN BARIS ("No.") BERURUT DARI 1 DI TIAP BLOK. Ini jaminan
   kelengkapan yang tidak dimiliki format BNI lain: satu baris yang dihapus
   dari dokumen meninggalkan lubang di penomorannya, dan itu terlihat
   bahkan ketika seluruh angka ringkasannya ikut disesuaikan.

Dokumen ini TIDAK mencetak nomor halaman dan TIDAK mencetak Ending Balance.
Saldo akhir yang diharapkan karena itu dihitung dari tiga angka yang memang
tercetak di kepala tiap blok (Beginning Balance − Total Debit + Total
Credit), lalu dibandingkan dengan saldo baris terakhir.

Extractor ini hanya menghasilkan data mentah sesuai kontrak BaseExtractor —
tidak tahu apa pun soal Excel/styling.
"""

import calendar
import re
from datetime import date, timedelta

import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor
from extractors.bni_nama import NamaLawanBNI
from extractors.peringatan import PencatatPeringatan, pencatat_laporan


BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]

# Singkatan bulan Inggris pada baris "Period : 01-Nov-2025 - 30-Nov-2025".
BULAN_EN = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}

# Urutan kolom tabel, kiri ke kanan. Dipakai untuk memberi nama pada kotak
# sel yang ditemukan di halaman (lihat _sel_halaman).
KOLOM = ('no', 'tanggal', 'branch', 'journal',
         'keterangan', 'nominal', 'dbcr', 'saldo')

RE_TANGGAL = re.compile(r'^(\d{2})/(\d{2})/(\d{4})$')
RE_NOMINAL = re.compile(r'^[\d,]+\.\d{2}$')

# Baris header kolom — jangkar letak tabel di tiap halaman (lihat §1).
RE_JUDUL_KOLOM = re.compile(
    r'^No\.\s+Post\s+Date\b.*\bDescription\b.*\bBalance$')

# "Account : 773392009 / MATARI JELAJAH INDONESIA PT ( IDR )". Spasi sebelum
# '/' bisa tidak ada, dan mata uangnya bisa kosong — keduanya ditemui di PDF
# referensi, jadi keduanya ditoleransi di sini alih-alih menggagalkan
# pembacaan seluruh dokumen.
RE_REKENING = re.compile(
    r'Account\s*:\s*(\d+)\s*/\s*(.+?)\s*\(\s*([A-Za-z]*)\s*\)')
RE_PERIODE = re.compile(
    r'Period\s*:\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s*-\s*'
    r'(\d{1,2})-([A-Za-z]{3})-(\d{4})')
RE_AWAL = re.compile(r'Beginning\s*Balance\s*:\s*([\d,]+\.\d{2})')
RE_TOT_DEB = re.compile(r'Total\s*Debit\s*:\s*([\d,]+\.\d{2})')
RE_TOT_KRE = re.compile(r'Total\s*Credit\s*:\s*([\d,]+\.\d{2})')

# Nominal dianggap sama kalau selisihnya di bawah satu sen. Semua angka di
# format ini bersen dua digit, jadi tidak ada pembulatan yang perlu ditoleransi.
TOLERANSI = 0.005


def _angka(teks):
    """'1,234,567.00' -> 1234567.0. None kalau bukan nominal yang utuh."""
    teks = (teks or '').strip()
    if not RE_NOMINAL.match(teks):
        return None
    try:
        return float(teks.replace(',', ''))
    except ValueError:
        return None


def _tanggal_periode(hari: str, bulan: str, tahun: str):
    """'01', 'Nov', '2025' -> date(2025, 11, 1). None kalau tidak terbaca."""
    nomor = BULAN_EN.get(bulan.upper())
    if not nomor:
        return None
    try:
        return date(int(tahun), nomor, int(hari))
    except ValueError:
        return None


class BNIInquiryExtractor(NamaLawanBNI, PencatatPeringatan, BaseExtractor):
    """
    Nama lawan transaksi dibaca pipeline bersama extractors/bni_nama.py —
    kolom Description format ini bertata bahasa sama dengan ACCOUNT
    STATEMENT, jadi aturannya tidak disalin ke dua tempat.
    """

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Peringatan yang terkumpul selama parsing (dibaca app.py / pemanggil).
        self.warnings: list[str] = []
        # Bentuk terstruktur dari peringatan yang sama, untuk metadata
        # '_peringatan' (lihat extractors/peringatan.py).
        self.peringatan: list[dict] = []
        self._cache = None

    def get_file_prefix(self) -> str:
        return 'BNI'

    # ------------------------------------------------------------------ #
    #  GEOMETRI: KOTAK SEL -> BARIS & KOLOM                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _baris_teks(kata: list) -> list:
        """Kelompokkan kata jadi baris teks (toleransi 3pt), kiri ke kanan."""
        baris = {}
        for w in kata:
            baris.setdefault(round(w['top'] / 3), []).append(w)
        return [sorted(baris[k], key=lambda w: w['x0']) for k in sorted(baris)]

    @classmethod
    def _atas_tabel(cls, kata: list):
        """
        Tepi atas baris header kolom, atau None kalau halaman ini tidak
        memuat tabel transaksi.

        Semua geometri tabel diukur dari sini — lihat §1 di docstring modul.
        """
        for baris in cls._baris_teks(kata):
            teks = ' '.join(w['text'] for w in baris)
            if RE_JUDUL_KOLOM.match(teks):
                return min(w['top'] for w in baris)
        return None

    @staticmethod
    def _kolom_halaman(rects: list) -> list:
        """
        Batas kiri-kanan tiap kolom, diambil dari kotak sel yang dicetak.

        Kotak yang sama dicetak berulang di tiap baris, jadi yang paling
        sering muncul adalah kolom tabelnya. Halaman terakhir tiap blok
        menambahkan satu kotak bingkai selebar tabel; ia kalah sering, dan
        karena tumpang tindih dengan kolom yang sudah diambil, ia dilewati.

        Kalau jumlah kemunculannya sama — halaman yang hanya memuat satu
        baris — urutan ditentukan posisi kiri kotaknya, bukan urutan acak
        dari dict, supaya hasilnya tidak berubah-ubah antar-mesin.
        """
        hitung = {}
        for r in rects:
            kunci = (round(r['x0']), round(r['x1']))
            if kunci[1] - kunci[0] < 15:
                continue
            hitung[kunci] = hitung.get(kunci, 0) + 1

        terpilih = []
        for (x0, x1), _ in sorted(hitung.items(),
                                  key=lambda kv: (-kv[1], kv[0][0], kv[0][1])):
            if any(not (x1 <= a or x0 >= b) for a, b in terpilih):
                continue
            terpilih.append((x0, x1))
        terpilih.sort()
        return terpilih

    def _sel_halaman(self, page, kata: list, urut: int) -> list:
        """
        Kembalikan isi tiap baris tabel halaman ini sebagai dict per kolom.
        Daftar kosong kalau halaman ini tidak memuat tabel transaksi.

        `kata` diterima dari pemanggil, bukan diambil sendiri: satu halaman
        PDF 95 halaman tidak perlu di-extract_words() dua kali hanya untuk
        menjawab "ada header kolomnya atau tidak".

        Kata ditempatkan ke baris yang tumpang tindihnya PALING BESAR, bukan
        ke setiap baris yang memuat titik tengahnya: transaksi yang terpotong
        batas halaman teksnya terpangkas di tepi kertas, sehingga sebagian
        kata menonjol keluar dari kotak selnya.
        """
        atas = self._atas_tabel(kata)
        if atas is None:
            return []

        # Toleransi 8pt: tepi atas kotak header sedikit di atas tepi atas
        # huruf-hurufnya.
        rects = [r for r in page.rects
                 if r['bottom'] - r['top'] >= 5 and r['top'] >= atas - 8]
        kolom = self._kolom_halaman(rects)
        if len(kolom) != len(KOLOM):
            self._catat('Tinggi', 'Kolom tabel tidak terbaca utuh',
                        f'Halaman {urut}: ditemukan {len(kolom)} kolom, '
                        f'seharusnya {len(KOLOM)}. Transaksi di halaman ini '
                        f'tidak diekstrak.', halaman=str(urut))
            return []

        band = sorted({(round(r['top'], 1), round(r['bottom'], 1))
                       for r in rects})
        isi = [dict() for _ in band]
        for w in kata:
            terbaik, luas = None, 0.0
            for i, (a, b) in enumerate(band):
                tumpang = min(w['bottom'], b) - max(w['top'], a)
                if tumpang > luas:
                    terbaik, luas = i, tumpang
            if terbaik is None:
                continue
            tengah = (w['x0'] + w['x1']) / 2
            for nama, (x0, x1) in zip(KOLOM, kolom):
                if x0 <= tengah < x1:
                    isi[terbaik].setdefault(nama, []).append(w)
                    break

        hasil = []
        for sel in isi:
            teks = {}
            for nama, kata_sel in sel.items():
                # Urut baca: baris teks dulu (dibulatkan 3pt), lalu kiri ke kanan.
                kata_sel.sort(key=lambda w: (round(w['top'] / 3), w['x0']))
                teks[nama] = ' '.join(w['text'] for w in kata_sel)
            hasil.append(teks)
        return hasil

    # ------------------------------------------------------------------ #
    #  PEMBACAAN DOKUMEN                                                 #
    # ------------------------------------------------------------------ #

    def _parse_document(self) -> dict:
        """
        Baca seluruh PDF sekali, simpan hasilnya (dipanggil berkali-kali oleh
        extract_saldo/extract_transaksi/validate).
        """
        if self._cache is not None:
            return self._cache

        blok, halaman, identitas = [], [], []
        kini = None

        with pdfplumber.open(self.pdf_path) as pdf:
            for urut, page in enumerate(pdf.pages, 1):
                teks = page.extract_text() or ''

                periode = None
                m = RE_PERIODE.search(teks)
                if m:
                    periode = {
                        'mulai': _tanggal_periode(*m.group(1, 2, 3)),
                        'selesai': _tanggal_periode(*m.group(4, 5, 6)),
                        'label': ' '.join(m.group(0).split(':', 1)[1].split()),
                    }

                m = RE_REKENING.search(teks)
                if m:
                    identitas.append({
                        'halaman': urut,
                        'no_rekening': m.group(1),
                        'nama_rekening': ' '.join(m.group(2).split()),
                        'mata_uang': m.group(3).upper() or '-',
                    })

                # Blok inquiry baru: periodenya berbeda dari blok berjalan.
                # Halaman tanpa baris "Period" selalu ikut blok berjalan —
                # bukan membuka blok baru.
                baru = kini is None or (
                    periode is not None and (
                        kini['periode'] is None
                        or periode['label'] != kini['periode']['label']))
                if baru:
                    kini = {'periode': periode, 'baris': [], 'halaman_pdf': [],
                            'awal': None, 'total_debet': None,
                            'total_kredit': None}
                    blok.append(kini)
                kini['halaman_pdf'].append(urut)

                for pola, kunci in ((RE_AWAL, 'awal'),
                                    (RE_TOT_DEB, 'total_debet'),
                                    (RE_TOT_KRE, 'total_kredit')):
                    m = pola.search(teks)
                    if m and kini[kunci] is None:
                        kini[kunci] = _angka(m.group(1))

                kata = page.extract_words(x_tolerance=1.5, y_tolerance=2)
                sel_halaman = self._sel_halaman(page, kata, urut)

                jumlah_baris = 0
                baris_pertama = True
                for i, sel in enumerate(sel_halaman):
                    if i == 0:            # baris header kolom
                        continue
                    if not any(v.strip() for v in sel.values()):
                        continue

                    nomor = sel.get('no', '').strip()
                    if nomor.isdigit():
                        kini['baris'].append({
                            'no': int(nomor),
                            'halaman': urut,
                            'urut': jumlah_baris + 1,
                            'tanggal': sel.get('tanggal', ''),
                            'branch': sel.get('branch', ''),
                            'journal': sel.get('journal', ''),
                            'keterangan': sel.get('keterangan', ''),
                            'nominal': sel.get('nominal', ''),
                            'dbcr': sel.get('dbcr', ''),
                            'saldo': sel.get('saldo', ''),
                        })
                        jumlah_baris += 1
                        baris_pertama = False
                        continue

                    # Baris tanpa nomor = sambungan transaksi yang terpotong
                    # batas halaman. Kolom teks disambung dengan spasi; kolom
                    # angka disambung RAPAT, untuk jaga-jaga kalau yang
                    # terpotong angkanya sendiri ("18,038,374,548." + "00").
                    if not kini['baris']:
                        self._catat('Sedang',
                                    'Ada baris tabel sebelum transaksi pertama terbaca',
                                    f'Halaman {urut} memuat baris tanpa nomor urut '
                                    f'padahal belum ada transaksi yang bisa disambung.',
                                    halaman=str(urut))
                        continue
                    if not baris_pertama:
                        self._catat('Sedang',
                                    'Ada baris transaksi tanpa nomor urut',
                                    f'Halaman {urut}: baris tanpa nomor muncul di '
                                    f'tengah halaman, bukan di sambungan antar halaman.',
                                    halaman=str(urut))
                    sebelumnya = kini['baris'][-1]
                    for kolom in ('tanggal', 'branch', 'keterangan'):
                        if sel.get(kolom):
                            sebelumnya[kolom] = (
                                sebelumnya[kolom] + ' ' + sel[kolom]).strip()
                    for kolom in ('journal', 'nominal', 'dbcr', 'saldo'):
                        if sel.get(kolom):
                            sebelumnya[kolom] += sel[kolom]
                    baris_pertama = False

                halaman.append({
                    'urut': urut,
                    # Format ini tidak mencetak nomor halaman sama sekali.
                    'no_tercetak': None,
                    'total_tercetak': None,
                    'periode': kini['periode']['label'] if kini['periode'] else None,
                    'ada_header_kolom': bool(sel_halaman),
                    'jumlah_baris': jumlah_baris,
                })

        self._cache = {'blok': blok, 'halaman': halaman,
                       'identitas': identitas,
                       'meta': self._meta(identitas)}
        self._periksa_identitas(identitas)
        return self._cache

    @staticmethod
    def _meta(identitas: list) -> dict:
        """Identitas rekening: diambil dari halaman pertama yang memuatnya."""
        utama = identitas[0] if identitas else {}
        return {
            'no_rekening': utama.get('no_rekening', 'unknown'),
            'nama_pemilik': utama.get('nama_rekening', '-'),
            # Dokumen TRANSACTION INQUIRY tidak mencetak jenis rekening di
            # mana pun. Dikirim '-' supaya tidak ada yang ditebak: laporan
            # yang menyebut "GIRO" tanpa dasar lebih berbahaya daripada
            # laporan yang mengaku tidak tahu.
            'jenis_rekening': '-',
            'mata_uang': utama.get('mata_uang', '-'),
        }

    def _periksa_identitas(self, identitas: list) -> None:
        """
        Semua halaman harus menyebut rekening yang sama.

        Diperiksa karena PDF gabungan hasil edit bisa membawa halaman
        rekening lain, dan itu tidak akan tertangkap checksum nominal mana
        pun: angka tiap bloknya tetap cocok dengan ringkasannya sendiri.
        """
        if not identitas:
            self._catat('Tinggi', 'Nomor rekening tidak ditemukan di dokumen',
                        'Baris "Account :" tidak terbaca di halaman mana pun.')
            return

        nomor = {}
        for i in identitas:
            nomor.setdefault(i['no_rekening'], []).append(i['halaman'])
        if len(nomor) > 1:
            rincian = '; '.join(f'{no} (halaman {self._ringkas_nomor(hal)})'
                                for no, hal in sorted(nomor.items()))
            self._catat('Tinggi',
                        'Dokumen memuat lebih dari satu nomor rekening',
                        f'Nomor rekening yang ditemukan: {rincian}. '
                        f'Hanya rekening pertama yang diekstrak.')

        nama = {}
        for i in identitas:
            nama.setdefault(i['nama_rekening'].upper(), []).append(i['halaman'])
        if len(nama) > 1:
            rincian = '; '.join(f'"{n}" (halaman {self._ringkas_nomor(hal)})'
                                for n, hal in sorted(nama.items()))
            self._catat('Tinggi',
                        'Nama pemilik rekening berbeda antar halaman',
                        f'Nama yang ditemukan: {rincian}.')

    @staticmethod
    def _ringkas_nomor(nomor: list, maks: int = 6) -> str:
        """Ringkas daftar nomor jadi satu baris yang tidak melebar tak terkendali."""
        nomor = sorted(set(nomor))
        if len(nomor) <= maks:
            return ', '.join(str(n) for n in nomor)
        return ', '.join(str(n) for n in nomor[:maks]) + f', ... (+{len(nomor) - maks})'

    # ------------------------------------------------------------------ #
    #  TRANSAKSI                                                         #
    # ------------------------------------------------------------------ #

    def _transaksi(self) -> list:
        """
        Susun transaksi final: tanggal, arah, nominal, saldo, keterangan.

        Nominal diambil dari kolom Amount dan diberi tanda oleh kolom Db/Cr
        (lihat §2 di docstring modul). Saldo diambil apa adanya dari kolom
        Balance; kalau satu sel saldo tidak terbaca, rantainya diteruskan
        dari nominal supaya saldo harian tetap terisi — nilai tercetaknya
        tetap dikirim terpisah (None) lewat '_provenance' agar engine tahu
        angka itu perkiraan, bukan bacaan.
        """
        doc = self._parse_document()
        hasil = []
        for blok in doc['blok']:
            berjalan = blok['awal']
            for r in blok['baris']:
                nominal = self._nominal_tercetak(r['nominal'])
                arah = (r['dbcr'] or '').strip().upper()[:1]
                mutasi = None
                if nominal is not None and arah in ('D', 'C'):
                    mutasi = nominal if arah == 'C' else -nominal

                saldo_tercetak = _angka(r['saldo'])
                saldo = saldo_tercetak
                if saldo is None and berjalan is not None and mutasi is not None:
                    saldo = berjalan + mutasi

                hasil.append({
                    'blok': blok,
                    'periode': blok['periode']['label'] if blok['periode'] else None,
                    'no': r['no'],
                    'tanggal': self._tanggal_baris(r),
                    'halaman': r['halaman'],
                    'urut': r['urut'],
                    'arah': arah,
                    'mutasi': mutasi,
                    'nominal': nominal,
                    'saldo': saldo,
                    'saldo_tercetak': saldo_tercetak,
                    'tanggal_mentah': r['tanggal'],
                    'nominal_mentah': r['nominal'],
                    'keterangan': ' '.join(r['keterangan'].split()),
                    'nama': self._nama_lawan(' '.join(r['keterangan'].split()), arah),
                })
                if saldo is not None:
                    berjalan = saldo
        return hasil

    @staticmethod
    def _tanggal_baris(r: dict):
        """Tanggal posting dari kolom Post Date. None kalau tidak terbaca."""
        potong = (r['tanggal'] or '').split()
        m = RE_TANGGAL.match(potong[0]) if potong else None
        if not m:
            return None
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None

    @staticmethod
    def _nominal_tercetak(teks: str):
        """
        Nominal dari kolom Amount.

        Sel ini kadang kemasukan potongan teks yang meluber dari kolom
        Description ("-", "|"), jadi yang diambil potongan PERTAMA yang
        berbentuk nominal utuh — bukan potongan pertama begitu saja.
        """
        for potong in (teks or '').split():
            nilai = _angka(potong)
            if nilai is not None:
                return nilai
        return None

    # ------------------------------------------------------------------ #
    #  KONTRAK BaseExtractor                                             #
    # ------------------------------------------------------------------ #

    def extract_no_rekening(self) -> str:
        return self._parse_document()['meta']['no_rekening']

    def extract_saldo(self) -> dict:
        doc = self._parse_document()
        transaksi = self._transaksi()

        # Saldo akhir harian = saldo pada transaksi terakhir hari itu.
        per_hari = {}
        for t in transaksi:
            if t['tanggal'] is not None and t['saldo'] is not None:
                per_hari[t['tanggal']] = t['saldo']

        # Beginning Balance berlaku pada hari pertama periode bloknya.
        saldo_awal_pada = {}
        tercakup = set()
        for blok in doc['blok']:
            periode = blok['periode']
            if not periode or not periode['mulai'] or not periode['selesai']:
                continue
            if blok['awal'] is not None:
                saldo_awal_pada.setdefault(periode['mulai'], blok['awal'])
            hari = periode['mulai']
            while hari <= periode['selesai']:
                tercakup.add(hari)
                hari += timedelta(days=1)

        hasil = {'_nama_pemilik': doc['meta']['nama_pemilik'],
                 '_no_rekening': doc['meta']['no_rekening'],
                 '_jenis_rekening': doc['meta']['jenis_rekening']}
        if not tercakup:
            return hasil

        ember = {}
        awal_bulan = {}
        berjalan = None
        # Hari diurutkan kronologis, bukan menurut urutan cetak bloknya:
        # satu berkas bisa memuat blok yang tidak berurutan (lihat §3).
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

        hasil['_biaya_admin'] = self._jadwal_biaya_admin(doc)
        # Jasa giro & PPh-nya dicetak dengan keterangan persis ini, tanpa
        # segmen atau kode apa pun di belakangnya — jadi kolom Keterangan
        # yang dipakai, dicocokkan persis sama supaya transaksi lain tidak
        # ikut terhitung.
        hasil['_bunga_pajak'] = {'kolom': 'Keterangan Transaksi',
                                 'bunga': ['JASA GIRO/BUNGA'],
                                 'pajak': ['PPH']}

        laporan = self.validate()
        if laporan.get('peringatan'):
            hasil['_peringatan'] = laporan['peringatan']
        hasil['_provenance'] = self._provenance(transaksi)
        hasil['_checksum'] = [
            {'label': p['label'], 'bulan': p['bulan'],
             'expected': p['expected'], 'actual': p['actual'],
             'toleransi': TOLERANSI}
            for p in laporan['periods']
        ]
        return hasil

    def _jadwal_biaya_admin(self, doc: dict) -> dict:
        """
        Ketentuan BNI: biaya administrasi rekening didebet pada hari terakhir
        periode laporan, bersamaan dengan jasa giro & PPh-nya.

        Jadwalnya diambil dari tanggal akhir periode yang TERCETAK di
        dokumen, bukan dari kalender, supaya laporan periode sebagian bulan
        tidak dinilai dengan tanggal yang tidak pernah berlaku baginya.
        """
        jadwal = {}
        for blok in doc['blok']:
            periode = blok['periode']
            if not periode or not periode['selesai']:
                continue
            akhir = periode['selesai']
            hari_terakhir = calendar.monthrange(akhir.year, akhir.month)[1]
            jadwal[BULAN_ORDER[akhir.month - 1]] = {
                'tanggal': akhir.day,
                'aturan': ('hari terakhir periode laporan (ketentuan BNI)'
                           if akhir.day != hari_terakhir else
                           'akhir bulan (ketentuan BNI)'),
            }
        return {'label_nama': self.LABEL_BANK['BIAYA ADM REK'], 'jadwal': jadwal}

    def _provenance(self, transaksi: list) -> dict:
        """
        Jejak cetak dokumen (lihat kontrak '_provenance' di extractors/base.py).

        Inilah yang membuat rantai saldo diperiksa engine: 'mutasi' berasal
        dari kolom Amount, 'saldo_tercetak' dari kolom Balance, dan keduanya
        dikirim apa adanya. Baris yang nominal dan saldonya tidak sejalan
        karena itu muncul sebagai temuan "Running Balance Tidak Konsisten",
        bukan diperhalus di sini.

        'teks_mentah' sengaja tidak dikirim: satu-satunya teks panjang per
        baris di format ini adalah kolom Description, yang memuat berita
        bebas dari nasabah — angka bergaya Indonesia di sana lazim dan bukan
        artefak dokumen.
        """
        baris = []
        for t in transaksi:
            if t['tanggal'] is None:
                continue
            baris.append({
                'bulan': BULAN_ORDER[t['tanggal'].month - 1],
                'tanggal': t['tanggal'].day,
                'halaman': t['halaman'],
                'urut': t['urut'],
                'periode': t['periode'],
                'mutasi': t['mutasi'],
                'saldo_tercetak': t['saldo_tercetak'],
                'teks_mentah': None,
            })
        return {'halaman': self._parse_document()['halaman'], 'baris': baris}

    def extract_transaksi(self) -> dict:
        ember = {}
        for t in self._transaksi():
            if t['tanggal'] is None or t['nominal'] is None or t['arah'] not in ('D', 'C'):
                continue
            nama_bulan = BULAN_ORDER[t['tanggal'].month - 1]
            ember.setdefault(nama_bulan, []).append({
                'Bulan': nama_bulan,
                'Tanggal': t['tanggal'].day,
                'Jenis Mutasi': 'Kredit' if t['arah'] == 'C' else 'Debit',
                'Mutasi': t['nominal'],
                'Nama Pengirim/Penerima': t['nama'],
                'Keterangan Transaksi': t['keterangan'],
            })
        return {b: pd.DataFrame(v) for b, v in ember.items() if v}

    # ------------------------------------------------------------------ #
    #  VALIDASI OTOMATIS (CHECKSUM)                                      #
    # ------------------------------------------------------------------ #

    def validate(self) -> dict:
        """
        Cocokkan hasil parsing dengan angka resmi di kepala tiap blok inquiry:
        Total Debit, Total Credit, dan saldo akhir yang mengikuti dari
        Beginning Balance − Total Debit + Total Credit.

        Selain itu diperiksa hal-hal yang tidak butuh angka ringkasan:
          - tanggal, nominal, arah, dan saldo tiap baris harus terbaca;
          - penomoran baris tiap blok harus 1..n tanpa lubang;
          - tanggal tiap baris harus berada di dalam periode bloknya;
          - sambungan antar blok (saldo & tanggal) setelah diurutkan
            kronologis — lihat §3 di docstring modul.
        """
        doc = self._parse_document()
        laporan = {'ok': True, 'periods': [],
                   'warnings': list(self.warnings),
                   'peringatan': list(self.peringatan)}
        catat = pencatat_laporan(laporan)

        if not doc['blok'] or not any(b['baris'] for b in doc['blok']):
            laporan['ok'] = False
            catat('Tinggi', 'Tidak ada transaksi yang terbaca dari dokumen',
                  'Tabel "TRANSACTION INQUIRY" tidak ditemukan atau kosong.')
            return laporan

        transaksi = self._transaksi()
        per_blok = {}
        for t in transaksi:
            per_blok.setdefault(id(t['blok']), []).append(t)

        for blok in doc['blok']:
            baris = per_blok.get(id(blok), [])
            periode = blok['periode']
            label = periode['label'] if periode else 'Tanpa periode'
            bulan = (BULAN_ORDER[periode['mulai'].month - 1]
                     if periode and periode['mulai'] else '-')

            total_debet = sum(t['nominal'] for t in baris
                              if t['arah'] == 'D' and t['nominal'] is not None)
            total_kredit = sum(t['nominal'] for t in baris
                               if t['arah'] == 'C' and t['nominal'] is not None)
            saldo_akhir = next((t['saldo'] for t in reversed(baris)
                                if t['saldo'] is not None), blok['awal'])

            # Dokumen tidak mencetak Ending Balance; yang tercetak tiga angka
            # di kepala blok. Saldo akhir yang diharapkan mengikuti dari
            # ketiganya, jadi tetap angka dokumen — bukan angka kita sendiri.
            penutup = None
            if (blok['awal'] is not None and blok['total_debet'] is not None
                    and blok['total_kredit'] is not None):
                penutup = blok['awal'] - blok['total_debet'] + blok['total_kredit']

            hasil = {
                'label': label, 'bulan': bulan,
                'expected': {'total_debit': blok['total_debet'],
                             'total_credit': blok['total_kredit'],
                             'closing': penutup},
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
            laporan['periods'].append(hasil)

            if penutup is None:
                catat('Sedang', 'Ringkasan resmi laporan tidak lengkap',
                      f'Blok {label}: baris Beginning Balance/Total Debit/Total '
                      f'Credit tidak lengkap terbaca, jadi hasil ekstraksi blok ini '
                      f'tidak bisa dicocokkan dengan angka resmi PDF.', bulan=bulan)

            self._periksa_baris(blok, baris, label, bulan, catat, laporan)

        # Sambungan antar blok diperiksa kronologis, bukan menurut urutan
        # cetak: blok hasil query terpisah wajar tersusun tidak berurutan.
        berperiode = [b for b in doc['blok']
                      if b['periode'] and b['periode']['mulai'] and b['periode']['selesai']]
        berperiode.sort(key=lambda b: b['periode']['mulai'])
        for sebelumnya, kini in zip(berperiode, berperiode[1:]):
            self._periksa_sambungan(sebelumnya, kini, catat)

        return laporan

    def _periksa_baris(self, blok, baris, label, bulan, catat, laporan) -> None:
        """Pemeriksaan per baris yang tidak butuh angka ringkasan resmi."""
        tanpa_tanggal = [t for t in baris if t['tanggal'] is None]
        if tanpa_tanggal:
            laporan['ok'] = False
            contoh = tanpa_tanggal[0]
            catat('Tinggi', 'Ada baris yang tanggalnya tidak terbaca',
                  f'{len(tanpa_tanggal)} baris pada blok {label}, mis. baris '
                  f'nomor {contoh["no"]} (halaman {contoh["halaman"]}) yang '
                  f'kolom Post Date-nya tercetak "{contoh["tanggal_mentah"]}".',
                  bulan=bulan, halaman=str(contoh['halaman']))

        tanpa_nominal = [t for t in baris
                         if t['nominal'] is None or t['arah'] not in ('D', 'C')]
        if tanpa_nominal:
            laporan['ok'] = False
            contoh = tanpa_nominal[0]
            catat('Tinggi', 'Ada baris yang nominal atau arah mutasinya tidak terbaca',
                  f'{len(tanpa_nominal)} baris pada blok {label}, mis. baris '
                  f'nomor {contoh["no"]} (halaman {contoh["halaman"]}) yang '
                  f'kolom Amount-nya tercetak "{contoh["nominal_mentah"]}".',
                  bulan=bulan, halaman=str(contoh['halaman']))

        putus = [t for t in baris if t['saldo_tercetak'] is None]
        if putus:
            laporan['ok'] = False
            catat('Tinggi', 'Ada baris yang saldonya tidak terbaca',
                  f'{len(putus)} baris pada blok {label} (halaman '
                  f'{self._ringkas_nomor([t["halaman"] for t in putus])}).',
                  bulan=bulan)

        # Penomoran baris: jaminan kelengkapan yang khas format ini (§4).
        nomor = [t['no'] for t in baris]
        if nomor and nomor != list(range(1, len(nomor) + 1)):
            laporan['ok'] = False
            hilang = sorted(set(range(1, max(nomor) + 1)) - set(nomor))
            catat('Tinggi', 'Penomoran baris transaksi tidak utuh',
                  f'Blok {label} memuat {len(nomor)} baris bernomor '
                  f'1..{max(nomor)}'
                  + (f'; nomor yang tidak ada: '
                     f'{self._ringkas_nomor(hilang)}.' if hilang else
                     '; ada nomor yang terulang atau tidak berurutan.')
                  + ' Baris yang dihapus dari dokumen meninggalkan lubang '
                    'seperti ini.', bulan=bulan)

        periode = blok['periode']
        if periode and periode['mulai'] and periode['selesai']:
            luar = [t for t in baris if t['tanggal'] is not None
                    and not (periode['mulai'] <= t['tanggal'] <= periode['selesai'])]
            if luar:
                laporan['ok'] = False
                catat('Tinggi', 'Ada transaksi di luar periode yang dinyatakan laporan',
                      f'{len(luar)} baris pada blok {label}, mis. baris nomor '
                      f'{luar[0]["no"]} bertanggal {luar[0]["tanggal"]:%d-%m-%Y} '
                      f'(halaman {luar[0]["halaman"]}).',
                      bulan=bulan, halaman=str(luar[0]['halaman']))

    def _periksa_sambungan(self, sebelumnya: dict, kini: dict, catat) -> None:
        """Sambungan antar blok inquiry di satu berkas: saldo dan tanggalnya."""
        label = kini['periode']['label'] if kini['periode'] else 'Tanpa periode'

        penutup = None
        if (sebelumnya['awal'] is not None
                and sebelumnya['total_debet'] is not None
                and sebelumnya['total_kredit'] is not None):
            penutup = (sebelumnya['awal'] - sebelumnya['total_debet']
                       + sebelumnya['total_kredit'])
        if (penutup is not None and kini['awal'] is not None
                and abs(penutup - kini['awal']) > TOLERANSI):
            catat('Tinggi', 'Saldo antar periode laporan tidak bersambung',
                  f'Saldo akhir periode sebelumnya {penutup:,.2f} tidak sama '
                  f'dengan Beginning Balance periode {label} '
                  f'{kini["awal"]:,.2f} — ada periode yang tidak disertakan '
                  f'atau angkanya tidak konsisten.')

        p_lama, p_baru = sebelumnya['periode'], kini['periode']
        jarak = (p_baru['mulai'] - p_lama['selesai']).days
        if jarak > 1:
            catat('Tinggi', 'Ada rentang tanggal yang tidak dicakup laporan',
                  f'Periode sebelumnya berakhir {p_lama["selesai"]:%d-%m-%Y}, '
                  f'periode berikutnya mulai {p_baru["mulai"]:%d-%m-%Y} — '
                  f'{jarak - 1} hari tidak dicakup laporan mana pun.')
        elif jarak < 1:
            catat('Sedang', 'Periode laporan tumpang tindih',
                  f'Periode {p_lama["label"]} dan {p_baru["label"]} beririsan '
                  f'tanggal — transaksi yang sama bisa terhitung dua kali.')
