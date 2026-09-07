"""
mandiri_statement.py — Extractor rekening koran Bank Mandiri format
"e-Statement" (Livin'/Mandiri Online — Tabungan, Tabungan Bisnis, Giro).

Beda dari format Kopra (extractors/mandiri_kopra.py):
  - Nominal Debit & Kredit TIDAK dipisah dua kolom. Ada satu kolom "Nominal"
    yang bertanda (+ masuk, - keluar), dan formatnya gaya Indonesia
    (1.234.567,89) — bukan gaya Inggris seperti Kopra.
  - Setiap transaksi punya nomor urut ("No") yang dicetak bank, berjalan
    1..N per periode laporan. Nomor inilah yang dipakai sebagai anchor baris,
    sekaligus jadi alat deteksi baris hilang (lihat validate()).
  - Ringkasan resmi tidak mencantumkan jumlah transaksi, hanya Saldo Awal,
    Dana Masuk, Dana Keluar, dan Saldo Akhir.

Pendekatan parsing:
  1. Geometri kolom dideteksi DINAMIS dari baris header tiap halaman
     ("No Date Remarks Amount (IDR) Balance (IDR)"). Halaman pertama tiap
     periode punya blok ringkasan di atas tabel, jadi posisi Y header
     berbeda-beda antar halaman.
  2. Kolom Nominal & Saldo rata-kanan, jadi patokannya x1 — bukan x0.
     Nominal besar seperti "+300.000.000,00" menggeser x0-nya sampai masuk
     wilayah kolom Keterangan; kalau dipatok x0, angka itu akan terbaca
     sebagai bagian keterangan dan nominalnya hilang.
  3. Satu transaksi memakan 2-4 baris teks: keterangan bisa berada di baris
     DI ATAS maupun DI BAWAH baris bernomor. Batas antar-transaksi diambil
     di titik tengah antar anchor nomor.
  4. Struktur baris keterangan dipertahankan (list, bukan satu string),
     karena posisi barisnya bermakna: baris terakhir hampir selalu
     "<NAMA LAWAN TRANSAKSI> <nomor rekening>".

Extractor ini hanya menghasilkan data mentah sesuai kontrak BaseExtractor —
tidak tahu apa pun soal Excel/styling.
"""

import re
from datetime import date, timedelta
import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor

BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]

# Singkatan bulan Inggris (dipakai PDF e-Statement) -> nomor bulan
BULAN_EN = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}

# Nominal e-Statement berformat Indonesia dengan tanda: "+1.234.567,89".
AMOUNT_RE = re.compile(r'^[+-]?\d{1,3}(?:\.\d{3})*,\d{2}$')

# Posisi kolom hasil pengukuran PDF referensi (halaman A4 lebar 595pt).
# Hanya dipakai sebagai cadangan kalau sebagian header tidak terbaca.
FALLBACK_COLS = {
    'no_x0':      20.0,
    'tanggal_x0': 52.0,
    'ket_x0':    124.0,
    'nominal_x1': 432.0,
    'saldo_x1':   571.0,
}

# Penanda akhir tabel. "ini adalah batas akhir transaksi anda" dicetak di
# dalam kolom Keterangan (x0 ~234), jadi TIDAK bisa dibedakan dari isi
# transaksi lewat posisi — harus dikenali dari teksnya, kalau tidak kalimat
# itu ikut menempel jadi keterangan transaksi terakhir.
FOOTER_MARKERS = (
    'ini adalah batas akhir transaksi anda',
    'disclaimer',
    'pt bank mandiri (persero) tbk.',
)

# Kode/nama bank yang dicetak MENDAHULUI nama lawan transaksi pada baris
# nama ("BRI ASNAH 483701008860530", "BANK BNI Ibu ANI ROHIMAH ..."). Bank
# tujuan sudah tercatat di baris keterangan sendiri, jadi di kolom nama
# prefix ini dibuang supaya nama yang sama tidak terpecah jadi beberapa
# grup di Rekap Kredit/Debit.
BANK_PREFIX = (
    'PT.', 'PT', 'BANK', 'BPD', 'BPR', 'TBK.', 'TBK',
    'BRI', 'BCA', 'BNI', 'BSI', 'BTN', 'BTPN', 'BJB', 'DKI',
    'MANDIRI', 'PERMATA', 'CIMB', 'NIAGA', 'DANAMON', 'OCBC', 'NISP',
    'MAYBANK', 'MUAMALAT', 'MEGA', 'SINARMAS', 'PANINBANK', 'PANIN',
    'SEABANK', 'JAGO', 'NEO', 'ALLO', 'BLU', 'SUPERBANK', 'JENIUS',
    'KALBAR', 'KALTENG', 'KALSEL', 'KALTIM', 'JATIM', 'JABAR', 'JATENG',
    'SUMUT', 'SUMSEL', 'SUMBAR', 'SULSELBAR', 'PAPUA', 'BALI', 'NTB',
    'CENTRAL', 'ASIA', 'INDONESIA', 'NEGARA', 'RAKYAT', 'SYARIAH',
    'TENGAH', 'BARAT', 'TIMUR', 'SELATAN', 'UTARA', 'LAIN',
)


