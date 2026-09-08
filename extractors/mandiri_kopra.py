"""
mandiri_kopra.py — Extractor rekening koran Bank Mandiri format "Kopra by Mandiri".

Pendekatan parsing:
  1. Posisi kolom dideteksi DINAMIS dari baris header tiap halaman
     (Remark / Reference No. / Debit / Credit / Balance). Posisi Y header
     berbeda-beda antar halaman (ada blok ringkasan di halaman pertama dan
     di setiap awal periode), jadi tidak bisa diasumsikan tetap.
  2. Tiga kolom angka (Debit/Credit/Balance) rata-kanan, sehingga yang dipakai
     sebagai patokan adalah x1 — bukan x0. Angka yang makin panjang menggeser
     x0 ke kiri, dan itulah penyebab saldo >= 1 miliar dulu terbaca 0.
  3. Satu transaksi bisa memakan beberapa baris teks. Tanggal dicetak di
     tengah blok, jadi batas antar-transaksi diambil di titik tengah antar
     anchor tanggal.
  4. Nama pengirim/penerima diekstrak lewat pipeline berurutan per pola,
     dari yang paling spesifik ke paling umum.

Extractor ini hanya menghasilkan data mentah sesuai kontrak BaseExtractor —
tidak tahu apa pun soal Excel/styling.
"""

import re
import calendar
from datetime import date, timedelta
import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor
from extractors.peringatan import PencatatPeringatan, pencatat_laporan
from extractors.mandiri_nama import extract_nama

BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]

# Singkatan bulan Inggris (dipakai PDF Kopra) -> nomor bulan
BULAN_EN = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}

# Nominal Kopra selalu berformat 1.234.567,89 gaya Inggris: "1,234,567.89"
AMOUNT_RE = re.compile(r'^-?[\d,]+\.\d{2}$')

# Posisi kolom hasil pengukuran PDF referensi (halaman A4 lebar 595pt).
# Hanya dipakai sebagai cadangan kalau baris header tidak ditemukan.
FALLBACK_COLS = {
    'remark_x0': 120.0,
    'ref_x0': 240.0,
    'ref_x1': 294.0,
    'debit_x1': 382.0,
    'credit_x1': 472.0,
    'balance_x1': 564.0,
}


# Ketentuan Bank Mandiri: biaya administrasi rekening didebet pada HARI
# TERAKHIR bulan berjalan. Dikonfirmasi dari data riil — 8/8 kejadian pada
# PDF Kopra dan 11/11 pada PDF e-Statement, lintas Giro & seluruh jenis
# Tabungan. Berlaku untuk kedua format, jadi ditaruh di satu tempat dan
# dipakai bersama oleh mandiri_kopra.py dan mandiri_statement.py.
#
# Catatan: "Biaya administrasi kartu debit" pada e-Statement SENGAJA tidak
# ikut diperiksa — itu biaya kartu yang mengikuti tanggal ulang tahun kartu,
# bukan biaya rekening, dan jadwalnya beda per nasabah. Karena label_nama
# dicocokkan PERSIS SAMA, baris itu otomatis tidak ikut.
LABEL_BIAYA_ADMIN = 'Biaya Administrasi'

# Baris bunga & pajak bunga dicocokkan lewat kolom Nama, bukan Keterangan:
# keterangan Kopra selalu berekor kode cabang ("Bunga 12001"), sedangkan
# nama sudah dinormalkan extractor.
BUNGA_PAJAK_MANDIRI = {
    'kolom': 'Nama Pengirim/Penerima',
    'bunga': ['Bunga'],
    'pajak': ['Pajak'],
}


