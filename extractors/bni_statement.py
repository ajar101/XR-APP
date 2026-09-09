"""
bni_statement.py — Extractor rekening BNI format "ACCOUNT STATEMENT".

Format ini dikenali dari judul "ACCOUNT STATEMENT" di kepala tiap halaman
plus tabel berkolom:

    Posting Date | Effective Date | Branch | Journal |
    Transaction Description | Amount | DB/CR | Balance

Tiga sifat dokumen ini yang menentukan cara membacanya:

1. TABELNYA BERGARIS. Setiap sel dicetak sebagai kotak (rect) tersendiri,
   jadi batas baris DAN batas kolom bisa diambil dari geometri dokumennya,
   bukan ditebak dari koordinat teks. Ini penting karena satu transaksi
   memakan 1–7 baris teks (kolom Branch dan Transaction Description
   membungkus), dan isi tiap sel dicetak rata-tengah secara vertikal —
   sehingga nominal & saldo sebuah transaksi kerap berada pada baris teks
   yang BERBEDA dari baris tanggalnya. Mengelompokkan per baris teks
   (pendekatan extractor BNI sebelumnya) memecah satu transaksi jadi
   beberapa, atau menempelkan nominal ke transaksi tetangganya.

2. KOLOM AMOUNT DICETAK GANDA. Efek tebal pada kolom Amount dibuat dengan
   menggambar tiap glyph dua kali di koordinat yang sama, sehingga
   extract_text() menghasilkan "22,,550000..0000" untuk 2.500,00.
   page.dedupe_chars() membereskan sebagian besar, tapi TIDAK semuanya:
   sebagian angka tetap keluar cacat ("9,999,9909.00" untuk 9.999.999,00).
   Karena itu nominal TIDAK diambil dari kolom Amount, melainkan dihitung
   dari selisih kolom Balance — yang dicetak biasa dan utuh. Hasilnya
   dicocokkan dengan Total Debet/Total Credit resmi di kaki laporan
   (lihat validate()), jadi bukan asumsi yang dibiarkan tanpa bukti.

3. SATU PDF BISA MEMUAT BEBERAPA LAPORAN. Berkas gabungan memuat beberapa
   blok "Period : ... - ...", masing-masing dengan Ledger Balance sendiri,
   nomor halaman yang mulai lagi dari 1, dan kaki ringkasannya sendiri.
   Tiap blok diperiksa terpisah; sambungan antar blok (saldo akhir blok
   sebelumnya vs Ledger Balance blok berikutnya) ikut diperiksa supaya
   periode yang hilang di tengah tidak lolos diam-diam.

Extractor ini hanya menghasilkan data mentah sesuai kontrak BaseExtractor —
tidak tahu apa pun soal Excel/styling.
"""

import calendar
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

# Singkatan bulan Inggris yang dipakai di baris "Period : 01-May-26 - 31-May-26".
BULAN_EN = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}

# Urutan kolom tabel, kiri ke kanan. Dipakai untuk memberi nama pada kotak
# sel yang ditemukan di halaman (lihat _kolom_halaman).
KOLOM = ('posting', 'effective', 'branch', 'journal',
         'keterangan', 'nominal', 'dbcr', 'saldo')

# Geometri kolom hasil pengukuran PDF referensi (halaman lebar 708pt).
# HANYA dipakai kalau kotak sel tidak ditemukan di halaman — mis. halaman
# yang hanya memuat kaki ringkasan.
KOLOM_BAKU = [(14, 124), (124, 234), (234, 292), (292, 337),
              (337, 496), (496, 574), (574, 609), (609, 694)]

RE_TANGGAL = re.compile(r'^(\d{2})/(\d{2})/(\d{4})$')
RE_NOMINAL = re.compile(r'^[\d,]+\.\d{2}$')

RE_REKENING = re.compile(
    r'Account\s*No\.?\s*:\s*(\S+)\s*/\s*(.+?)\s*\(([A-Za-z]{3})\)')
RE_JENIS    = re.compile(r'Account\s*Type\s*:\s*(\S+)')
RE_PERIODE  = re.compile(
    r'Period\s*:\s*(\d{1,2})-([A-Za-z]{3})-(\d{2})\s*-\s*(\d{1,2})-([A-Za-z]{3})-(\d{2})')
RE_HALAMAN  = re.compile(r'Page\s*:\s*(\d+)')
RE_LEDGER   = re.compile(r'Ledger\s*Balance\s*:?\s*([\d,]+\.\d{2})')
RE_ENDING   = re.compile(r'Ending\s*Balance\s*:?\s*([\d,]+\.\d{2})')
RE_TOT_DEB  = re.compile(r'Total\s*Debet\s*:?\s*(\d+)\s+([\d,]+\.\d{2})')
RE_TOT_KRE  = re.compile(r'Total\s*Credit\s*:?\s*(\d+)\s+([\d,]+\.\d{2})')

