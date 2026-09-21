"""
mestika_statement.py — Extractor rekening Bank Mestika format
"Rekening Koran / Account Statement", yaitu e-statement yang diterbitkan
PT Bank Mestika Dharma Tbk.

Satu format ini dipakai untuk rekening Giro Mestika beserta varian
fasilitasnya (GIRO PRK / PRK-OD). Yang berbeda antar jenis rekening hanya
isi baris "PRODUK / PRODUCT" di kepala laporan — tata letak tabelnya sama.
Jenis rekening karena itu dibaca sebagai DATA ('_jenis_rekening'), bukan
sebagai cabang kode.

EMPAT SIFAT DOKUMEN INI YANG MENENTUKAN CARA MEMBACANYA

1. TABELNYA TIDAK BERGARIS — ia laporan LEBAR TETAP (fixed width).
   Seluruh isi tabel dicetak dengan font monospace (Lucida Console, pitch
   4,8 pt), dan tiap kolom selalu mulai di koordinat x yang sama persis.
   Jadi batas kolom diambil dari koordinat teksnya, bukan dari garis tabel
   (tidak ada) dan bukan pula dari spasi di hasil extract_text() — spasi
   antar kolom hilang begitu kolom sebelahnya terisi penuh, sehingga
   "…PERTKPO 58,260,501.00CR" menempel jadi satu kata.

2. KOLOM "KETERANGAN" SEBENARNYA DUA RUAS.
   Ruas kiri (24 huruf, x=60,8) memuat JENIS transaksi dari kamus tertutup
   — "NK LLG-IN", "TRK TUNAI", "DB TRANSFER DANA BI-FAST", dst. Ruas kanan
   (35 huruf, x=179,6) memuat nama lawan transaksi beserta beritanya.
   Pemisahan ini yang membuat nama lawan bisa dibaca dari STRUKTUR
   dokumennya, bukan ditebak dari bentuk kalimatnya.

3. SALDONYA SALDO DEBET (rekening PRK/overdraft).
   Tiap nominal berakhiran DB/CR dan tiap saldo berakhiran D/C. Pada
   rekening PRK saldonya "D" — artinya nasabah yang berutang ke bank, bukan
   sebaliknya. Transaksi CR MENGURANGI angka D itu, transaksi DB
   menambahnya.

   Karena itu saldo bersaldo D diserahkan ke engine sebagai angka NEGATIF
   (dan saldo C sebagai positif). Ini bukan kosmetik: seluruh pemeriksaan
   engine bertumpu pada "saldo sebelumnya + mutasi bertanda = saldo baris
   ini" (engine/anomaly_detector.py CHECK 2). Kalau saldo D dikirim sebagai
   angka positif, arah aritmatikanya terbalik dan SELURUH 1.180 baris
   dokumen referensi akan dilaporkan sebagai "Running Balance Tidak
   Konsisten" — ribuan temuan palsu yang menenggelamkan temuan asli.
   Angka negatif juga yang benar secara ekonomi: plafon terpakai adalah
   kewajiban, bukan dana milik nasabah.

4. SATU PDF MEMUAT BEBERAPA LAPORAN BULANAN.
   Tiap blok "PERIODE / PERIOD" punya kepala sendiri, penomoran halaman
   yang mulai lagi dari 1, baris "SALDO AWAL" di halaman pertamanya,
   "SALDO PINDAHAN" di tiap halaman berikutnya, dan kaki ringkasannya
   sendiri (SALDO AWAL / MUTASI DEBET / MUTASI KREDIT / SALDO AKHIR).
   Tiap blok diperiksa sendiri, lalu sambungan antar blok (bulan & saldo)
   ikut diperiksa supaya periode yang hilang tidak lolos diam-diam.

Halaman yang bukan bagian laporan Mestika (mis. PDF bank lain yang ikut
ter-merge dalam satu berkas) dilewati dan DILAPORKAN sebagai peringatan,
bukan dibuang diam-diam.

Extractor ini hanya menghasilkan data mentah sesuai kontrak BaseExtractor —
tidak tahu apa pun soal Excel/styling.
"""

import re
from datetime import date

import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor
from extractors.peringatan import PencatatPeringatan, pencatat_laporan


BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]

# Judul yang tercetak di kepala SETIAP halaman laporan Mestika. Dipakai
# untuk memisahkan halaman milik laporan ini dari halaman bank lain yang
# ikut tergabung di berkas yang sama.
PENANDA_MESTIKA = 'ACCOUNT STATEMENT'
PENANDA_MESTIKA_ID = 'REKENING KORAN'

# ------------------------------------------------------------------------
#  GEOMETRI KOLOM
# ------------------------------------------------------------------------
# Tabelnya tak bergaris tapi lebar-tetap: tiap ruas selalu mulai di x yang
# sama, dengan pitch monospace 4,8 pt. Nilai di bawah diukur dari PDF
# referensi dan berlaku untuk seluruh 40 halamannya.
#
# Ruas diberi RENTANG x0 (bukan satu titik) supaya pembulatan koordinat PDF
# tidak memindahkan satu huruf ke kolom sebelah.
PITCH = 4.8

# (nama_ruas, x0 awal ruas, lebar dalam huruf, batas kiri, batas kanan)
RUAS = (
    ('tanggal',  33.2,  5,  28.0,  59.0),
    ('jenis',    60.8, 24,  59.0, 178.0),
    ('nama',    179.6, 35, 178.0, 350.0),
    ('cbg',     352.4,  3, 350.0, 368.0),
)