class MandiriStatementExtractor(BaseExtractor):

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Peringatan yang terkumpul selama parsing (dibaca app.py / pemanggil).
        self.warnings: list[str] = []
        self._cache = None

    def get_file_prefix(self) -> str:
        return 'MANDIRI'

    # ------------------------------------------------------------------ #
    #  DETEKSI KOLOM DINAMIS                                             #
    # ------------------------------------------------------------------ #

    def _find_columns(self, page, words: list) -> dict | None:
        """
        Cari baris header tabel di halaman ini dan turunkan geometri kolom.

        Header dicetak dua baris (Indonesia lalu Inggris); yang dipakai baris
        Inggris ("No Date Remarks Amount (IDR) Balance (IDR)") karena itu
        baris terakhir sebelum tabel dimulai.

        Mengembalikan None kalau halaman tidak punya tabel transaksi
        (mis. halaman disclaimer di akhir tiap laporan).
        """
        anchor = None
        for w in words:
            if w['text'] == 'Remarks' and w['x0'] < 250:
                anchor = w
        if anchor is None:
            return None

        same_row = [w for w in words if abs(w['top'] - anchor['top']) < 2]
        pos = {}
        for w in same_row:
            pos.setdefault(w['text'], []).append(w)

        cols = {
            'header_y':   anchor['bottom'],
            'ket_x0':     anchor['x0'],
            'no_x0':      pos['No'][0]['x0'] if 'No' in pos else FALLBACK_COLS['no_x0'],
            'tanggal_x0': pos['Date'][0]['x0'] if 'Date' in pos else FALLBACK_COLS['tanggal_x0'],
            'nominal_x1': FALLBACK_COLS['nominal_x1'],
            'saldo_x1':   FALLBACK_COLS['saldo_x1'],
        }

        # Dua kolom angka sama-sama diberi label "(IDR)"; dibedakan lewat
        # urutan kiri-ke-kanan, bukan lewat teksnya.
        idr = sorted(pos.get('(IDR)', []), key=lambda w: w['x1'])
        if len(idr) >= 2:
            cols['nominal_x1'] = idr[0]['x1']
            cols['saldo_x1'] = idr[-1]['x1']
        else:
            self.warnings.append(
                f"Halaman {page.page_number}: batas kolom Nominal/Saldo tidak "
                f"terbaca dari header, memakai posisi cadangan."
            )

        # Ambang antar kolom angka = titik tengah antar tepi kanannya.
        cols['nominal_max'] = (cols['nominal_x1'] + cols['saldo_x1']) / 2
        # Batas kiri wilayah angka. Nominal terpanjang pada PDF referensi
        # ("+1.079.837.415,00") lebarnya ~80pt, jadi ambangnya diambil
        # selebar itu dari tepi kanan kolom Nominal.
        cols['amount_min_x1'] = cols['nominal_x1'] - 80
        return cols

    def _footer_y(self, page, words: list, header_y: float) -> float:
        """
        Batas bawah tabel: baris penanda akhir laporan atau footer halaman.
        """
        lines = {}
        for w in words:
            if w['top'] > header_y:
                lines.setdefault(round(w['top'], 1), []).append(w)

        footer = float(page.height)
        for top, lw in lines.items():
            teks = ' '.join(w['text'] for w in sorted(lw, key=lambda x: x['x0'])).lower()
            if any(teks.startswith(m) for m in FOOTER_MARKERS):
                footer = min(footer, top)
        return footer

    # ------------------------------------------------------------------ #
    #  PENGELOMPOKAN BARIS                                               #
    # ------------------------------------------------------------------ #

    def _page_rows(self, page) -> list:
        """
        Kembalikan transaksi mentah pada satu halaman.

        Anchor baris = nomor urut di kolom "No". Keterangan sebuah transaksi
        bisa berada di atas maupun di bawah baris bernomor itu, jadi batas
        klaster diambil di titik tengah antar anchor.
        """
        words = page.extract_words()
        if not words:
            return []

        cols = self._find_columns(page, words)
        if cols is None:
            return []

        footer_y = self._footer_y(page, words, cols['header_y'])
        body = [w for w in words if cols['header_y'] + 1 < w['top'] < footer_y]
        if not body:
            return []

        anchors = sorted(
            [w for w in body
             if w['x0'] < cols['tanggal_x0'] - 5 and re.match(r'^\d+$', w['text'])],
            key=lambda w: w['top'],
        )

        rows = []
        for i, a in enumerate(anchors):
            y0 = cols['header_y'] if i == 0 else (anchors[i - 1]['top'] + a['top']) / 2
            y1 = (a['top'] + anchors[i + 1]['top']) / 2 if i + 1 < len(anchors) else footer_y
            cluster = [w for w in body if y0 <= w['top'] < y1 and w is not a]

            per_baris = {}
            for w in cluster:
                per_baris.setdefault(round(w['top'], 1), []).append(w)

            keterangan, tanggal, waktu = [], None, None
            nominal = saldo = None

            for top in sorted(per_baris):
                baris = sorted(per_baris[top], key=lambda w: w['x0'])
                teks_ket, teks_tgl = [], []

                for w in baris:
                    # Angka dikenali lewat pola DAN tepi kanannya. Nominal
                    # panjang menjorok ke wilayah keterangan, jadi x0 tidak
                    # bisa dipakai untuk memisahkannya.
                    if AMOUNT_RE.match(w['text']) and w['x1'] > cols['amount_min_x1']:
                        nilai = self._parse_amount(w['text'])
                        if nilai is None:
                            continue
                        if w['x1'] <= cols['nominal_max']:
                            nominal = nilai if nominal is None else nominal
                        else:
                            saldo = nilai if saldo is None else saldo
                    elif w['x0'] >= cols['ket_x0'] - 8:
                        teks_ket.append(w['text'])
                    elif w['x0'] >= cols['tanggal_x0'] - 5:
                        teks_tgl.append(w['text'])

                gabung_tgl = ' '.join(teks_tgl)
                m = re.match(r'^(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})$', gabung_tgl)
                if m and tanggal is None and m.group(2) in BULAN_EN:
                    tanggal = date(int(m.group(3)), BULAN_EN[m.group(2)], int(m.group(1)))
                elif waktu is None:
                    m = re.match(r'^(\d{2}:\d{2}:\d{2})', gabung_tgl)
                    if m:
                        waktu = m.group(1)

                if teks_ket:
                    keterangan.append(' '.join(teks_ket))

            rows.append({
                'no': int(a['text']),
                'date': tanggal,
                'time': waktu,
                'lines': keterangan,
                'nominal': nominal,
                'balance': saldo,
            })
        return rows

    # ------------------------------------------------------------------ #
    #  PEMBACAAN SELURUH DOKUMEN                                         #
    # ------------------------------------------------------------------ #

    def _parse_document(self) -> dict:
        """
        Baca PDF sekali, kembalikan periode + transaksi + identitas.

        Hasilnya di-cache supaya extract_saldo() dan extract_transaksi()
        tidak membuka PDF dua kali.
        """
        if self._cache is not None:
            return self._cache

        periods = []
        rows = []
        meta = {'no_rekening': 'unknown', 'nama_pemilik': '-',
                'jenis_rekening': '-', 'cabang': '-'}

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''

                # Blok ringkasan menandai awal satu periode laporan. Satu PDF
                # e-Statement biasanya memuat beberapa bulan yang digabung,
                # masing-masing dengan blok ringkasannya sendiri.
                if 'Saldo Awal' in text:
                    per = self._parse_summary(text)
                    if per:
                        per['page'] = page.page_number
                        periods.append(per)
                    if meta['no_rekening'] == 'unknown':
                        meta.update(self._parse_identity(text))

                for urut, r in enumerate(self._page_rows(page)):
                    r['page'] = page.page_number
                    r['urut'] = urut
                    r['period_idx'] = len(periods) - 1
                    rows.append(r)

        # Tanggal dicetak sekali per transaksi; kalau satu baris gagal
        # terbaca tanggalnya, warisi dari baris sebelumnya agar transaksinya
        # tidak hilang dari laporan — dan catat sebagai peringatan.
        terakhir = None
        for r in rows:
            if r['date'] is None:
                r['date'] = terakhir
                if terakhir is not None:
                    self.warnings.append(
                        f"Halaman {r['page']} baris No. {r['no']}: tanggal tidak "
                        f"terbaca, memakai tanggal transaksi sebelumnya "
                        f"({terakhir.isoformat()})."
                    )
            else:
                terakhir = r['date']

        self._cache = {'periods': periods, 'rows': rows, 'meta': meta}
        return self._cache

    def _merged_rows(self) -> list:
        """
        Baris gabungan untuk pelaporan, tanpa duplikat antar blok laporan.

        Satu PDF bisa memuat beberapa laporan yang rentangnya beririsan
        (mis. unduhan Okt-Des lalu Des-Jan), sehingga transaksi di bagian
        yang beririsan tercetak dua kali. Baris dianggap sama kalau tanggal,
        nominal, dan saldo berjalannya sama. Saldo berjalan berubah di setiap
        transaksi, jadi kunci ini praktis tidak mungkin bentrok. Duplikat
        DALAM satu blok tidak pernah dibuang — dua transaksi identik di satu
        laporan memang bisa terjadi dan justru perlu terlihat.
        """
        doc = self._parse_document()
        seen = {}
        for r in doc['rows']:
            k = (r['date'], r['nominal'], r['balance'])
            prev = seen.get(k)
            if prev is None or prev['period_idx'] == r['period_idx']:
                if prev is not None:
                    k = k + (r['page'], len(seen))
                seen[k] = r
            else:
                seen[k] = r

        # Urut MENGIKUTI CETAKAN di dalam tiap laporan. Antar laporan diurut
        # per tanggal mulai periodenya, karena e-Statement gabungan sering
        # mencetak bulan terbaru lebih dulu — kalau urutan cetak diikuti
        # mentah-mentah, laporan jadi mundur dari Desember ke Oktober.
        def kunci(r):
            per = doc['periods'][r['period_idx']] if r['period_idx'] >= 0 else None
            mulai = per['start'] if per and per.get('start') else date.min
            return (mulai, r['page'], r['urut'])

        return sorted(seen.values(), key=kunci)

    def _overlap_warning(self) -> str | None:
        """Rentang periode yang saling tumpang tindih = transaksi ganda."""
        doc = self._parse_document()
        pers = [p for p in doc['periods'] if p.get('start') and p.get('end')]
        for i in range(len(pers)):
            for j in range(i + 1, len(pers)):
                a, b = pers[i], pers[j]
                if a['start'] <= b['end'] and b['start'] <= a['end']:
                    dup = len(doc['rows']) - len(self._merged_rows())
                    return (
                        f"Periode {a['start']}..{a['end']} dan "
                        f"{b['start']}..{b['end']} saling tumpang tindih; "
                        f"{dup} transaksi ganda digabung menjadi satu."
                    )
        return None

    def _parse_summary(self, text: str) -> dict | None:
        """
        Ambil rentang periode + angka resmi dari blok ringkasan halaman awal
        tiap laporan:

            Periode/Period : 01 Nov 2025 - 30 Nov 2025
            Saldo Awal/Initial Balance : 647.634,48
            Dana Masuk/Incoming Transactions : + 535.012.558,39
            Dana Keluar/Outgoing Transactions : - 525.916.650,68
            Saldo Akhir/Closing Balance : 9.743.542,19

        Jumlah transaksi TIDAK dicantumkan di ringkasan, jadi n_debit/n_credit
        dikembalikan None (engine melewatkan pemeriksaan yang angkanya None).
        """
        m = re.search(
            r'Periode/Period\s*:\s*(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s*-\s*'
            r'(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})',
            text,
        )
        if not m or m.group(2) not in BULAN_EN or m.group(5) not in BULAN_EN:
            return None

        def ambil(pola):
            found = re.search(pola, text)
            return self._parse_amount(found.group(1)) if found else None

        return {
            'start': date(int(m.group(3)), BULAN_EN[m.group(2)], int(m.group(1))),
            'end':   date(int(m.group(6)), BULAN_EN[m.group(5)], int(m.group(4))),
            'opening':      ambil(r'Saldo Awal/Initial Balance\s*:\s*(-?[\d.,]+)'),
            'closing':      ambil(r'Saldo Akhir/Closing Balance\s*:\s*(-?[\d.,]+)'),
            'total_credit': ambil(r'Incoming Transactions\s*:\s*\+?\s*(-?[\d.,]+)'),
            'total_debit':  ambil(r'Outgoing Transactions\s*:\s*-?\s*([\d.,]+)'),
            'n_debit': None,
            'n_credit': None,
        }

    def _parse_identity(self, text: str) -> dict:
        """
        Ambil identitas dari blok kepala laporan.

        Jenis rekening ("Tabungan Mandiri", "Giro Rupiah IDR", ...) dicetak
        sebagai baris tersendiri tepat SEBELUM baris Saldo Awal, tanpa label
        — jadi diambil lewat posisinya, bukan lewat kata kunci.
        """
        out = {}
        m = re.search(r'Nama/Name\s*:\s*(.+?)\s+Periode/Period', text)
        if m:
            out['nama_pemilik'] = ' '.join(m.group(1).split()) or '-'
        m = re.search(r'Nomor Rekening/Account Number\s*:\s*(\d{6,})', text)
        if m:
            out['no_rekening'] = m.group(1)
        m = re.search(r'Cabang/Branch\s*:\s*(.+?)\s+Dicetak pada', text)
        if m:
            out['cabang'] = ' '.join(m.group(1).split()) or '-'

        lines = [l.strip() for l in text.split('\n')]
        for i, line in enumerate(lines):
            if line.startswith('Saldo Awal') and i > 0:
                kandidat = lines[i - 1]
                # Baris sebelumnya harus benar-benar baris jenis rekening —
                # bukan baris berlabel lain yang kebetulan ada di atasnya.
                if kandidat and ':' not in kandidat and len(kandidat) < 60:
                    out['jenis_rekening'] = kandidat
                break
        return out

    # ------------------------------------------------------------------ #
    #  KONTRAK BaseExtractor                                             #
    # ------------------------------------------------------------------ #

    def extract_no_rekening(self) -> str:
        return self._parse_document()['meta']['no_rekening']

    def extract_saldo(self) -> dict:
        doc = self._parse_document()
        rows = self._merged_rows()

        # Saldo akhir per hari = saldo pada transaksi terakhir hari itu.
        per_day = {}
        for r in rows:
            if r['date'] is not None and r['balance'] is not None:
                per_day[r['date']] = r['balance']

        # Saldo awal berlaku pada hari pertama periodenya.
        opening_on = {}
        for p in doc['periods']:
            if p.get('start') and p.get('opening') is not None:
                opening_on.setdefault(p['start'], p['opening'])

        # Hari yang benar-benar dicakup laporan (gabungan semua periode).
        covered = set()
        for p in doc['periods']:
            if not (p.get('start') and p.get('end')):
                continue
            d = p['start']
            while d <= p['end']:
                covered.add(d)
                d += timedelta(days=1)
        if not covered:
            return {'_nama_pemilik': doc['meta']['nama_pemilik'],
                    '_no_rekening': doc['meta']['no_rekening'],
                    '_jenis_rekening': doc['meta']['jenis_rekening']}

        buckets = {}
        awal_bulan = {}
        prev = None
        for d in sorted(covered):
            if d in opening_on and prev is None:
                prev = opening_on[d]
            # Saldo awal sebuah bulan = saldo sebelum transaksi hari pertama:
            # dari Saldo Awal laporan kalau periode mulai di sini, selain itu
            # saldo akhir hari terakhir bulan sebelumnya.
            if (d.year, d.month) not in awal_bulan:
                awal_bulan[(d.year, d.month)] = opening_on.get(d, prev)
            if d in per_day:
                prev = per_day[d]
            buckets.setdefault((d.year, d.month), []).append(
                {'Bulan': BULAN_ORDER[d.month - 1], 'Tanggal': d.day,
                 'Saldo Akhir Harian': prev}
            )

        result = {}
        for (year, month), data in buckets.items():
            bulan_id = BULAN_ORDER[month - 1]
            result[bulan_id] = {'df': pd.DataFrame(data), 'tahun': str(year)}
            saldo_awal = awal_bulan.get((year, month))
            if saldo_awal is not None:
                result[f'_saldo_awal_{bulan_id}'] = saldo_awal

        result['_nama_pemilik'] = doc['meta']['nama_pemilik']
        result['_no_rekening'] = doc['meta']['no_rekening']
        result['_jenis_rekening'] = doc['meta']['jenis_rekening']

        # Laporkan hasil checksum dalam bentuk umum supaya engine bisa
        # menampilkannya sebagai indikator tanpa tahu format e-Statement.
        lap = self.validate()
        result['_checksum'] = [
            {'label': per['label'], 'bulan': per['bulan'],
             'expected': {k: per['expected'].get(k) for k in
                          ('n_debit', 'n_credit', 'total_debit', 'total_credit', 'closing')},
             'actual': per['actual']}
            for per in lap['periods']
        ]
        return result

    def extract_transaksi(self) -> dict:
        buckets = {}

        for r in self._merged_rows():
            if r['date'] is None or not r['nominal']:
                continue
            bulan_id = BULAN_ORDER[r['date'].month - 1]
            jenis = 'Kredit' if r['nominal'] > 0 else 'Debit'

            keterangan = ' '.join(' '.join(r['lines']).split())
            buckets.setdefault(bulan_id, []).append({
                'Bulan': bulan_id,
                'Tanggal': r['date'].day,
                'Jenis Mutasi': jenis,
                'Mutasi': abs(r['nominal']),
                'Nama Pengirim/Penerima': self._extract_nama(r['lines']),
                'Keterangan Transaksi': keterangan,
            })

        return {b: pd.DataFrame(v) for b, v in buckets.items() if v}

    # ------------------------------------------------------------------ #
    #  VALIDASI OTOMATIS (CHECKSUM)                                      #
    # ------------------------------------------------------------------ #

    def validate(self) -> dict:
        """
        Cocokkan hasil parsing dengan angka resmi yang tercetak di tiap blok
        ringkasan PDF: Dana Masuk, Dana Keluar, Saldo Awal, dan Saldo Akhir.

        Selain itu ada dua pemeriksaan yang tidak butuh angka ringkasan:
          - Nomor urut "No" harus berjalan 1..N tanpa lompatan. Kalau ada
            yang hilang, berarti ada baris/halaman yang tidak terbaca (atau
            memang dibuang dari dokumen).
          - Rantai saldo berjalan: saldo tiap baris harus sama dengan saldo
            sebelumnya + nominal bertanda.

        Mengembalikan {'ok': bool, 'periods': [...], 'warnings': [...]}.
        """
        doc = self._parse_document()
        report = {'ok': True, 'periods': [], 'warnings': list(self.warnings)}

        if not doc['periods']:
            report['ok'] = False
            report['warnings'].append(
                'Tidak ada blok ringkasan periode yang terbaca — '
                'PDF kemungkinan bukan format e-Statement Mandiri.'
            )
            return report

        if not doc['rows']:
            report['ok'] = False
            report['warnings'].append(
                'Blok ringkasan terbaca tetapi tidak ada baris transaksi yang terdeteksi.'
            )

        report['duplikat_digabung'] = len(doc['rows']) - len(self._merged_rows())
        overlap = self._overlap_warning()
        if overlap:
            report['warnings'].append(overlap)

        for idx, per in enumerate(doc['periods']):
            rows = [r for r in doc['rows'] if r['period_idx'] == idx]
            got = {
                'n_debit':  sum(1 for r in rows if (r['nominal'] or 0) < 0),
                'n_credit': sum(1 for r in rows if (r['nominal'] or 0) > 0),
                'total_debit':  -sum(r['nominal'] for r in rows if (r['nominal'] or 0) < 0),
                'total_credit':  sum(r['nominal'] for r in rows if (r['nominal'] or 0) > 0),
            }
            saldo_terakhir = [r['balance'] for r in rows if r['balance'] is not None]
            got['closing'] = saldo_terakhir[-1] if saldo_terakhir else None

            label = f"{per['start']}..{per['end']}"

            checks = {}
            for key, expected in (
                ('total_debit', per['total_debit']),
                ('total_credit', per['total_credit']),
                ('closing', per['closing']),
            ):
                actual = got[key]
                if expected is None:
                    checks[key] = None          # angka resmi tidak terbaca
                    continue
                ok = (actual is not None
                      and abs(round(actual, 2) - round(expected, 2)) < 0.005)
                checks[key] = ok
                if not ok:
                    report['ok'] = False
                    report['warnings'].append(
                        f"Periode {label}: {key} hasil parsing {actual} "
                        f"!= angka resmi {expected}"
                    )

            # Nomor urut transaksi yang dicetak bank — alat deteksi baris
            # hilang yang tidak tergantung angka ringkasan.
            nomor = [r['no'] for r in rows]
            if nomor:
                hilang = sorted(set(range(1, max(nomor) + 1)) - set(nomor))
                checks['nomor_urut'] = (not hilang and nomor[0] == 1)
                if hilang:
                    report['ok'] = False
                    report['warnings'].append(
                        f"Periode {label}: nomor transaksi {self._ringkas(hilang)} "
                        f"tidak ditemukan — ada baris/halaman yang tidak terbaca "
                        f"atau hilang dari dokumen."
                    )
                elif nomor[0] != 1:
                    report['ok'] = False
                    report['warnings'].append(
                        f"Periode {label}: transaksi tidak dimulai dari nomor 1 "
                        f"(mulai dari {nomor[0]}) — halaman awal laporan kemungkinan hilang."
                    )

            if per['opening'] is None:
                report['warnings'].append(
                    f"Periode {label}: Saldo Awal tidak terbaca dari PDF."
                )
            else:
                # Rantai saldo berjalan: pemeriksaan bebas yang menangkap
                # baris terlewat atau nominal salah baca, dan hanya berlaku
                # DI DALAM satu blok laporan.
                prev = per['opening']
                putus = 0
                for r in rows:
                    if r['balance'] is None or r['nominal'] is None:
                        continue
                    if abs(prev + r['nominal'] - r['balance']) > 0.005:
                        putus += 1
                    prev = r['balance']
                checks['rantai_saldo'] = (putus == 0)
                if putus:
                    report['ok'] = False
                    report['warnings'].append(
                        f"Periode {label}: rantai saldo berjalan putus di "
                        f"{putus} baris — ada transaksi terlewat atau salah baca."
                    )

            report['periods'].append({
                'label': label,
                'bulan': BULAN_ORDER[per['start'].month - 1],
                'tahun': per['start'].year,
                'expected': per,
                'actual': got,
                'checks': checks,
            })

        return report

    # ------------------------------------------------------------------ #
    #  HELPER                                                            #
    # ------------------------------------------------------------------ #

    def _parse_amount(self, s: str):
        """Nominal gaya Indonesia bertanda: '+1.234.567,89' -> 1234567.89."""
        s = (s or '').strip()
        if not s or s == '-':
            return None
        negatif = s.startswith('-')
        s = s.lstrip('+-').strip()
        if not re.match(r'^\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?$|^\d+(?:,\d{1,2})?$', s):
            return None
        try:
            nilai = float(s.replace('.', '').replace(',', '.'))
        except ValueError:
            return None
        return -nilai if negatif else nilai

    def _ringkas(self, angka: list, maks: int = 10) -> str:
        """Ringkas daftar nomor supaya pesan peringatan tidak meledak panjangnya."""
        if len(angka) <= maks:
            return ', '.join(str(a) for a in angka)
        return ', '.join(str(a) for a in angka[:maks]) + f", ... ({len(angka)} nomor)"

    # -- Ekstraksi nama ------------------------------------------------- #

    # Nomor rekening/HP/VA di ekor baris nama: "AFIF HARIYANTO 362201000051539"
    _TAIL_ACCT_RE = re.compile(r'\s+\d{6,}\s*$')
    # Arah transfer yang mengawali baris: "Ke BRI", "DARI CAHAYA NABATI"
    _LEAD_ARAH_RE = re.compile(r'^(?:ke|dari)\s+', re.IGNORECASE)

    def _bersih_nama(self, s: str) -> str:
        s = ' '.join((s or '').split())
        s = self._TAIL_ACCT_RE.sub('', s)
        s = s.strip(' .,-/')
        return ' '.join(s.split())

    def _buang_prefix_bank(self, s: str) -> str:
        """
        Buang nama bank yang mendahului nama lawan transaksi.

        Bank tujuan sudah tercatat di baris keterangannya sendiri; kalau ikut
        terbawa ke kolom nama, orang yang sama akan terpecah jadi beberapa
        grup di Rekap Kredit/Debit ("BRI ASNAH" vs "ASNAH"). Pembuangan
        berhenti begitu ketemu token yang bukan nama bank, dan tidak pernah
        menghabiskan seluruh baris — kalau semua tokennya kebetulan nama bank,
        baris itu dikembalikan apa adanya.
        """
        tokens = s.split()
        i = 0
        while i < len(tokens) and tokens[i].upper().strip('.,') in BANK_PREFIX:
            i += 1
        sisa = ' '.join(tokens[i:])
        return sisa if sisa else s

    def _nama_dari_baris(self, s: str) -> str:
        """Baris "<NAMA> <nomor rekening>" -> nama saja."""
        s = self._LEAD_ARAH_RE.sub('', ' '.join((s or '').split()))
        return self._bersih_nama(self._buang_prefix_bank(s))

    def _extract_nama(self, lines: list) -> str:
        """
        Tentukan nama lawan transaksi (atau label kategori kalau transaksinya
        memang tidak punya lawan, mis. bunga/pajak/biaya).

        Memanfaatkan STRUKTUR BARIS, bukan mencocokkan satu string panjang:
        pada e-Statement, baris nama lawan transaksi selalu dicetak sebagai
        baris tersendiri setelah baris jenis transaksi.
        """
        lines = [' '.join(l.split()) for l in (lines or []) if l and l.strip()]
        if not lines:
            return '-'

        head = lines[0]
        low = head.lower()

        # 1. BI Fast: ['Transfer BI Fast', 'Ke BRI', '<NAMA> <norek>', '<berita>']
        #    Baris arah ("Ke BRI"/"Dari") memuat bank lawan, BUKAN namanya —
        #    nama orangnya selalu di baris SETELAH baris arah itu. Kalau baris
        #    arah ikut dianggap kandidat nama, seluruh transfer BI Fast akan
        #    tercatat atas nama banknya dan Rekap Kredit/Debit jadi tidak ada
        #    gunanya.
        if low.startswith('transfer bi fast'):
            for i, baris in enumerate(lines[1:], start=1):
                if not self._LEAD_ARAH_RE.match(baris):
                    continue
                if i + 1 < len(lines):
                    kandidat = self._nama_dari_baris(lines[i + 1])
                    if kandidat and re.search(r'[A-Za-z]{2,}', kandidat):
                        return kandidat
                # Nama tidak tercetak — pakai bank tujuannya sebagai label.
                bank = self._bersih_nama(self._LEAD_ARAH_RE.sub('', baris))
                return f'Transfer BI Fast {bank}'.strip() if bank else 'Transfer BI Fast'
            for baris in lines[1:]:
                kandidat = self._nama_dari_baris(baris)
                if kandidat and re.search(r'[A-Za-z]{2,}', kandidat):
                    return kandidat
            return 'Transfer BI Fast'

        # 2. Biaya yang menyertai transaksi lain — diperiksa SEBELUM pola
        #    transfer, karena barisnya berbunyi "Biaya transfer ..." dan akan
        #    tertangkap pola transfer kalau urutannya dibalik.
        if low.startswith('biaya'):
            return self._label_biaya(head)

        # 3. Transfer sesama Mandiri / antar bank:
        #    ['Transfer ke BANK MANDIRI', '<NAMA> <norek>']
        #    ['Transfer antar Mandiri', 'DARI <NAMA>', '<berita>', 'Transfer Fee <ref>']
        if re.match(r'^transfer\s+(ke|dari|antar)\b', low):
            for baris in lines[1:]:
                if re.match(r'^transfer\s+fee\b', baris, re.IGNORECASE):
                    continue
                kandidat = self._nama_dari_baris(baris)
                if kandidat and not kandidat.isdigit():
                    return kandidat
            return head

        # 4. Tunai.
        if low.startswith('penarikan tunai'):
            return 'Tarik Tunai'
        if low.startswith('penyetoran tunai') or low.startswith('setoran tunai'):
            return 'Setor Tunai'

        # 5. Pembayaran tagihan/merchant: nama biller ada di baris pertama
        #    ("Pembayaran PLN Prabayar"), nomor pelanggan di baris berikutnya.
        m = re.match(r'^pembayaran\s+(.+)$', low)
        if m:
            sisa = head[len('Pembayaran'):].strip()
            if sisa.lower().startswith('qr'):
                # ['Pembayaran QR', 'ke <MERCHANT>', '<ref>'] — nama merchant
                # ada di baris berikutnya, bukan di baris pertama.
                for baris in lines[1:]:
                    kandidat = self._bersih_nama(self._LEAD_ARAH_RE.sub('', baris))
                    if kandidat and not kandidat.isdigit():
                        return kandidat
                return 'Pembayaran QR'
            return self._bersih_nama(sisa) or 'Pembayaran Tagihan'

        # 6. Kategori tetap yang memang tidak punya lawan transaksi.
        if low.startswith('bunga rekening'):
            return 'Bunga'
        if low.startswith('pajak rekening'):
            return 'Pajak'
        if low.startswith('top-up') or low.startswith('top up'):
            return self._bersih_nama(head)
        if low.startswith('pendebitan otomatis'):
            return 'Pendebitan Otomatis'

        # 7. Transaksi EDC/merchant: nama merchant ada di baris kedua.
        if low.startswith('transaksi di'):
            for baris in lines[1:]:
                kandidat = self._bersih_nama(baris)
                if kandidat and not kandidat.isdigit():
                    return kandidat
            return self._bersih_nama(head)

        # 8. Branchless/agen: baris tengahnya kode acak, keterangan aslinya
        #    ada di baris terakhir.
        if low.startswith('branchless'):
            for baris in reversed(lines[1:]):
                kandidat = self._bersih_nama(baris)
                if kandidat and not re.match(r'^[0-9a-f-]+$', kandidat, re.IGNORECASE):
                    return kandidat
            return self._bersih_nama(head)

        # 9. Fallback: baris yang paling mungkin memuat nama — baris berisi
        #    huruf yang bukan sekadar nomor referensi.
        for baris in lines:
            kandidat = self._nama_dari_baris(baris)
            if kandidat and re.search(r'[A-Za-z]{3,}', kandidat):
                return kandidat
        return self._bersih_nama(head) or '-'

    def _label_biaya(self, head: str) -> str:
        """Seragamkan label baris biaya supaya rekapnya tidak terpecah-pecah."""
        low = head.lower()
        if 'bi fast' in low:
            return 'Biaya Transfer BI Fast'
        if 'transfer' in low:
            return 'Biaya Transfer'
        if 'administrasi' in low:
            return ('Biaya Administrasi Kartu Debit' if 'kartu' in low
                    else 'Biaya Administrasi')
        if low.startswith('biaya transaksi bank'):
            return 'Biaya Transaksi Bank'
        if low.startswith('biaya pembayaran'):
            return 'Biaya Pembayaran Tagihan'
        return self._bersih_nama(head) or 'Biaya Bank'