def jadwal_biaya_admin_mandiri(saldo_result: dict) -> dict:
    """
    Bangun metadata '_biaya_admin' dari bulan-bulan yang ada di hasil saldo.

    Dipakai kedua extractor Mandiri. Bulan yang tahunnya tidak terbaca
    dilewati — engine lebih baik tidak memeriksa daripada memeriksa dengan
    tanggal tebakan.
    """
    jadwal = {}
    for bulan, info in saldo_result.items():
        if bulan.startswith('_') or not isinstance(info, dict):
            continue
        bulan_num = BULAN_ORDER.index(bulan) + 1 if bulan in BULAN_ORDER else None
        try:
            tahun = int(info.get('tahun'))
        except (TypeError, ValueError):
            continue
        if not bulan_num:
            continue
        try:
            _, ndays = calendar.monthrange(tahun, bulan_num)
        except (calendar.IllegalMonthError, ValueError):
            continue
        jadwal[bulan] = {
            'tanggal': ndays,
            'aturan': f'akhir bulan/tanggal {ndays} (ketentuan Bank Mandiri)',
        }
    if not jadwal:
        return {}
    return {'_biaya_admin': {'label_nama': LABEL_BIAYA_ADMIN, 'jadwal': jadwal}}


class MandiriKopraExtractor(PencatatPeringatan, BaseExtractor):

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Peringatan yang terkumpul selama parsing (dibaca app.py / pemanggil).
        self.warnings: list[str] = []
        # Bentuk terstruktur dari peringatan yang sama, untuk metadata
        # '_peringatan' (lihat extractors/peringatan.py).
        self.peringatan: list[dict] = []
        self._cache = None

    def get_file_prefix(self) -> str:
        return 'MANDIRI'

    # ------------------------------------------------------------------ #
    #  DETEKSI KOLOM DINAMIS                                             #
    # ------------------------------------------------------------------ #

    def _find_columns(self, page, words: list) -> dict | None:
        """
        Cari baris header tabel di halaman ini dan turunkan geometri kolom.

        Mengembalikan None kalau halaman tidak punya tabel transaksi
        (mis. halaman lampiran). Kolom kiri-rata memakai x0, kolom angka
        yang rata-kanan memakai x1.
        """
        anchor = None
        for w in words:
            if w['text'] == 'Remark' and 110 < w['x0'] < 130:
                anchor = w
                break
        if anchor is None:
            return None

        # Kata lain pada baris header yang sama (toleransi 2pt).
        same_row = [w for w in words if abs(w['top'] - anchor['top']) < 2]
        pos = {w['text']: w for w in same_row}

        def x0_of(name, key):
            return pos[name]['x0'] if name in pos else FALLBACK_COLS[key]

        def x1_of(name, key):
            return pos[name]['x1'] if name in pos else FALLBACK_COLS[key]

        missing = [n for n in ('Reference', 'Debit', 'Credit', 'Balance') if n not in pos]
        if missing:
            self._catat(
                'Rendah',
                'Sebagian kolom header tabel tidak ditemukan, posisi kolom '
                'memakai nilai cadangan',
                f"Halaman {page.page_number}: {', '.join(missing)}.",
                halaman=str(page.page_number),
            )

        cols = {
            'header_y':   anchor['top'],
            'remark_x0':  anchor['x0'],
            'ref_x0':     x0_of('Reference', 'ref_x0'),
            'ref_x1':     x1_of('No.', 'ref_x1') if 'No.' in pos else FALLBACK_COLS['ref_x1'],
            'debit_x1':   x1_of('Debit', 'debit_x1'),
            'credit_x1':  x1_of('Credit', 'credit_x1'),
            'balance_x1': x1_of('Balance', 'balance_x1'),
        }
        # Ambang antar-kolom angka = titik tengah antar tepi kanan header.
        cols['debit_max']  = (cols['debit_x1'] + cols['credit_x1']) / 2
        cols['credit_max'] = (cols['credit_x1'] + cols['balance_x1']) / 2
        # Batas kiri wilayah angka: sedikit di kanan kolom Reference No.
        cols['amount_min_x1'] = cols['ref_x1'] + 6
        return cols

    # ------------------------------------------------------------------ #
    #  PENGELOMPOKAN BARIS                                               #
    # ------------------------------------------------------------------ #

    def _page_rows(self, page) -> list:
        """
        Kembalikan transaksi mentah pada satu halaman.

        Bidang tabel dimulai DI BAWAH baris header, sehingga baris periode
        ("01 Jun 2025 - 30 Jun 2025 IDR ...") di blok ringkasan tidak ikut
        terbaca sebagai transaksi hantu tanggal 1.
        """
        words = page.extract_words()
        if not words:
            return []

        cols = self._find_columns(page, words)
        if cols is None:
            return []

        # Batas bawah: di atas footer halaman.
        footer_y = float(page.height)
        for w in words:
            if 'koprabymandiri.com' in w['text'] or w['text'] == 'Page':
                if w['top'] > cols['header_y']:
                    footer_y = min(footer_y, w['top'])

        body = [w for w in words
                if cols['header_y'] + 5 < w['top'] < footer_y]
        if not body:
            return []

        ordered = sorted(body, key=lambda w: (round(w['top'], 1), w['x0']))

        # Anchor tanggal: "DD Mon YYYY," di kolom Posting Date.
        anchors = []
        for i, w in enumerate(ordered):
            if w['x0'] >= cols['remark_x0'] - 20 or not re.match(r'^\d{1,2}$', w['text']):
                continue
            if i + 2 >= len(ordered):
                continue
            mon, yr = ordered[i + 1], ordered[i + 2]
            if mon['text'][:3] not in BULAN_EN:
                continue
            if not re.match(r'^\d{4},$', yr['text']):
                continue
            anchors.append({
                'y': w['top'],
                'day': int(w['text']),
                'month': BULAN_EN[mon['text'][:3]],
                'year': int(yr['text'][:4]),
            })

        rows = []
        for i, a in enumerate(anchors):
            # Batas klaster = titik tengah antar anchor, karena tanggal
            # dicetak di tengah blok remark yang bisa beberapa baris.
            y0 = cols['header_y'] + 5 if i == 0 else (anchors[i - 1]['y'] + a['y']) / 2
            y1 = (a['y'] + anchors[i + 1]['y']) / 2 if i + 1 < len(anchors) else footer_y
            cluster = [w for w in body if y0 <= w['top'] < y1]

            remark_w, ref_w = [], []
            debit = credit = balance = None

            for w in sorted(cluster, key=lambda x: (round(x['top'], 1), x['x0'])):
                # Angka dikenali lewat pola DAN posisi — nomor referensi Kopra
                # panjang tapi tidak pernah berdesimal, sedangkan remark
                # sesekali memuat token berformat angka.
                if AMOUNT_RE.match(w['text']) and w['x1'] > cols['amount_min_x1']:
                    val = self._parse_amount(w['text'])
                    if val is None:
                        continue
                    if w['x1'] <= cols['debit_max']:
                        debit = val if debit is None else debit
                    elif w['x1'] <= cols['credit_max']:
                        credit = val if credit is None else credit
                    else:
                        balance = val if balance is None else balance
                elif w['x0'] >= cols['ref_x0'] - 5:
                    ref_w.append(w)
                elif w['x0'] >= cols['remark_x0'] - 5:
                    remark_w.append(w)

            rows.append({
                'day': a['day'],
                'month': a['month'],
                'year': a['year'],
                'remark': ' '.join(w['text'] for w in remark_w).strip(),
                'reference': ' '.join(w['text'] for w in ref_w).strip(),
                'debit': debit or 0,
                'credit': credit or 0,
                'balance': balance,
            })
        return rows

    # ------------------------------------------------------------------ #
    #  PEMBACAAN SELURUH DOKUMEN                                         #
    # ------------------------------------------------------------------ #

    def _parse_document(self) -> dict:
        """
        Baca PDF sekali, kembalikan periode + transaksi + ringkasan resmi.

        Hasilnya di-cache supaya extract_saldo() dan extract_transaksi()
        tidak membuka PDF dua kali.
        """
        if self._cache is not None:
            return self._cache

        periods = []   # {'month','year','opening','closing','n_debit',...}
        rows = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''

                # Blok ringkasan menandai awal satu periode laporan. Satu PDF
                # bisa memuat beberapa laporan yang digabung, dan rentangnya
                # bisa saling tumpang tindih.
                if 'Account Statement Summary' in text:
                    per = self._parse_period(text)
                    if per:
                        per.update(self._parse_summary(text))
                        per['page'] = page.page_number
                        periods.append(per)

                # Baris dimiliki oleh blok ringkasan terakhir sebelum halaman
                # ini — bukan ditebak dari bulannya.
                for urut, r in enumerate(self._page_rows(page)):
                    r['page'] = page.page_number
                    r['urut'] = urut          # posisi baris di halaman
                    r['period_idx'] = len(periods) - 1
                    r['date'] = date(r['year'], r['month'], r['day'])
                    rows.append(r)

            meta = self._parse_identity(pdf)

        self._cache = {'periods': periods, 'rows': rows, 'meta': meta}
        return self._cache

    def _merged_rows(self) -> list:
        """
        Baris gabungan untuk pelaporan, tanpa duplikat antar blok laporan.

        PDF gabungan sering memuat dua laporan yang rentangnya beririsan,
        sehingga transaksi di bagian yang beririsan tercetak dua kali. Baris
        dianggap sama kalau tanggal, nominal, dan saldo berjalannya sama.
        Remark sengaja TIDAK ikut jadi kunci: transaksi yang sama bisa
        tercetak dengan pembungkusan baris berbeda di dua laporan. Saldo
        berjalan berubah di setiap transaksi, jadi kunci ini praktis tidak
        mungkin bentrok. Duplikat DALAM satu blok tidak pernah dibuang.
        """
        doc = self._parse_document()
        seen = {}
        for r in doc['rows']:
            k = (r['date'], r['debit'], r['credit'], r['balance'])
            prev = seen.get(k)
            if prev is None or prev['period_idx'] == r['period_idx']:
                # Blok yang sama: simpan keduanya lewat kunci berbeda.
                if prev is not None:
                    k = k + (r['page'], len(seen))
                seen[k] = r
            # Blok berbeda dengan isi identik: laporan yang lebih baru menang.
            else:
                seen[k] = r
        # Urut MENGIKUTI CETAKAN (blok laporan, halaman, posisi baris), bukan
        # per tanggal. Mengurutkan ulang per tanggal akan menyembunyikan
        # anomali urutan tanggal — justru salah satu hal yang diperiksa.
        return sorted(seen.values(),
                      key=lambda r: (r['period_idx'], r['page'], r['urut']))

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

    def _parse_period(self, text: str) -> dict | None:
        """
        Ambil rentang periode laporan.

        Satu blok ringkasan bisa menjangkau beberapa bulan sekaligus
        ("01 Nov 2025 - 23 Feb 2026"), jadi yang disimpan rentang tanggalnya
        — bukan hanya bulan awal.
        """
        m = re.search(
            r'(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})',
            text,
        )
        if not m or m.group(2) not in BULAN_EN or m.group(5) not in BULAN_EN:
            return None
        return {
            'start': date(int(m.group(3)), BULAN_EN[m.group(2)], int(m.group(1))),
            'end':   date(int(m.group(6)), BULAN_EN[m.group(5)], int(m.group(4))),
        }

    def _parse_summary(self, text: str) -> dict:
        """
        Ambil angka resmi dari blok ringkasan.

        Label dan angkanya ada di baris berbeda:
            Opening Balance No. of Debit Total Amount Debited
            547,883,734.03 46 671,667,312.75
        """
        out = {'opening': None, 'closing': None, 'n_debit': None,
               'n_credit': None, 'total_debit': None, 'total_credit': None}
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if i + 1 >= len(lines):
                continue
            nxt = lines[i + 1].strip()
            if 'Opening Balance' in line and 'No. of Debit' in line:
                m = re.match(r'^([\d,]+\.\d{2})\s+(\d+)\s+([\d,]+\.\d{2})', nxt)
                if m:
                    out['opening'] = self._parse_amount(m.group(1))
                    out['n_debit'] = int(m.group(2))
                    out['total_debit'] = self._parse_amount(m.group(3))
            elif 'Closing Balance' in line and 'No. of Credit' in line:
                m = re.match(r'^([\d,]+\.\d{2})\s+(\d+)\s+([\d,]+\.\d{2})', nxt)
                if m:
                    out['closing'] = self._parse_amount(m.group(1))
                    out['n_credit'] = int(m.group(2))
                    out['total_credit'] = self._parse_amount(m.group(3))
        return out

    def _parse_identity(self, pdf) -> dict:
        """
        Ambil nomor rekening & nama pemilik.

        Nilainya ada di baris SETELAH header "Account No. Account Name Alias":
            1200010763543 UMRINDO MANDIRI SEJA UMRINDO MANDIRI SEJA
        Nama dan alias sering identik, jadi bagian yang berulang dibuang.
        """
        out = {'no_rekening': 'unknown', 'nama_pemilik': '-'}
        for page in pdf.pages[:3]:
            lines = (page.extract_text() or '').split('\n')
            for i, line in enumerate(lines):
                if 'Account No.' not in line or i + 1 >= len(lines):
                    continue
                m = re.match(r'^(\d{10,16})\s+(.+)$', lines[i + 1].strip())
                if not m:
                    continue
                out['no_rekening'] = m.group(1)
                rest = ' '.join(m.group(2).split())
                # Buang alias yang mengulang nama (persis setengah + setengah).
                words = rest.split()
                half = len(words) // 2
                if half and words[:half] == words[half:]:
                    rest = ' '.join(words[:half])
                out['nama_pemilik'] = rest.strip() or '-'
                return out
        return out

    # ------------------------------------------------------------------ #
    #  KONTRAK BaseExtractor                                             #
    # ------------------------------------------------------------------ #

    def extract_no_rekening(self) -> str:
        return self._parse_document()['meta']['no_rekening']

    def extract_saldo(self) -> dict:
        doc = self._parse_document()
        rows = self._merged_rows()

        # Saldo akhir per hari; laporan yang lebih baru menang di hari yang sama.
        per_day = {}
        for r in rows:
            if r['balance'] is not None:
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
                    '_no_rekening': doc['meta']['no_rekening']}

        buckets = {}
        awal_bulan = {}     # (tahun, bulan) -> saldo awal bulan itu
        prev = None
        for d in sorted(covered):
            if d in opening_on and prev is None:
                prev = opening_on[d]
            # Saldo awal sebuah bulan = saldo sebelum transaksi hari pertama:
            # dari Opening Balance kalau periode mulai di sini, selain itu
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
        result.update(jadwal_biaya_admin_mandiri(result))
        result['_bunga_pajak'] = BUNGA_PAJAK_MANDIRI

        # Laporkan hasil checksum dalam bentuk umum supaya engine bisa
        # menampilkannya sebagai indikator tanpa tahu format Kopra.
        lap = self.validate()
        # Peringatan pembacaan dokumen diteruskan ke engine supaya muncul di
        # Sheet Indikasi Kejanggalan (lihat kontrak di extractors/base.py).
        if lap.get('peringatan'):
            result['_peringatan'] = lap['peringatan']
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
            if r['debit'] == 0 and r['credit'] == 0:
                continue
            bulan_id = BULAN_ORDER[r['month'] - 1]
            jenis = 'Debit' if r['debit'] > 0 else 'Kredit'
            nominal = r['debit'] if r['debit'] > 0 else r['credit']

            keterangan = ' '.join(r['remark'].split())
            buckets.setdefault(bulan_id, []).append({
                'Bulan': bulan_id,
                'Tanggal': r['day'],
                'Jenis Mutasi': jenis,
                'Mutasi': nominal,
                'Nama Pengirim/Penerima': self._extract_nama(keterangan),
                'Keterangan Transaksi': keterangan,
            })

        return {b: pd.DataFrame(v) for b, v in buckets.items() if v}

    # ------------------------------------------------------------------ #
    #  VALIDASI OTOMATIS (CHECKSUM)                                      #
    # ------------------------------------------------------------------ #

    def validate(self) -> dict:
        """
        Cocokkan hasil parsing dengan angka resmi yang tercetak di tiap blok
        ringkasan PDF: No. of Debit/Credit, Total Amount Debited/Credited,
        Opening Balance, dan Closing Balance.

        Pencocokan dilakukan PER BLOK LAPORAN (bukan per bulan), karena satu
        blok bisa menjangkau beberapa bulan sekaligus dan satu PDF bisa memuat
        beberapa laporan yang digabung.

        Mengembalikan {'ok': bool, 'periods': [...], 'warnings': [...]}.
        """
        doc = self._parse_document()
        report = {'ok': True, 'periods': [], 'warnings': list(self.warnings),
                  'peringatan': list(self.peringatan)}
        catat = pencatat_laporan(report)

        if not doc['periods']:
            report['ok'] = False
            catat('Tinggi',
                  'Tidak ada blok ringkasan periode yang terbaca',
                  'PDF kemungkinan bukan format Kopra by Mandiri.')
            return report

        if not doc['rows']:
            report['ok'] = False
            catat('Tinggi',
                  'Blok ringkasan terbaca tetapi tidak ada baris transaksi yang '
                  'terdeteksi',
                  'Tata letak tabelnya kemungkinan berbeda dari yang dikenali '
                  'extractor.')

        report['duplikat_digabung'] = len(doc['rows']) - len(self._merged_rows())
        overlap = self._overlap_warning()
        if overlap:
            catat('Sedang',
                  'Ada periode laporan yang saling tumpang tindih dalam satu PDF; '
                  'transaksi gandanya digabung menjadi satu',
                  overlap)

        for idx, per in enumerate(doc['periods']):
            rows = [r for r in doc['rows'] if r['period_idx'] == idx]
            got = {
                'n_debit': sum(1 for r in rows if r['debit'] > 0),
                'n_credit': sum(1 for r in rows if r['credit'] > 0),
                'total_debit': sum(r['debit'] for r in rows),
                'total_credit': sum(r['credit'] for r in rows),
            }
            last = [r['balance'] for r in rows if r['balance'] is not None]
            got['closing'] = last[-1] if last else None

            label = (f"{per['start']}..{per['end']}"
                     if per.get('start') else f"blok {idx + 1}")

            checks = {}
            for key, expected in (
                ('n_debit', per['n_debit']),
                ('n_credit', per['n_credit']),
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

            if per['opening'] is None:
                catat('Rendah',
                      'Opening Balance satu laporan tidak terbaca, rantai saldo '
                      'laporan itu tidak bisa diperiksa',
                      f'Periode {label}.',
                      bulan=(BULAN_ORDER[per['start'].month - 1]
                             if per.get('start') else '-'))
            else:
                # Rantai saldo berjalan: saldo tiap baris harus sama dengan
                # saldo sebelumnya + kredit - debit. Ini pemeriksaan bebas
                # yang menangkap baris terlewat atau nominal salah kolom,
                # dan hanya berlaku DI DALAM satu blok — antar blok bisa ada
                # celah tanggal yang sah.
                prev = per['opening']
                putus = 0
                for r in rows:
                    if r['balance'] is None:
                        continue
                    if abs(prev + r['credit'] - r['debit'] - r['balance']) > 0.005:
                        putus += 1
                    prev = r['balance']
                checks['rantai_saldo'] = (putus == 0)
                if putus:
                    # Tidak tercakup metadata '_checksum' (yang hanya membawa
                    # lima angka ringkasan), jadi tanpa dicatat di sini temuan
                    # ini tidak akan pernah sampai ke pemeriksa.
                    report['ok'] = False
                    catat('Tinggi',
                          f'Rantai saldo berjalan putus di {putus} baris — ada '
                          f'transaksi terlewat atau salah baca',
                          f'Periode {label}. Saldo tiap baris seharusnya sama '
                          f'dengan saldo baris sebelumnya + kredit − debit.',
                          bulan=(BULAN_ORDER[per['start'].month - 1]
                                 if per.get('start') else '-'))

            report['periods'].append({
                'label': label,
                'bulan': BULAN_ORDER[per['start'].month - 1] if per.get('start') else '-',
                'tahun': per['start'].year if per.get('start') else '-',
                'expected': per,
                'actual': got,
                'checks': checks,
            })

        return report

    # ------------------------------------------------------------------ #
    #  HELPER                                                            #
    # ------------------------------------------------------------------ #

    def _parse_amount(self, s: str):
        s = (s or '').strip()
        if not s or s == '-':
            return None
        try:
            return float(s.replace(',', ''))
        except ValueError:
            return None

    # -- Ekstraksi nama ------------------------------------------------- #

    def _extract_nama(self, keterangan: str) -> str:
        """
        Nama lawan transaksi dari kolom Remark.

        Pipeline-nya dipakai bersama dengan extractor Rekening Koran lewat
        extractors/mandiri_nama.py — remark kedua format berasal dari mesin
        pembukuan Mandiri yang sama, jadi polanya identik.
        """
        return extract_nama(keterangan)