# Kolom nominal & saldo RATA KANAN, jadi x0-nya bergeser mengikuti panjang
# angkanya — nominal bermiliar mulai di x≈385, yang akan tertukar dengan
# kolom Cbg kalau dipisah memakai x0. Keduanya karena itu dipisah memakai
# TEPI KANAN: nominal berakhir di x≈471, saldo di x≈563.
BATAS_KANAN_NOMINAL = 473.5

# Batas kiri isi tabel. Paragraf syarat & ketentuan di kaki halaman mulai di
# x=33,2 — sama persis dengan kolom Tanggal — sehingga tidak bisa dibedakan
# lewat koordinat saja. Yang membedakannya: baris tabel selalu berbentuk
# baris transaksi atau baris saldo yang sah, sedangkan paragraf tidak.
# Baris SAMBUNGAN (lanjutan nama/keterangan) tidak pernah menyentuh kolom
# Tanggal, jadi ia wajib mulai di kanan batas ini.
BATAS_KIRI_SAMBUNGAN = 59.0

# Dua baris teks dianggap satu baris visual kalau selisih 'top'-nya di bawah
# ini. Jarak antar baris terdekat di dokumen referensi 7,8 pt.
TOLERANSI_BARIS = 3.0

# Nominal dianggap sama kalau selisihnya di bawah satu sen. Semua angka di
# format ini bersen dua digit, jadi tidak ada pembulatan yang perlu
# ditoleransi.
TOLERANSI = 0.005

# ------------------------------------------------------------------------
#  POLA
# ------------------------------------------------------------------------
RE_TANGGAL = re.compile(r'^(\d{2})/(\d{2})$')
# "58,260,501.00CR" / "1,500.00DB"
RE_NOMINAL = re.compile(r'^([\d,]+\.\d{2})(CR|DB)$')
# "26,659,133,801.55D" — D = saldo debet (PRK terpakai), C = saldo kredit.
RE_SALDO = re.compile(r'^([\d,]+\.\d{2})([DC])$')
RE_KAKI = re.compile(
    r'^(SALDO AWAL|MUTASI DEBET|MUTASI KREDIT|SALDO AKHIR):([\d,]+\.\d{2})([DC])?$')
RE_REKENING = re.compile(r'NO\.\s*REK\s*/\s*ACC\.\s*NO\s*:\s*(\d+)')
RE_NAMA = re.compile(r'^(.*?)\s*NO\.\s*REK\s*/\s*ACC\.\s*NO\s*:')
RE_PRODUK = re.compile(r'PRODUK\s*/\s*PRODUCT\s*:\s*(.+?)\s*$', re.MULTILINE)
RE_PERIODE = re.compile(r'PERIODE\s*/\s*PERIOD\s*:\s*([A-Z]{3})\s*(\d{4})')
RE_HALAMAN = re.compile(r'HALAMAN\s*/\s*PAGE\s*:\s*(\d+)\s*/\s*(\d+)')

# Bulan di kepala laporan ditulis singkatan Inggris.
BULAN_PERIODE = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}

# ------------------------------------------------------------------------
#  KAMUS JENIS TRANSAKSI
# ------------------------------------------------------------------------
# Ruas kiri kolom Keterangan berisi kode jenis transaksi dari kamus
# tertutup. Kamus ini memutuskan dari mana nama lawan transaksi diambil —
# lihat _nama_lawan().
#
# 'label' = nama baku yang dipakai kalau barisnya memang TIDAK memuat nama
# siapa pun (biaya bank, bunga, setor/tarik tunai, kliring warkat sendiri).
# Tanpa pembakuan ini satu kanal pecah jadi ratusan baris berbeda di Rekap
# hanya karena nomor warkat/slipnya berganti tiap transaksi — dan Rekap
# itulah yang dipakai menghitung HHI (lihat engine/penyatu_nama.py).
LABEL_BAKU = {
    # Baris bank sendiri: bunga, biaya, koreksi.
    'BUNGA PRK/OD':             'Bunga PRK/OD',
    'KOREKSI CR BUNGA PRK/OD':  'Koreksi Bunga PRK/OD',
    'BEBAN BUNGA BERJLN / PEN': 'Beban Bunga Berjalan',
    'ADM BLN':                  'Biaya Administrasi',
    'ND BG/CEK/B.STR':          'Biaya Buku Cek/Bilyet Giro',
    'PB DR ADM RTGS VIA IB':    'Biaya Transfer RTGS',
    'BIAYA TRANSFER ONLINE':    'Biaya Transfer Online',
    'DB ADM TRANSFER DANA BI-': 'Biaya Transfer BI-Fast',
    # Kanal tunai & warkat: nomor slip/warkat, bukan nama.
    'TRK TUNAI':                'Tarik Tunai',
    'STR TUNAI':                'Setor Tunai',
    'BYR KLR':                  'Kliring Keluar',
    'BYR PEMIND':               'Pemindahbukuan',
    'ND U/KRD':                 'Angsuran Kredit',
}

# Awalan struktural di depan nama lawan, per kanal:
#   "EBK BCA INDOMOBIL FINANCE INDONE"  → kanal e-banking + bank tujuan
#   "IB PT. TRANS JAYA PERTA 3831815858" → kanal internet banking
#   "ONLINE IB 014 SUHADI 1192497609"    → kanal online + kode bank
#   "MB 20100157295"                     → kanal mobile banking
RE_AWALAN_KANAL = re.compile(r'^(?:EBK|IB|MB|ONLINE\s+IB(?:\s+\d{3})?)\s+')