# Baris kaki/kepala laporan yang berada DI DALAM tabel, jadi tidak bisa
# dibedakan dari transaksi lewat posisi — harus dikenali dari teksnya.
PENANDA_BUKAN_TRANSAKSI = ('Ledger Balance', 'Ending Balance',
                           'Total Debet', 'Total Credit')

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
    """'01', 'May', '26' -> date(2026, 5, 1). None kalau tidak terbaca."""
    nomor = BULAN_EN.get(bulan.upper())
    if not nomor:
        return None
    try:
        return date(2000 + int(tahun), nomor, int(hari))
    except ValueError:
        return None


class BNIStatementExtractor(PencatatPeringatan, BaseExtractor):

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
    def _kolom_halaman(page) -> list:
        """
        Batas kiri-kanan tiap kolom, diambil dari kotak sel yang dicetak.

        Kotak yang sama dicetak berulang di tiap baris, jadi yang paling
        sering muncul adalah kolom tabelnya. Kotak baris header sedikit
        lebih sempit (mis. 29–123 vs 14–124) sehingga akan tumpang tindih
        dengan kolom yang sudah diambil — kotak tumpang tindih dilewati,
        bukan ditambahkan, supaya jumlah kolomnya tetap delapan.
        """
        hitung = {}
        for r in page.rects:
            if r['bottom'] - r['top'] < 6:
                continue
            kunci = (round(r['x0']), round(r['x1']))
            if kunci[1] - kunci[0] < 15:
                continue
            hitung[kunci] = hitung.get(kunci, 0) + 1

        terpilih = []
        for (x0, x1), _ in sorted(hitung.items(), key=lambda kv: -kv[1]):
            if any(not (x1 <= a or x0 >= b) for a, b in terpilih):
                continue
            terpilih.append((x0, x1))
        terpilih.sort()
        return terpilih if len(terpilih) == len(KOLOM) else list(KOLOM_BAKU)

    @staticmethod
    def _band_halaman(page) -> list:
        """Batas atas-bawah tiap baris tabel, dari kotak sel yang dicetak."""
        band = {(round(r['top'], 1), round(r['bottom'], 1)) for r in page.rects}
        return sorted(b for b in band if b[1] - b[0] >= 5)

    def _sel_halaman(self, page) -> list:
        """
        Kembalikan isi tiap baris tabel halaman ini sebagai dict per kolom.

        Kata ditempatkan ke baris yang tumpang tindihnya PALING BESAR, bukan
        ke setiap baris yang memuat titik tengahnya. Bedanya terasa pada
        transaksi yang terpotong batas halaman: teksnya terpangkas di tepi
        kertas sehingga sebagian kata menonjol keluar dari kotak selnya.
        """
        kolom = self._kolom_halaman(page)
        band = self._band_halaman(page)
        if not band:
            return []

        kata = page.dedupe_chars(tolerance=1).extract_words(
            x_tolerance=1.5, y_tolerance=2)

        isi = [dict() for _ in band]
        for w in kata:
            terbaik, luas = None, 0.0
            for i, (atas, bawah) in enumerate(band):
                tumpang = min(w['bottom'], bawah) - max(w['top'], atas)
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
        for (atas, bawah), sel in zip(band, isi):
            teks = {}
            for nama, kata_sel in sel.items():
                # Urut baca: baris teks dulu (dibulatkan 3pt), lalu kiri ke kanan.
                kata_sel.sort(key=lambda w: (round(w['top'] / 3), w['x0']))
                teks[nama] = ' '.join(w['text'] for w in kata_sel)
            hasil.append({'atas': atas, 'bawah': bawah, 'sel': teks})
        return hasil

    # ------------------------------------------------------------------ #
    #  PEMBACAAN DOKUMEN                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _nama_blok_alamat(page) -> str:
        """
        Nama pada blok alamat di kiri atas halaman.

        Dibaca terpisah dari nama pada baris "Account No. : <rek> / <nama>"
        supaya keduanya bisa dibandingkan: keduanya seharusnya merujuk
        pemilik rekening yang sama.
        """
        kata = [w for w in page.extract_words(x_tolerance=1.5, y_tolerance=2)
                if w['x1'] < 225 and w['top'] < 290]
        if not kata:
            return ''
        baris = {}
        for w in kata:
            baris.setdefault(round(w['top'] / 4), []).append(w)
        kunci = min(baris)
        return ' '.join(w['text'] for w in sorted(baris[kunci],
                                                  key=lambda w: w['x0']))

    def _parse_document(self) -> dict:
        """
        Baca seluruh PDF sekali, simpan hasilnya (dipanggil berkali-kali oleh
        extract_saldo/extract_transaksi/validate).
        """
        if self._cache is not None:
            return self._cache

        blok = []
        halaman = []
        kini = None
        identitas = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for urut, page in enumerate(pdf.pages, 1):
                teks = page.extract_text() or ''

                m = RE_PERIODE.search(teks)
                periode = None
                if m:
                    mulai = _tanggal_periode(m.group(1), m.group(2), m.group(3))
                    selesai = _tanggal_periode(m.group(4), m.group(5), m.group(6))
                    periode = {'mulai': mulai, 'selesai': selesai,
                               'label': m.group(0).split(':', 1)[1].strip()}

                m = RE_HALAMAN.search(teks)
                no_tercetak = int(m.group(1)) if m else None

                m = RE_REKENING.search(teks)
                if m:
                    identitas.append({
                        'halaman': urut,
                        'no_rekening': m.group(1),
                        'nama_rekening': ' '.join(m.group(2).split()),
                        'mata_uang': m.group(3).upper(),
                        'nama_alamat': self._nama_blok_alamat(page),
                    })
                m = RE_JENIS.search(teks)
                jenis = m.group(1).upper() if m else None

                # Blok laporan baru: periodenya berbeda, atau halaman
                # tercetaknya mulai lagi dari 1. Halaman tanpa periode
                # (mis. halaman yang hanya memuat kaki ringkasan) selalu
                # ikut blok berjalan — bukan membuka blok baru.
                baru = kini is None or (
                    periode is not None and (
                        kini['periode'] is None
                        or periode['label'] != kini['periode']['label']
                        or no_tercetak == 1))
                if baru:
                    kini = {'periode': periode, 'jenis': jenis, 'baris': [],
                            'ledger': None, 'ending': None,
                            'n_debet': None, 'total_debet': None,
                            'n_kredit': None, 'total_kredit': None,
                            'halaman_pdf': []}
                    blok.append(kini)
                if kini['jenis'] is None:
                    kini['jenis'] = jenis
                kini['halaman_pdf'].append(urut)

                m = RE_LEDGER.search(teks)
                if m and kini['ledger'] is None:
                    kini['ledger'] = _angka(m.group(1))
                m = RE_ENDING.search(teks)
                if m:
                    kini['ending'] = _angka(m.group(1))
                m = RE_TOT_DEB.search(teks)
                if m:
                    kini['n_debet'], kini['total_debet'] = int(m.group(1)), _angka(m.group(2))
                m = RE_TOT_KRE.search(teks)
                if m:
                    kini['n_kredit'], kini['total_kredit'] = int(m.group(1)), _angka(m.group(2))

                ada_header = False
                jumlah_baris = 0
                baris_pertama = True
                for band in self._sel_halaman(page):
                    sel = band['sel']
                    gabung = ' '.join(sel.values())
                    if 'Posting' in sel.get('posting', '') and 'Date' in sel.get('posting', ''):
                        ada_header = True
                        continue
                    if any(p in gabung for p in PENANDA_BUKAN_TRANSAKSI):
                        continue
                    if not gabung.strip():
                        continue

                    kepala = sel.get('posting', '').split()
                    if kepala and RE_TANGGAL.match(kepala[0]):
                        kini['baris'].append({
                            'halaman': urut,
                            'urut': jumlah_baris + 1,
                            'posting': sel.get('posting', ''),
                            'effective': sel.get('effective', ''),
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

                    # Baris tanpa tanggal = sambungan transaksi yang terpotong
                    # batas halaman. Kolom teks disambung dengan spasi, kolom
                    # angka disambung RAPAT karena yang terpotong adalah
                    # angkanya sendiri ("18,038,374,548." + "00").
                    if not kini['baris']:
                        self._catat('Sedang',
                                    'Ada baris tabel sebelum transaksi pertama terbaca',
                                    f'Halaman {urut} memuat baris tanpa tanggal posting '
                                    f'padahal belum ada transaksi yang bisa disambung.',
                                    halaman=str(urut))
                        continue
                    if not baris_pertama:
                        self._catat('Sedang',
                                    'Ada baris transaksi tanpa tanggal posting',
                                    f'Halaman {urut}: baris tanpa tanggal muncul di '
                                    f'tengah halaman, bukan di sambungan antar halaman.',
                                    halaman=str(urut))
                    sebelumnya = kini['baris'][-1]
                    for kolom in ('branch', 'keterangan'):
                        if sel.get(kolom):
                            sebelumnya[kolom] = (sebelumnya[kolom] + ' ' + sel[kolom]).strip()
                    for kolom in ('journal', 'nominal', 'dbcr', 'saldo'):
                        if sel.get(kolom):
                            sebelumnya[kolom] += sel[kolom]
                    baris_pertama = False

                halaman.append({
                    'urut': urut,
                    'no_tercetak': no_tercetak,
                    # Format ini tidak mencetak "halaman x dari y".
                    'total_tercetak': None,
                    'periode': kini['periode']['label'] if kini['periode'] else None,
                    'ada_header_kolom': ada_header,
                    'jumlah_baris': jumlah_baris,
                })

        self._cache = {'blok': blok, 'halaman': halaman,
                       'identitas': identitas,
                       'meta': self._meta(identitas, blok)}
        self._periksa_identitas(identitas)
        return self._cache

    def _meta(self, identitas: list, blok: list) -> dict:
        """Identitas rekening: diambil dari halaman pertama yang memuatnya."""
        utama = identitas[0] if identitas else {}
        jenis = next((b['jenis'] for b in blok if b.get('jenis')), None)
        return {
            'no_rekening': utama.get('no_rekening', 'unknown'),
            'nama_pemilik': utama.get('nama_rekening', '-'),
            # "CURRENT" pada dokumen BNI berarti rekening giro; dipetakan ke
            # istilah yang dipakai extractor lain supaya laporannya seragam.
            'jenis_rekening': {'CURRENT': 'GIRO',
                               'SAVING': 'TABUNGAN',
                               'SAVINGS': 'TABUNGAN'}.get(jenis, jenis or '-'),
            'mata_uang': utama.get('mata_uang', '-'),
        }

    def _periksa_identitas(self, identitas: list) -> None:
        """
        Semua halaman harus menyebut rekening yang sama, dan nama pada blok
        alamat harus merujuk pemilik yang sama dengan nama rekeningnya.

        Keduanya diperiksa karena PDF gabungan hasil edit bisa membawa
        halaman rekening lain, dan itu tidak akan tertangkap oleh checksum
        nominal mana pun: angka tiap bloknya tetap cocok dengan ringkasannya
        sendiri.
        """
        if not identitas:
            self._catat('Tinggi', 'Nomor rekening tidak ditemukan di dokumen',
                        'Baris "Account No." tidak terbaca di halaman mana pun.')
            return

        nomor = {}
        for i in identitas:
            nomor.setdefault(i['no_rekening'], []).append(i['halaman'])
        if len(nomor) > 1:
            rincian = '; '.join(
                f'{no} (halaman {self._ringkas_halaman(hal)})'
                for no, hal in sorted(nomor.items()))
            self._catat('Tinggi',
                        'Dokumen memuat lebih dari satu nomor rekening',
                        f'Nomor rekening yang ditemukan: {rincian}. '
                        f'Hanya rekening pertama yang diekstrak.')

        utama = identitas[0]
        alamat = self._kata_nama(utama.get('nama_alamat', ''))
        rekening = self._kata_nama(utama.get('nama_rekening', ''))
        if alamat and rekening and not (alamat & rekening):
            self._catat('Sedang',
                        'Nama pada blok alamat berbeda dengan nama rekening',
                        f'Blok alamat: "{utama.get("nama_alamat")}" vs nama '
                        f'rekening: "{utama.get("nama_rekening")}". Tidak ada satu '
                        f'kata pun yang sama — perlu dipastikan dokumen ini memang '
                        f'milik pihak yang diperiksa.',
                        halaman=str(utama.get('halaman', '-')))

    @staticmethod
    def _kata_nama(teks: str) -> set:
        """Kata pembeda sebuah nama badan usaha/perorangan (tanpa bentuk badan)."""
        umum = {'PT', 'CV', 'TBK', 'PERSERO', 'UD', 'PD', 'KOPERASI'}
        kata = re.findall(r'[A-Za-z]{2,}', (teks or '').upper())
        return {k for k in kata if k not in umum}

    @staticmethod
    def _ringkas_halaman(nomor: list, maks: int = 6) -> str:
        nomor = sorted(set(nomor))
        if len(nomor) <= maks:
            return ', '.join(str(n) for n in nomor)
        return ', '.join(str(n) for n in nomor[:maks]) + f', ... (+{len(nomor) - maks})'

    # ------------------------------------------------------------------ #
    #  NOMINAL: DARI SELISIH SALDO                                       #
    # ------------------------------------------------------------------ #

    def _transaksi(self) -> list:
        """
        Susun transaksi final: tanggal, arah, nominal, saldo, keterangan.

        Nominal = selisih saldo tercetak antar baris (lihat alasannya di
        docstring modul). Saldo baris terakhir tiap blok memang dikosongkan
        bank — nilainya diambil dari "Ending Balance" di kaki laporan.
        """
        doc = self._parse_document()
        hasil = []
        for blok in doc['blok']:
            baris = blok['baris']
            sebelumnya = blok['ledger']
            for i, r in enumerate(baris):
                saldo = _angka(r['saldo'])
                if saldo is None and i == len(baris) - 1:
                    saldo = blok['ending']

                nominal_cetak = self._nominal_tercetak(r['nominal'])
                arah = r['dbcr'].strip().upper()[:1]
                if saldo is None and sebelumnya is not None and nominal_cetak is not None:
                    # Saldo tak terbaca tapi nominal & arahnya jelas: rantainya
                    # masih bisa diteruskan, dan selisihnya akan ketahuan di
                    # checksum kalau tebakan ini keliru.
                    saldo = sebelumnya + (nominal_cetak if arah == 'K' else -nominal_cetak)

                if saldo is not None and sebelumnya is not None:
                    mutasi = saldo - sebelumnya
                elif nominal_cetak is not None:
                    mutasi = nominal_cetak if arah == 'K' else -nominal_cetak
                else:
                    mutasi = None

                tanggal = None
                m = RE_TANGGAL.match(r['posting'].split()[0]) if r['posting'].split() else None
                if m:
                    try:
                        tanggal = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                    except ValueError:
                        tanggal = None

                keterangan = ' '.join(r['keterangan'].split())
                hasil.append({
                    'blok': blok,
                    'periode': blok['periode']['label'] if blok['periode'] else None,
                    'tanggal': tanggal,
                    'halaman': r['halaman'],
                    'urut': r['urut'],
                    'arah': arah,
                    'mutasi': mutasi,
                    'nominal': abs(mutasi) if mutasi is not None else None,
                    'nominal_cetak': nominal_cetak,
                    'saldo': saldo,
                    'saldo_tercetak': _angka(r['saldo']),
                    'keterangan': keterangan,
                    'nama': self._nama_lawan(keterangan, arah),
                })
                if saldo is not None:
                    sebelumnya = saldo
        return hasil

    @staticmethod
    def _nominal_tercetak(teks: str):
        """
        Nominal dari kolom Amount — dipakai sebagai cadangan & pembanding saja.

        Sel ini kerap memuat lebih dari satu potongan angka (sisa cetakan
        ganda, atau tanda '|' yang meluber dari kolom keterangan), jadi yang
        diambil potongan pertama yang berbentuk nominal utuh.
        """
        for potong in (teks or '').split():
            nilai = _angka(potong)
            if nilai is not None:
                return nilai
        return None

    # ------------------------------------------------------------------ #
    #  NAMA LAWAN TRANSAKSI                                              #
    # ------------------------------------------------------------------ #

    # Baris yang dibukukan bank sendiri: tidak ada lawan transaksi, tapi
    # perlu label tetap supaya terkelompok rapi di Rekap Debit/Kredit.
    # Dicocokkan pada kepala keterangan (segmen pertama), persis sama.
    LABEL_BANK = {
        'BIAYA ADM REK':     'Biaya Admin',
        'BY TRX ATM PRIMA':  'Biaya Transaksi ATM Prima',
        'BY TRX BIFAST':     'Biaya Transaksi BI-FAST',
        'JASA GIRO/BUNGA':   'Jasa Giro/Bunga',
        'PPH':               'PPh Jasa Giro',
    }
    # Penarikan warkat: nomor di belakangnya adalah nomor warkat, bukan nama.
    LABEL_WARKAT = (
        ('TARIK CHQ/BG',  'Tarik Cek/Bilyet Giro'),
        ('TARIK CHQ',     'Tarik Cek'),
        ('TARIK KLIRING', 'Tarik Kliring'),
        ('TARIK TUNAI',   'Tarik Tunai'),
        ('SETOR TUNAI',   'Setor Tunai'),
    )

    # "PEMINDAHAN KE 1815273089 CV RANGGA JAYA TRANS"
    RE_PEMINDAHAN = re.compile(r'PEMINDAHAN\s+(?:KE|DARI)\s+(\d+)\s*(.*)$')
    # "535 NUSANTARA EKSPR 23 ACR Invoice ..." — kode cabang 3 digit di depan.
    RE_KODE_CABANG = re.compile(r'^(\d{3})\s+(.*)$')
    # "HA BONGSUNG -PT BANK WOORI SAU" — nama, lalu bank asal setelah " -".
    RE_NAMA_BANK = re.compile(r'^(.*?)\s+-\s*(?:PT\s+)?BANK\b')
    # Label kanal, bukan nama pihak.
    LABEL_KANAL = {'BNI DIRECT', 'BNI DIRECT.', 'BNIDIRECT'}
    # Sapaan yang lazim mendahului nama orang dan ikut dicetak bank.
    SAPAAN = {'BPK', 'BP', 'IBU', 'IBU.', 'BPK.', 'SDR', 'SDRI', 'TN', 'NY', 'HJ'}
    # Lebar kolom nama pada baris kredit antarbank: 15 karakter, sisanya
    # berita transaksi yang menempel tanpa pemisah (lihat _potong_lebar).
    LEBAR_NAMA_KREDIT = 15

    def _nama_lawan(self, keterangan: str, arah: str) -> str:
        """
        Nama pihak lawan transaksi dari kolom Transaction Description.

        Kolom ini tersusun atas beberapa segmen dipisah '|'. Yang bisa
        dipastikan hanya bentuk-bentuk di bawah; selebihnya dikembalikan
        nomor rekening lawan yang tercetak — itu identitas yang memang
        disediakan dokumen, bukan tebakan, dan tetap mengelompokkan
        transaksi ke pihak yang sama di Rekap.
        """
        segmen = [s.strip() for s in (keterangan or '').split('|') if s.strip()]
        if not segmen:
            return ''

        kepala = segmen[0]
        # "KOR ..." = koreksi atas transaksi sejenis; polanya sama.
        if kepala.upper().startswith('KOR '):
            kepala = kepala[4:].strip()

        if kepala.upper() in self.LABEL_BANK:
            return self.LABEL_BANK[kepala.upper()]
        for awalan, label in self.LABEL_WARKAT:
            if kepala.upper().startswith(awalan):
                # "SETOR TUNAI | DAENG AJAM NURJAMIL | <berita>"
                if len(segmen) > 1 and not segmen[1][:1].isdigit():
                    return self._rapikan(segmen[1])
                return label

        # "TRANSFER KE | PEMINDAHAN KE 327655583 DAPENSI DWIKARYA | ..."
        for i, seg in enumerate(segmen):
            if i == 0:
                continue
            m = self.RE_PEMINDAHAN.match(seg)
            if not m:
                continue
            rekening, ekor = m.group(1), m.group(2).strip()
            nama = self._rapikan(self._potong_berita(ekor))
            if nama:
                return nama
            # Nama tidak menempel di klausa pemindahan. Segmen terakhir masih
            # bisa memuatnya: pada transaksi masuk lewat e-channel segmen itu
            # berisi "<NAMA PENGIRIM> <berita>".
            #
            # Kecuali kalau segmen itu dibuka nomor rekening lawan yang tadi
            # juga: bentuk itu adalah nomor referensi diikuti berita
            # ("3819622222 Pelunasan KIR mobil tangki"), tidak pernah memuat
            # nama, dan menambangnya hanya menghasilkan potongan berita yang
            # menyamar jadi nama pihak.
            akhir = segmen[-1]
            if (i != len(segmen) - 1
                    and akhir.upper() not in self.LABEL_KANAL
                    and not re.match(r'^0*' + rekening + r'\b', akhir)):
                nama = self._rapikan(self._potong_berita(akhir))
                if nama:
                    return nama
            # Dokumen tidak mencetak nama pihak lawan untuk transaksi ini —
            # yang tersedia hanya nomor rekeningnya. Nomor itu yang dipakai:
            # bukan tebakan, dan tetap menyatukan transaksi ke pihak yang
            # sama saat direkap.
            return rekening

        # "KREDIT LAIN-LAIN | 535 NUSANTARA EKSPR 23 ACR Invoice ..."
        m = self.RE_KODE_CABANG.match(segmen[1]) if len(segmen) > 1 else None
        if m:
            return self._rapikan(self._potong_lebar(m.group(2), self.LEBAR_NAMA_KREDIT))

        # "TRANSFER DARI | HA BONGSUNG -PT BANK WOORI SAU | ..."
        if len(segmen) > 1:
            m = self.RE_NAMA_BANK.match(segmen[1])
            if m:
                return self._rapikan(m.group(1))
            if segmen[1].upper() not in self.LABEL_KANAL:
                return self._rapikan(segmen[1])
        return ''

    @staticmethod
    def _potong_lebar(teks: str, lebar: int) -> str:
        """
        Ambil kata-kata pertama yang masih muat dalam kolom selebar `lebar`.

        Dipakai untuk baris kredit antarbank, yang mencetak nama pengirim
        pada kolom tetap 15 karakter lalu menyambungnya langsung dengan
        berita transaksi tanpa pemisah apa pun ("NUSANTARA EKSPR 23 ACR
        Invoice ..."). Batas kolomnya yang jadi pemisah, bukan tanda baca.
        """
        semua = teks.split()
        hasil = []
        panjang = 0
        for kata in semua:
            tambah = len(kata) + (1 if hasil else 0)
            if panjang + tambah > lebar:
                break
            hasil.append(kata)
            panjang += tambah
        # Kata pertama yang sendirian sudah melebihi lebar kolom tetap
        # dipakai: memotongnya di tengah kata justru merusak namanya.
        if not hasil:
            return semua[0] if semua else ''
        return ' '.join(hasil)

    @classmethod
    def _potong_berita(cls, ekor: str) -> str:
        """
        Pisahkan nama dari berita transaksi yang menempel di belakangnya.

        Bank mencetak keduanya berurutan tanpa pemisah apa pun
        ("ALI SYAMSUDI STABIL an Akhmad Ridwan"), jadi yang bisa dipakai
        hanya bentuk hurufnya: nama pihak dicetak sistem dalam HURUF BESAR
        semua, sedangkan berita diketik nasabah sendiri sehingga hampir
        selalu mengandung huruf kecil. Kata pertama yang mengandung huruf
        kecil karena itu menandai awal berita.

        Kalau justru kata pertamanya sudah berhuruf kecil, berarti tidak ada
        nama sama sekali di situ — seluruhnya berita ("1730018931288
        pemindahbukuan ke mtf"), dan yang dikembalikan kosong.
        """
        kata = (ekor or '').split()
        hasil = []
        for i, k in enumerate(kata):
            bersapa = k.upper().strip('.,') in cls.SAPAAN
            if i and any(c.islower() for c in k) and not bersapa:
                break
            if not i and k.islower():
                return ''
            hasil.append(k)
        nama = ' '.join(hasil)
        return '' if nama.replace('.', '').isdigit() else nama

    @staticmethod
    def _rapikan(nama: str) -> str:
        """Buang nomor referensi & tanda baca yang menempel di ekor nama."""
        nama = ' '.join((nama or '').split())
        nama = re.sub(r'\s+TRF\s+TO:\S*.*$', '', nama, flags=re.IGNORECASE)
        nama = re.sub(r'\s+NO\s*:\S*.*$', '', nama, flags=re.IGNORECASE)
        nama = re.sub(r'(?:\s+\d{6,})+\s*$', '', nama)
        # "BILL PAYMENT (MPN G2 IDR )" -> "BILL PAYMENT (MPN G2 IDR)"
        nama = re.sub(r'\s+([)\]])', r'\1', nama)
        return nama.strip(' .,-/|')

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

        # Saldo awal berlaku pada hari pertama periode blok yang bersangkutan.
        saldo_awal_pada = {}
        tercakup = set()
        for blok in doc['blok']:
            periode = blok['periode']
            if not periode or not periode['mulai'] or not periode['selesai']:
                continue
            if blok['ledger'] is not None:
                saldo_awal_pada.setdefault(periode['mulai'], blok['ledger'])
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
        # Bunga giro & pajaknya dicetak dengan keterangan persis ini, tanpa
        # ekor kode apa pun — jadi kolom Keterangan yang dipakai, dicocokkan
        # persis sama supaya transaksi lain tidak ikut terhitung.
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
            nama_bulan = BULAN_ORDER[akhir.month - 1]
            hari_terakhir = calendar.monthrange(akhir.year, akhir.month)[1]
            aturan = ('hari terakhir periode laporan (ketentuan BNI)'
                      if akhir.day != hari_terakhir else
                      'akhir bulan (ketentuan BNI)')
            jadwal[nama_bulan] = {'tanggal': akhir.day, 'aturan': aturan}
        return {'label_nama': self.LABEL_BANK['BIAYA ADM REK'], 'jadwal': jadwal}

    def _provenance(self, transaksi: list) -> dict:
        """
        Jejak cetak dokumen (lihat kontrak '_provenance' di extractors/base.py).

        'teks_mentah' sengaja tidak dikirim: satu-satunya teks panjang per
        baris di format ini adalah kolom Transaction Description, yang memuat
        berita bebas dari nasabah — angka bergaya Indonesia di sana lazim dan
        bukan artefak dokumen.
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
            if t['tanggal'] is None or t['nominal'] is None:
                continue
            nama_bulan = BULAN_ORDER[t['tanggal'].month - 1]
            ember.setdefault(nama_bulan, []).append({
                'Bulan': nama_bulan,
                'Tanggal': t['tanggal'].day,
                'Jenis Mutasi': 'Kredit' if t['arah'] == 'K' else 'Debit',
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
        Cocokkan hasil parsing dengan angka resmi di kaki tiap blok laporan:
        Ending Balance, Total Debet (jumlah & nominal), Total Credit.

        Selain itu diperiksa hal-hal yang tidak butuh angka ringkasan:
          - arah D/K tiap baris harus sesuai tanda selisih saldonya;
          - saldo tiap baris harus terbaca (rantai saldo tidak boleh putus);
          - Ledger Balance blok berikutnya harus sama dengan saldo akhir blok
            sebelumnya, dan periodenya bersambung — kalau tidak, ada periode
            yang hilang di antara keduanya.
        """
        doc = self._parse_document()
        laporan = {'ok': True, 'periods': [],
                   'warnings': list(self.warnings),
                   'peringatan': list(self.peringatan)}
        catat = pencatat_laporan(laporan)

        if not doc['blok'] or not any(b['baris'] for b in doc['blok']):
            laporan['ok'] = False
            catat('Tinggi', 'Tidak ada transaksi yang terbaca dari dokumen',
                  'Tabel "ACCOUNT STATEMENT" tidak ditemukan atau kosong.')
            return laporan

        transaksi = self._transaksi()
        per_blok = {}
        for t in transaksi:
            per_blok.setdefault(id(t['blok']), []).append(t)

        blok_sebelumnya = None
        for blok in doc['blok']:
            baris = per_blok.get(id(blok), [])
            periode = blok['periode']
            label = periode['label'] if periode else 'Tanpa periode'
            bulan = (BULAN_ORDER[periode['mulai'].month - 1]
                     if periode and periode['mulai'] else '-')

            n_debet = sum(1 for t in baris if t['arah'] == 'D')
            n_kredit = sum(1 for t in baris if t['arah'] == 'K')
            total_debet = sum(t['nominal'] or 0 for t in baris if t['arah'] == 'D')
            total_kredit = sum(t['nominal'] or 0 for t in baris if t['arah'] == 'K')
            saldo_akhir = next((t['saldo'] for t in reversed(baris)
                                if t['saldo'] is not None), blok['ledger'])

            hasil = {
                'label': label, 'bulan': bulan,
                'expected': {'n_debit': blok['n_debet'],
                             'n_credit': blok['n_kredit'],
                             'total_debit': blok['total_debet'],
                             'total_credit': blok['total_kredit'],
                             'closing': blok['ending']},
                'actual': {'n_debit': n_debet, 'n_credit': n_kredit,
                           'total_debit': total_debet,
                           'total_credit': total_kredit,
                           'closing': saldo_akhir},
                'checks': {},
            }
            for kunci in ('n_debit', 'n_credit', 'total_debit', 'total_credit', 'closing'):
                harapan = hasil['expected'][kunci]
                if harapan is None:
                    continue
                nyata = hasil['actual'][kunci]
                cocok = nyata is not None and abs(float(nyata) - float(harapan)) <= TOLERANSI
                hasil['checks'][kunci] = cocok
                if not cocok:
                    laporan['ok'] = False
            laporan['periods'].append(hasil)

            if blok['ending'] is None or blok['total_debet'] is None:
                catat('Sedang', 'Ringkasan resmi laporan tidak lengkap',
                      f'Blok {label}: baris Ending Balance/Total Debet/Total Credit '
                      f'tidak ditemukan, jadi hasil ekstraksi blok ini tidak bisa '
                      f'dicocokkan dengan angka resmi PDF.',
                      bulan=bulan)

            # Arah D/K vs tanda selisih saldo.
            salah_arah = [t for t in baris
                          if t['mutasi'] not in (None, 0)
                          and ((t['mutasi'] > 0) != (t['arah'] == 'K'))]
            if salah_arah:
                laporan['ok'] = False
                catat('Tinggi', 'Arah Debet/Kredit tidak sesuai perubahan saldo',
                      f'{len(salah_arah)} baris pada blok {label}, mis. halaman '
                      f'{salah_arah[0]["halaman"]} tanggal '
                      f'{salah_arah[0]["tanggal"]}.', bulan=bulan)

            putus = [t for t in baris if t['saldo'] is None]
            if putus:
                laporan['ok'] = False
                catat('Tinggi', 'Ada baris yang saldonya tidak terbaca',
                      f'{len(putus)} baris pada blok {label} (halaman '
                      f'{self._ringkas_halaman([t["halaman"] for t in putus])}).',
                      bulan=bulan)

            if blok_sebelumnya is not None:
                self._periksa_sambungan(blok_sebelumnya, blok, catat)
            blok_sebelumnya = blok

        return laporan

    def _periksa_sambungan(self, sebelumnya: dict, kini: dict, catat) -> None:
        """Sambungan antar blok laporan di satu berkas: saldo dan tanggalnya."""
        label = kini['periode']['label'] if kini['periode'] else 'Tanpa periode'
        if (sebelumnya['ending'] is not None and kini['ledger'] is not None
                and abs(sebelumnya['ending'] - kini['ledger']) > TOLERANSI):
            catat('Tinggi', 'Saldo antar periode laporan tidak bersambung',
                  f'Saldo akhir periode sebelumnya {sebelumnya["ending"]:,.2f} '
                  f'tidak sama dengan saldo awal periode {label} '
                  f'{kini["ledger"]:,.2f} — ada periode yang tidak disertakan.')

        p_lama = sebelumnya['periode']
        p_baru = kini['periode']
        if not (p_lama and p_baru and p_lama['selesai'] and p_baru['mulai']):
            return
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