# Transfer BI-Fast masuk menaruh NAMA BANK PENGIRIM di depan nama orangnya
# ("MANDIRI SAMUDERA KENCANA MAS"). Nama banknya dibuang supaya satu lawan
# transaksi tidak pecah menurut bank yang dipakainya mengirim.
BANK_BI_FAST = (
    'MANDIRI', 'BCA', 'BRI', 'BNI', 'UOB', 'OCBC', 'CIMB', 'PERMATA',
    'DANAMON', 'PANIN', 'MAYBANK', 'BTN', 'BSI', 'MEGA', 'SINARMAS',
    'BUKOPIN', 'DBS', 'HSBC', 'CITIBANK', 'JAGO', 'SEABANK', 'NEO',
    'BTPN', 'MESTIKA', 'ARTHA', 'SAMPOERNA', 'GANESHA', 'INDEX', 'QNB',
)

# Token yang PASTI bukan bagian nama badan usaha, melainkan berita/referensi
# yang tercetak menempel di belakang nama pada kanal kliring (LLG/RTGS):
# deretan >= 6 angka (nomor rekening/invoice), atau token bertanda baca
# dokumen ('/', '#', ';'). Ambang 6 angka sama dengan yang dipakai
# extractors/bri_statement.py untuk mengenali nomor rekening.
RE_TOKEN_REFERENSI = re.compile(r'\d{6,}|[/#;]')

# Panjang minimal potongan nama pemilik rekening yang boleh dijadikan
# penanda "di sini beritanya mulai" — lihat _potong_nama_pemilik().
MIN_POTONGAN_PEMILIK = 4
MIN_SISA_NAMA = 8


class MestikaStatementExtractor(BaseExtractor, PencatatPeringatan):
    """Extractor Bank Mestika format Rekening Koran / Account Statement."""

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        self.warnings: list[str] = []
        self.peringatan: list[dict] = []
        self._doc = None
        self._baris_unik = None
        self._duplikat = 0

    def get_file_prefix(self) -> str:
        return 'MESTIKA'

    # ------------------------------------------------------------------ #
    #  PEMBACAAN HALAMAN                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _baris_visual(page) -> list:
        """
        Kelompokkan huruf per baris visual, lalu urutkan dari atas.

        Dikerjakan dari objek huruf (bukan extract_text) karena spasi antar
        kolom hilang begitu kolom sebelahnya terisi penuh — lihat catatan 1
        di kepala modul.
        """
        chars = sorted(page.chars, key=lambda c: (c['top'], c['x0']))
        baris, kini, top_kini = [], [], None
        for c in chars:
            if top_kini is None or abs(c['top'] - top_kini) <= TOLERANSI_BARIS:
                if top_kini is None:
                    top_kini = c['top']
                kini.append(c)
            else:
                baris.append(kini)
                kini, top_kini = [c], c['top']
        if kini:
            baris.append(kini)
        return baris

    @staticmethod
    def _ruas(chars: list) -> dict:
        """
        Pecah satu baris visual menjadi ruas-ruas kolom.

        Tiap ruas dirakit ulang sebagai teks lebar-tetap: posisi huruf di
        dalam ruas dihitung dari x0-nya sendiri, sehingga spasi yang tidak
        dicetak PDF tetap muncul sebagai spasi — itulah yang membuat
        "SALDO PINDAHAN" dan isi kolom yang kosong bisa dibedakan.
        """
        hasil = {}
        for nama, awal, lebar, kiri, kanan in RUAS:
            petak = {}
            for c in chars:
                if kiri <= c['x0'] < kanan:
                    i = round((c['x0'] - awal) / PITCH)
                    if 0 <= i <= lebar:
                        petak[i] = c['text']
            hasil[nama] = (
                ''.join(petak.get(i, ' ') for i in range(max(petak) + 1)).strip()
                if petak else ''
            )
        hasil['nominal'] = ''.join(
            c['text'] for c in chars
            if c['x0'] >= RUAS[-1][4] and c['x1'] <= BATAS_KANAN_NOMINAL).strip()
        hasil['saldo'] = ''.join(
            c['text'] for c in chars if c['x0'] > BATAS_KANAN_NOMINAL).strip()
        hasil['teks'] = ''.join(c['text'] for c in chars).strip()
        hasil['x0'] = min(c['x0'] for c in chars) if chars else 0.0
        return hasil

    @staticmethod
    def _is_header_kolom(teks: str) -> bool:
        """
        Baris nama kolom dicetak dua kali per huruf untuk meniru tebal
        ("DDaattee DDeessccrriippttiioonn…"). Dikenali setelah
        hurufnya dirapatkan kembali.
        """
        rapat = re.sub(r'(.)\1', r'\1', teks).upper()
        return 'DESCRIPTION' in rapat and 'BALANCE' in rapat

    @staticmethod
    def _angka(teks: str) -> float:
        return float(teks.replace(',', ''))

    @classmethod
    def _saldo_bertanda(cls, angka: str, tanda: str) -> float:
        """
        Saldo D (debet / plafon PRK terpakai) → negatif; saldo C → positif.
        Lihat catatan 3 di kepala modul.
        """
        nilai = cls._angka(angka)
        return -nilai if tanda == 'D' else nilai

    # ------------------------------------------------------------------ #
    #  PARSING DOKUMEN                                                   #
    # ------------------------------------------------------------------ #

    def _parse_document(self) -> dict:
        if self._doc is None:
            self._doc = self._parse_document_sekali()
        return self._doc

    def _parse_document_sekali(self) -> dict:
        halaman_meta, blok_per_periode, urutan = [], {}, []
        asing, halaman_tercetak = [], set()
        self._duplikat = 0

        with pdfplumber.open(self.pdf_path) as pdf:
            for urut, page in enumerate(pdf.pages, start=1):
                teks = (page.extract_text() or '')
                atas = teks.upper()
                if PENANDA_MESTIKA not in atas and PENANDA_MESTIKA_ID not in atas:
                    asing.append(urut)
                    continue

                meta = self._meta_halaman(teks)
                baris_visual = self._baris_visual(page)
                isi = self._isi_tabel(baris_visual)

                # Halaman tanpa kepala periode DAN tanpa isi tabel adalah
                # limpahan paragraf syarat & ketentuan dari halaman
                # sebelumnya (dokumen referensi punya satu). Itu bagian sah
                # dari laporan, jadi dilewati tanpa peringatan.
                if meta['periode'] is None and not isi['baris'] and not isi['kaki']:
                    continue

                halaman_meta.append({
                    'urut': urut,
                    'no_tercetak': meta['no_halaman'],
                    'total_tercetak': meta['total_halaman'],
                    'periode': meta['periode'],
                    'ada_header_kolom': isi['ada_header_kolom'],
                    'jumlah_baris': len(isi['baris']),
                })

                kunci = meta['periode']
                # Halaman yang nomor cetaknya sudah pernah muncul untuk
                # periode yang sama adalah laporan yang SAMA, tercetak dua
                # kali di berkas gabungan. Barisnya dilewati supaya mutasinya
                # tidak terhitung ganda; jumlahnya dilaporkan lewat
                # validate()['duplikat_digabung'].
                #
                # Pengulangan dikenali dari NOMOR HALAMAN TERCETAK, bukan
                # dari isi barisnya. Dua baris yang seluruh nilainya sama —
                # termasuk saldo berjalannya — bisa sah: pada dokumen
                # referensi ada dua setoran tunai Rp300 juta di hari yang
                # sama, yang saldonya kembali ke angka yang sama karena di
                # antaranya ada penarikan Rp300 juta. Menyamakan baris
                # seperti itu akan MENGHILANGKAN transaksi yang benar.
                if (kunci, meta['no_halaman']) in halaman_tercetak:
                    self._duplikat += len(isi['baris'])
                    continue
                if meta['no_halaman'] is not None:
                    halaman_tercetak.add((kunci, meta['no_halaman']))

                if kunci not in blok_per_periode:
                    blok_per_periode[kunci] = {
                        'periode': kunci,
                        'tahun': meta['tahun'],
                        'bulan': meta['bulan'],
                        'meta': meta,
                        'halaman': [],
                        'baris': [],
                        'penanda_saldo': [],
                        'saldo_awal_kepala': None,
                        'ringkasan': None,
                    }
                    urutan.append(kunci)
                blok = blok_per_periode[kunci]
                blok['halaman'].append(urut)

                for penanda in isi['penanda_saldo']:
                    if penanda['jenis'] == 'SALDO AWAL' and blok['saldo_awal_kepala'] is None:
                        blok['saldo_awal_kepala'] = penanda['saldo']
                blok['penanda_saldo'] += [dict(p, halaman=urut)
                                          for p in isi['penanda_saldo']]

                for posisi, baris in enumerate(isi['baris'], start=1):
                    tanggal = self._tanggal_baris(baris['tanggal'], blok)
                    if tanggal is None:
                        continue
                    baris.update({'tanggal': tanggal, 'halaman': urut,
                                  'urut': posisi, 'periode': kunci})
                    blok['baris'].append(baris)

                if isi['kaki'] and blok['ringkasan'] is None:
                    blok['ringkasan'] = isi['kaki']

        if asing:
            self._catat(
                'Tinggi', 'Berkas memuat halaman yang bukan rekening Mestika',
                f'{len(asing)} halaman (mis. halaman {asing[0]}) tidak memuat '
                f'kepala "Rekening Koran / Account Statement" Bank Mestika dan '
                f'tidak ikut diekstrak. Periksa apakah berkasnya gabungan '
                f'beberapa dokumen.',
                halaman=str(asing[0]))

        return {'blok': [blok_per_periode[k] for k in urutan],
                'halaman': halaman_meta}

    def _meta_halaman(self, teks: str) -> dict:
        meta = {'nama_pemilik': '', 'no_rekening': '', 'jenis_rekening': '',
                'periode': None, 'bulan': None, 'tahun': None,
                'no_halaman': None, 'total_halaman': None}
        for baris in teks.split('\n'):
            m = RE_REKENING.search(baris)
            if m:
                meta['no_rekening'] = m.group(1)
                mn = RE_NAMA.match(baris)
                if mn:
                    meta['nama_pemilik'] = mn.group(1).strip()
        m = RE_PRODUK.search(teks)
        if m:
            meta['jenis_rekening'] = m.group(1).strip()
        m = RE_PERIODE.search(teks.upper())
        if m and m.group(1) in BULAN_PERIODE:
            meta['periode'] = f'{m.group(1)} {m.group(2)}'
            meta['bulan'] = BULAN_PERIODE[m.group(1)]
            meta['tahun'] = int(m.group(2))
        m = RE_HALAMAN.search(teks.upper())
        if m:
            meta['no_halaman'] = int(m.group(1))
            meta['total_halaman'] = int(m.group(2))
        return meta

    def _isi_tabel(self, baris_visual: list) -> dict:
        """
        Ambil isi tabel satu halaman: baris transaksi, penanda saldo, kaki
        ringkasan.

        Isi tabel dibatasi DUA SISI, dan keduanya perlu:
          - sisi atas  : baris nama kolom. Tanpa batas ini, baris kepala
                         ("TRANS JAYA PERTAMA PT. NO. REK / ACC. NO : …")
                         terbaca sebagai sambungan keterangan, karena ia pun
                         mulai di kanan kolom Tanggal.
          - sisi bawah : paragraf syarat & ketentuan. Ia mulai di x yang
                         sama persis dengan kolom Tanggal, jadi yang
                         membedakannya bukan koordinat melainkan bentuk:
                         baris tabel selalu baris transaksi / penanda saldo
                         yang sah.
        """
        hasil = {'baris': [], 'penanda_saldo': [], 'kaki': None,
                 'ada_header_kolom': False}
        mulai = None
        for i, chars in enumerate(baris_visual):
            if self._is_header_kolom(''.join(c['text'] for c in chars)):
                hasil['ada_header_kolom'] = True
                mulai = i + 1
                break
        if mulai is None:
            return hasil

        kaki = {}
        terakhir = None
        for chars in baris_visual[mulai:]:
            r = self._ruas(chars)
            if not r['teks']:
                continue

            m = RE_KAKI.match(r['teks'])
            if m:
                nilai = self._angka(m.group(2))
                if m.group(1) in ('SALDO AWAL', 'SALDO AKHIR'):
                    nilai = self._saldo_bertanda(m.group(2), m.group(3) or 'C')
                kaki[m.group(1)] = nilai
                terakhir = None
                continue

            baris = self._baca_baris(r)
            if baris is not None:
                hasil['baris'].append(baris)
                terakhir = baris
                continue

            penanda = self._baca_penanda_saldo(r)
            if penanda is not None:
                hasil['penanda_saldo'].append(penanda)
                terakhir = None
                continue

            if (terakhir is not None
                    and r['x0'] >= BATAS_KIRI_SAMBUNGAN
                    and not r['nominal'] and not r['saldo']):
                # Sambungan nama/keterangan baris di atasnya. Syarat "tidak
                # ada nominal & saldo" menjaga paragraf kaki halaman tidak
                # ikut tersangkut: prosanya membentang selebar halaman,
                # jadi ia selalu mengisi ruas nominal/saldo — sedangkan
                # sambungan tidak pernah.
                if r['jenis']:
                    terakhir['sambungan_jenis'].append(r['jenis'])
                if r['nama']:
                    terakhir['sambungan_nama'].append(r['nama'])
                continue

            # Bukan bentuk tabel mana pun: paragraf kaki halaman. Isi tabel
            # habis di sini.
            if r['x0'] < BATAS_KIRI_SAMBUNGAN:
                terakhir = None

        if kaki:
            hasil['kaki'] = {
                'saldo_awal':   kaki.get('SALDO AWAL'),
                'total_debet':  kaki.get('MUTASI DEBET'),
                'total_kredit': kaki.get('MUTASI KREDIT'),
                'saldo_akhir':  kaki.get('SALDO AKHIR'),
            }
        return hasil

    def _baca_baris(self, r: dict):
        """Satu baris transaksi, atau None kalau bentuknya bukan itu."""
        if not RE_TANGGAL.match(r['tanggal']):
            return None
        mn = RE_NOMINAL.match(r['nominal'])
        ms = RE_SALDO.match(r['saldo'])
        if not mn or not ms:
            return None
        nominal = self._angka(mn.group(1))
        arah = 'K' if mn.group(2) == 'CR' else 'D'
        return {
            'tanggal': r['tanggal'],
            'jenis': r['jenis'],
            'nama': r['nama'],
            'sambungan_jenis': [],
            'sambungan_nama': [],
            'cbg': r['cbg'],
            'nominal': nominal,
            'arah': arah,
            'mutasi': nominal if arah == 'K' else -nominal,
            'saldo': self._saldo_bertanda(ms.group(1), ms.group(2)),
            'teks_mentah': r['teks'],
        }

    def _baca_penanda_saldo(self, r: dict):
        """Baris "SALDO AWAL" / "SALDO PINDAHAN" di kepala tabel halaman."""
        jenis = r['jenis'].strip().upper()
        if jenis not in ('SALDO AWAL', 'SALDO PINDAHAN'):
            return None
        m = RE_SALDO.match(r['nominal'])
        if not m:
            return None
        return {'jenis': jenis,
                'saldo': self._saldo_bertanda(m.group(1), m.group(2))}

    @staticmethod
    def _tanggal_baris(tanggal: str, blok: dict):
        """
        Tanggal baris ditulis "DD/MM" tanpa tahun; tahunnya dari kepala
        laporan. Satu blok Mestika mencakup satu bulan, jadi bulan yang
        berbeda dari kepala hanya mungkin terjadi di pergantian tahun —
        ditangani, bukan dibuang.
        """
        m = RE_TANGGAL.match(tanggal)
        if not m or blok['tahun'] is None:
            return None
        hari, bulan = int(m.group(1)), int(m.group(2))
        tahun = blok['tahun']
        if blok['bulan'] == 1 and bulan == 12:
            tahun -= 1
        elif blok['bulan'] == 12 and bulan == 1:
            tahun += 1
        try:
            return date(tahun, bulan, hari)
        except ValueError:
            return None

    def _baris(self) -> list:
        """
        Seluruh baris transaksi dokumen.

        Halaman yang terulang (laporan bulan yang sama tercetak dua kali di
        berkas gabungan) sudah disingkirkan saat parsing — lihat
        _parse_document_sekali(). Di sini tinggal meratakan blok jadi satu
        daftar, urut seperti tercetak.
        """
        if self._baris_unik is None:
            self._baris_unik = [b for blok in self._parse_document()['blok']
                                for b in blok['baris']]
        return self._baris_unik

    def _duplikat_digabung(self) -> int:
        """Jumlah baris yang dilewati karena halamannya terulang di berkas."""
        self._parse_document()
        return self._duplikat

    # ------------------------------------------------------------------ #
    #  NAMA LAWAN TRANSAKSI                                              #
    # ------------------------------------------------------------------ #

    def _nama_lawan(self, baris: dict) -> str:
        """
        Nama lawan transaksi, dengan jaminan hasilnya selalu bisa dibaca.

        Diambil dari STRUKTUR dokumennya: ruas kiri kolom Keterangan
        menyebut kanal transaksinya (kamus tertutup, 22 nilai di dokumen
        referensi), dan kanal itulah yang menentukan bentuk ruas kanannya.
        Baris yang memang tidak memuat nama siapa pun (biaya, bunga, setor/
        tarik tunai, warkat kliring sendiri) diberi label kanalnya — lihat
        LABEL_BAKU.
        """
        nama = self._nama_lawan_mentah(baris)
        return nama if self._bermakna(nama) else 'Tidak Teridentifikasi'

    # Nomor rekening terpendek yang masuk akal dipakai sebagai identitas.
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

    def _nama_lawan_mentah(self, baris: dict) -> str:
        jenis = ' '.join(baris['jenis'].split()).upper()

        baku = LABEL_BAKU.get(jenis)
        if baku:
            return baku

        teks = ' '.join(baris['nama'].split())
        if not teks:
            return ''

        # Kanal e-banking/internet banking: awalan kanal (dan bank tujuan
        # untuk BI-Fast keluar) mendahului nama penerimanya.
        sisa = RE_AWALAN_KANAL.sub('', teks, count=1)
        if sisa != teks:
            sisa = self._buang_nama_bank(sisa)
            return self._potong_referensi(sisa)

        # BI-Fast masuk: "<BANK> <NAMA PENGIRIM>".
        if jenis.startswith('KR-TRANSFER DANA BI-FAST'):
            return self._potong_referensi(self._buang_nama_bank(teks))

        # Kliring masuk (LLG/RTGS): nama lawan dicetak menempel dengan
        # beritanya tanpa pemisah. Yang dipotong hanya yang PASTI bukan
        # nama — lihat _potong_referensi() dan _potong_nama_pemilik().
        return self._potong_nama_pemilik(self._potong_referensi(teks))

    @staticmethod
    def _buang_nama_bank(teks: str) -> str:
        """
        Buang nama bank lawan di depan nama orang/lembaganya.

        Kalau setelah dibuang tidak tersisa apa-apa, nama banknya memang
        satu-satunya isi kolom — kembalikan apa adanya, jangan jadi kosong.
        """
        token = teks.split()
        if len(token) >= 2 and token[0].upper() in BANK_BI_FAST:
            return ' '.join(token[1:])
        return teks

    @staticmethod
    def _potong_referensi(teks: str) -> str:
        """
        Potong di token pertama yang PASTI bukan bagian nama badan usaha:
        deretan >= 6 angka (nomor rekening/invoice/berita) atau token
        bertanda baca dokumen ('/', '#', ';').

        Ini satu-satunya pemotongan yang tidak menebak: nama perusahaan
        Indonesia tidak memuat deretan enam angka maupun garis miring.
        Berita yang berupa kata biasa ("TRANS JAYA PE") sengaja dibiarkan —
        memotongnya butuh tebakan, dan menahan sedikit derau lebih aman
        daripada memenggal nama yang sah.
        """
        keluar = []
        for token in teks.split():
            if RE_TOKEN_REFERENSI.search(token):
                break
            if token.startswith('-') and len(token) > 3:
                break
            keluar.append(token)
        return ' '.join(keluar) if keluar else teks

    def _potong_nama_pemilik(self, teks: str) -> str:
        """
        Potong ekor yang ternyata NAMA PEMILIK REKENING INI.

        Berita kliring masuk lazim menyebut penerimanya — yaitu pemilik
        rekening yang sedang diperiksa — dan Mestika mencetaknya menempel di
        belakang nama pengirim, terpotong selebar kolom:

            "SARI ADITYA LOKA PT TRANS JAYA PERT"  (pemilik: TRANS JAYA PERTAMA PT.)
            "TUNGGAL PERKASA PLANTATIONS PT TRAN"

        Ekor itu bukan lawan transaksi. Ia dikenali sebagai AWALAN dari nama
        pemilik rekening — bukan kemiripan huruf — sehingga tidak bisa salah
        mengenai pihak lain yang kebetulan mirip.

        Dua syarat menjaga pemotongan ini tidak makan nama yang sah:
          - potongannya minimal 4 huruf, supaya "PT" atau "TR" tidak lolos;
          - sisa di kirinya minimal 8 huruf, supaya baris yang justru DIMULAI
            nama pemilik ("PT TRANS JAYA PERTAMA BANK MESTIKA", transfer
            antar rekening sendiri) tidak habis terpotong jadi "PT".
        """
        pemilik = self._nama_pemilik_normal()
        if not pemilik:
            return teks
        token = teks.split()
        for i in range(1, len(token)):
            sisa = ' '.join(token[:i])
            if len(sisa) < MIN_SISA_NAMA:
                continue
            ekor = re.sub(r'[^A-Z0-9 ]', '', ' '.join(token[i:]).upper())
            if len(ekor) >= MIN_POTONGAN_PEMILIK and pemilik.startswith(ekor):
                return sisa
        return teks

    def _nama_pemilik_normal(self) -> str:
        blok = self._parse_document()['blok']
        if not blok:
            return ''
        return re.sub(r'[^A-Z0-9 ]', '', blok[0]['meta']['nama_pemilik'].upper())

    @staticmethod
    def _keterangan(baris: dict) -> str:
        """
        Keterangan transaksi: jenis + seluruh isi kolom keterangan, termasuk
        baris sambungannya, ditambah kode cabang.

        Semuanya ikut dibawa karena justru itu yang membedakan dua transaksi
        yang kebetulan bertanggal dan bernominal sama — nomor warkat, nomor
        rekening lawan, dan nomor berita. Tanpanya, pemeriksaan duplikasi di
        engine menandai transaksi sah yang memang berulang di hari yang sama.
        """
        bagian = [baris['jenis']]
        bagian += baris['sambungan_jenis']
        if baris['nama']:
            bagian.append(baris['nama'])
        bagian += baris['sambungan_nama']
        if baris['cbg']:
            bagian.append(f"[{baris['cbg']}]")
        return ' '.join(' '.join(b.split()) for b in bagian if b and b.strip())

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

        ember, awal_bulan = {}, {}
        for blok in doc['blok']:
            if blok['bulan'] is None or blok['tahun'] is None:
                continue
            kunci = (blok['tahun'], blok['bulan'])
            awal = blok['saldo_awal_kepala']
            if awal is None and blok['ringkasan']:
                awal = blok['ringkasan'].get('saldo_awal')
            if awal is not None:
                awal_bulan.setdefault(kunci, awal)

            berjalan = awal
            for hari in self._hari_sebulan(blok['tahun'], blok['bulan']):
                if hari in per_hari:
                    berjalan = per_hari[hari]
                ember.setdefault(kunci, []).append(
                    {'Bulan': BULAN_ORDER[blok['bulan'] - 1],
                     'Tanggal': hari.day, 'Saldo Akhir Harian': berjalan})

        for (tahun, bulan), data in ember.items():
            nama_bulan = BULAN_ORDER[bulan - 1]
            hasil[nama_bulan] = {'df': pd.DataFrame(data), 'tahun': str(tahun)}
            if awal_bulan.get((tahun, bulan)) is not None:
                hasil[f'_saldo_awal_{nama_bulan}'] = awal_bulan[(tahun, bulan)]

        # '_biaya_admin' dan '_bunga_pajak' SENGAJA tidak dikirim:
        #
        #   biaya admin — baris "ADM BLN" di dokumen referensi jatuh pada
        #   tanggal 23, 23, dan 24 (satu rekening, tiga bulan). Satu rekening
        #   terlalu sedikit untuk dijadikan ketentuan bank, dan pemeriksaan
        #   yang dilewati jauh lebih aman daripada jadwal yang ditebak.
        #
        #   bunga & pajak — pemeriksaannya di engine memodelkan PPh Final 20%
        #   atas bunga SIMPANAN yang diterima nasabah (mutasi kredit).
        #   Rekening ini fasilitas PRK: bunganya DIBEBANKAN ke nasabah
        #   (mutasi debet) dan tidak berpasangan pajak. Mengirim metadatanya
        #   hanya akan menanyakan pasangan yang memang tidak pernah ada.

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

    @staticmethod
    def _hari_sebulan(tahun: int, bulan: int) -> list:
        import calendar
        return [date(tahun, bulan, h)
                for h in range(1, calendar.monthrange(tahun, bulan)[1] + 1)]

    def extract_transaksi(self) -> dict:
        ember = {}
        for b in self._baris():
            nama_bulan = BULAN_ORDER[b['tanggal'].month - 1]
            ember.setdefault(nama_bulan, []).append({
                'Bulan': nama_bulan,
                'Tanggal': b['tanggal'].day,
                'Jenis Mutasi': 'Kredit' if b['arah'] == 'K' else 'Debit',
                'Mutasi': b['nominal'],
                'Nama Pengirim/Penerima': self._nama_lawan(b),
                'Keterangan Transaksi': self._keterangan(b),
            })
        return {b: pd.DataFrame(v) for b, v in ember.items() if v}

    def _provenance(self, baris: list) -> dict:
        """
        Jejak cetak dokumen (lihat kontrak '_provenance' di extractors/base.py).

        'teks_mentah' dikirim: seluruh isi baris di format ini teks cetak
        mesin — jenis transaksi dari kamus tertutup, nomor warkat, dan nama
        lawan dari sistem kliring. Tidak ada ruas berita bebas yang diketik
        nasabah dengan angka bergaya Indonesia, yang akan jadi temuan palsu.
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
                'teks_mentah': b['teks_mentah'],
            } for b in baris],
        }

    # ------------------------------------------------------------------ #
    #  VALIDASI OTOMATIS (CHECKSUM)                                      #
    # ------------------------------------------------------------------ #

    def validate(self) -> dict:
        """
        Cocokkan hasil parsing dengan kaki ringkasan resmi tiap blok laporan:
        MUTASI DEBET, MUTASI KREDIT, SALDO AWAL, dan SALDO AKHIR.

        Selain itu diperiksa hal-hal yang tidak butuh angka ringkasan:
          - rantai saldo berjalan di dalam blok, termasuk baris "SALDO
            PINDAHAN" di kepala tiap halaman — penanda itu ikut diperiksa
            supaya halaman yang hilang dari berkas ketahuan;
          - identitas rekening harus sama di seluruh blok;
          - sambungan antar blok: bulan harus berurutan dan saldo akhir blok
            sebelumnya harus sama dengan saldo awal blok berikutnya.
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
                  f'Tabel "{PENANDA_MESTIKA_ID} / {PENANDA_MESTIKA}" tidak '
                  f'ditemukan atau kosong.')
            return laporan

        self._periksa_identitas(doc, catat)

        if laporan['duplikat_digabung']:
            catat('Sedang', 'Berkas memuat periode laporan yang tumpang tindih',
                  f'{laporan["duplikat_digabung"]} transaksi tercetak di lebih '
                  f'dari satu blok laporan dan digabung menjadi satu. Total '
                  f'mutasi laporan karena itu lebih kecil dari penjumlahan '
                  f'mentah tiap blok — ini disengaja.')

        for blok in doc['blok']:
            laporan['periods'].append(self._periksa_blok(blok, catat, laporan))

        self._periksa_sambungan(doc, catat)
        return laporan

    def _periksa_blok(self, blok: dict, catat, laporan: dict) -> dict:
        label = blok['periode'] or 'Tanpa periode'
        bulan = BULAN_ORDER[blok['bulan'] - 1] if blok['bulan'] else '-'
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
                         'opening': ringkasan.get('saldo_awal'),
                         'closing': ringkasan.get('saldo_akhir')},
            'actual': {'total_debit': total_debet,
                       'total_credit': total_kredit,
                       'opening': blok['saldo_awal_kepala'],
                       'closing': saldo_akhir},
            'checks': {},
        }
        for kunci in ('total_debit', 'total_credit', 'opening', 'closing'):
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
                  f'Blok {label}: baris SALDO AWAL / MUTASI DEBET / MUTASI '
                  f'KREDIT / SALDO AKHIR tidak terbaca, jadi hasil ekstraksi '
                  f'blok ini tidak bisa dicocokkan dengan angka resmi PDF.',
                  bulan=bulan)

        self._periksa_rantai_saldo(blok, label, bulan, catat, laporan)
        self._periksa_pindahan(blok, label, bulan, catat, laporan)
        return hasil

    def _periksa_rantai_saldo(self, blok: dict, label: str, bulan: str,
                              catat, laporan: dict) -> None:
        """
        Saldo berjalan harus menyambung DI DALAM satu blok laporan: saldo
        baris sebelumnya + mutasi baris ini = saldo baris ini. Baris pertama
        disambungkan ke SALDO AWAL di kepala halaman pertama blok.
        """
        putus = []
        sebelumnya = blok['saldo_awal_kepala']
        if sebelumnya is None and blok['ringkasan']:
            sebelumnya = blok['ringkasan'].get('saldo_awal')
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
                  f'{len(putus)} baris pada blok {label} saldonya tidak sama '
                  f'dengan saldo baris sebelumnya ditambah mutasinya, mis. '
                  f'halaman {putus[0]["halaman"]} tanggal '
                  f'{putus[0]["tanggal"].isoformat()}.',
                  bulan=bulan, halaman=str(putus[0]['halaman']))

    def _periksa_pindahan(self, blok: dict, label: str, bulan: str,
                          catat, laporan: dict) -> None:
        """
        "SALDO PINDAHAN" di kepala tiap halaman harus sama dengan saldo baris
        terakhir halaman sebelumnya.

        Pemeriksaan ini yang menangkap HALAMAN YANG HILANG dari berkas:
        rantai saldo antar baris tetap mulus kalau satu halaman penuh
        dibuang bersama isinya, tapi saldo pindahannya tidak akan cocok lagi.
        """
        per_halaman = {}
        for b in blok['baris']:
            per_halaman[b['halaman']] = b['saldo']

        selisih = []
        for penanda in blok.get('penanda_saldo', []):
            if penanda['jenis'] != 'SALDO PINDAHAN':
                continue
            halaman_sebelum = [h for h in per_halaman if h < penanda['halaman']]
            if not halaman_sebelum:
                continue
            akhir = per_halaman[max(halaman_sebelum)]
            if abs(akhir - penanda['saldo']) > TOLERANSI:
                selisih.append(penanda)
        if selisih:
            laporan['ok'] = False
            catat('Tinggi', 'Saldo pindahan antar halaman tidak menyambung',
                  f'{len(selisih)} halaman pada blok {label} membuka dengan '
                  f'SALDO PINDAHAN yang berbeda dari saldo akhir halaman '
                  f'sebelumnya, mis. halaman {selisih[0]["halaman"]}. '
                  f'Kemungkinan ada halaman yang hilang dari berkas.',
                  bulan=bulan, halaman=str(selisih[0]['halaman']))

    def _periksa_identitas(self, doc: dict, catat) -> None:
        rekening = {b['meta']['no_rekening'] for b in doc['blok']
                    if b['meta']['no_rekening']}
        if len(rekening) > 1:
            catat('Tinggi', 'Berkas memuat lebih dari satu nomor rekening',
                  f'Nomor rekening yang terbaca: {", ".join(sorted(rekening))}. '
                  f'Laporan ini menggabungkan semuanya menjadi satu — pastikan '
                  f'itu memang yang diinginkan.')

    def _periksa_sambungan(self, doc: dict, catat) -> None:
        """
        Antar blok laporan: bulannya harus berurutan dan saldonya nyambung.
        """
        blok = [b for b in doc['blok'] if b['bulan'] and b['tahun']]
        blok.sort(key=lambda b: (b['tahun'], b['bulan']))
        for a, b in zip(blok, blok[1:]):
            urut_a = a['tahun'] * 12 + a['bulan']
            urut_b = b['tahun'] * 12 + b['bulan']
            if urut_b - urut_a > 1:
                hilang = urut_b - urut_a - 1
                catat('Tinggi', 'Ada periode laporan yang tidak tercakup berkas',
                      f'{hilang} bulan antara {a["periode"]} dan {b["periode"]} '
                      f'tidak ada laporannya, jadi mutasi bulan itu tidak ikut '
                      f'diperiksa.',
                      bulan=BULAN_ORDER[b['bulan'] - 1])

            akhir = (a['ringkasan'] or {}).get('saldo_akhir')
            awal = b['saldo_awal_kepala']
            if awal is None and b['ringkasan']:
                awal = b['ringkasan'].get('saldo_awal')
            if akhir is not None and awal is not None and abs(akhir - awal) > TOLERANSI:
                catat('Tinggi', 'Saldo antar periode laporan tidak menyambung',
                      f'Saldo akhir {a["periode"]} ({akhir:,.2f}) berbeda dari '
                      f'saldo awal {b["periode"]} ({awal:,.2f}).',
                      bulan=BULAN_ORDER[b['bulan'] - 1])
