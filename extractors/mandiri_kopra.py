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
import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor

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


class MandiriKopraExtractor(BaseExtractor):

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
            self.warnings.append(
                f"Halaman {page.page_number}: kolom header {', '.join(missing)} "
                f"tidak ditemukan, memakai posisi cadangan."
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

                # Blok ringkasan menandai awal satu periode laporan.
                per = self._parse_period(text)
                if per:
                    per.update(self._parse_summary(text))
                    periods.append(per)

                for r in self._page_rows(page):
                    r['page'] = page.page_number
                    rows.append(r)

            meta = self._parse_identity(pdf)

        self._cache = {'periods': periods, 'rows': rows, 'meta': meta}
        return self._cache

    def _parse_period(self, text: str) -> dict | None:
        m = re.search(
            r'(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})',
            text,
        )
        if not m or m.group(2) not in BULAN_EN:
            return None
        return {'month': BULAN_EN[m.group(2)], 'year': int(m.group(3))}

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
        result = {}

        for per in doc['periods']:
            bulan_id = BULAN_ORDER[per['month'] - 1]
            tahun = str(per['year'])

            # Saldo akhir harian = balance transaksi terakhir pada hari itu.
            per_day = {}
            for r in doc['rows']:
                if r['month'] == per['month'] and r['year'] == per['year']:
                    if r['balance'] is not None:
                        per_day[r['day']] = r['balance']

            total_hari = calendar.monthrange(per['year'], per['month'])[1]
            # Hari sebelum transaksi pertama memakai Opening Balance resmi.
            prev = per['opening']
            data = []
            for day in range(1, total_hari + 1):
                if day in per_day:
                    prev = per_day[day]
                data.append({
                    'Bulan': bulan_id,
                    'Tanggal': day,
                    'Saldo Akhir Harian': prev,
                })

            result[bulan_id] = {'df': pd.DataFrame(data), 'tahun': tahun}
            if per['opening'] is not None:
                result[f'_saldo_awal_{bulan_id}'] = per['opening']

        result['_nama_pemilik'] = doc['meta']['nama_pemilik']
        result['_no_rekening'] = doc['meta']['no_rekening']
        return result

    def extract_transaksi(self) -> dict:
        doc = self._parse_document()
        buckets = {}

        for r in doc['rows']:
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
        Cocokkan hasil parsing dengan angka resmi yang tercetak di tiap
        blok ringkasan PDF: No. of Debit/Credit, Total Amount Debited/
        Credited, Opening Balance, dan Closing Balance.

        Mengembalikan {'ok': bool, 'periods': [...], 'warnings': [...]}.
        Setiap ketidakcocokan dicatat sebagai warning — parser tidak boleh
        diam-diam lanjut dengan angka yang salah.
        """
        doc = self._parse_document()
        report = {'ok': True, 'periods': [], 'warnings': list(self.warnings)}

        # Tidak ada blok ringkasan sama sekali = PDF ini bukan format Kopra
        # (atau gagal dibaca). Jangan laporkan "cocok" untuk hasil kosong.
        if not doc['periods']:
            report['ok'] = False
            report['warnings'].append(
                'Tidak ada blok ringkasan periode yang terbaca — '
                'PDF kemungkinan bukan format Kopra by Mandiri.'
            )
            return report

        if not doc['rows']:
            report['ok'] = False
            report['warnings'].append(
                'Blok ringkasan terbaca tetapi tidak ada baris transaksi yang terdeteksi.'
            )

        for per in doc['periods']:
            rows = [r for r in doc['rows']
                    if r['month'] == per['month'] and r['year'] == per['year']]
            got = {
                'n_debit': sum(1 for r in rows if r['debit'] > 0),
                'n_credit': sum(1 for r in rows if r['credit'] > 0),
                'total_debit': sum(r['debit'] for r in rows),
                'total_credit': sum(r['credit'] for r in rows),
            }
            # Closing balance = saldo transaksi terakhir periode ini.
            last = [r['balance'] for r in rows if r['balance'] is not None]
            got['closing'] = last[-1] if last else None

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
                        f"{BULAN_ORDER[per['month'] - 1]} {per['year']}: {key} "
                        f"hasil parsing {actual} != angka resmi {expected}"
                    )

            if per['opening'] is None:
                report['warnings'].append(
                    f"{BULAN_ORDER[per['month'] - 1]} {per['year']}: "
                    f"Opening Balance tidak terbaca dari PDF."
                )

            report['periods'].append({
                'bulan': BULAN_ORDER[per['month'] - 1],
                'tahun': per['year'],
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

    # Kode cabang/channel yang menempel di ekor remark dan bukan bagian nama.
    _TAIL_CODE_RE = re.compile(r'(?:\s+\d{4,})+\s*$')
    # Penanda batas akhir nama. "Transfer Fee"/"Clearing Fee" adalah label
    # biaya yang MENYERTAI transfer, bukan penanda transaksi biaya.
    _NAME_STOP_RE = re.compile(
        r'\s+(?:Transfer\s+(?:Fee|ATM)|Clearing\s+Fee|Deposit|Sweep)\b|\s+\d{5,}',
        re.IGNORECASE,
    )
    # Nomor rekening yang mengawali remark: "8205290229 - JUMA BERLIAN EXIM"
    _LEAD_ACCT_RE = re.compile(r'^\d{6,}\s*-\s*')
    # Kode cabang yang menempel tanpa spasi di ekor nama: "...EXIM PT12124"
    _GLUED_CODE_RE = re.compile(r'(?<=[A-Za-z])\d{5,6}$')
    # Ekor kode bank tujuan: "... - CENAIDJA12124"
    _TAIL_BANK_RE = re.compile(r'\s*-\s*[A-Z]{4}IDJA\d*\s*$')

    def _clean_nama(self, s: str) -> str:
        s = ' '.join((s or '').split())
        s = self._LEAD_ACCT_RE.sub('', s)
        s = self._TAIL_BANK_RE.sub('', s)
        s = self._TAIL_CODE_RE.sub('', s)
        s = self._GLUED_CODE_RE.sub('', s)
        s = s.strip(' .,-/')
        return ' '.join(s.split())

    def _cut_at_stop(self, s: str) -> str:
        m = self._NAME_STOP_RE.search(s)
        return s[:m.start()] if m else s

    def _extract_nama(self, keterangan: str) -> str:
        """
        Pipeline berurutan: pola paling spesifik lebih dulu.

        Pengecekan biaya/admin sengaja ditempatkan PALING AKHIR — kalau
        ditaruh di awal, kata "Transfer Fee" yang menyertai hampir semua
        transfer InhouseTrf akan menelan nama aslinya.
        """
        if not keterangan:
            return '-'
        text = ' '.join(keterangan.split())

        # 1. MCM InhouseTrf KE/DARI <NAMA>  (pola terbesar, ~40% data)
        m = re.search(r'InhouseTrf\s+(?:KE|DARI)\s+(.+)', text, re.IGNORECASE)
        if m:
            nama = self._clean_nama(self._cut_at_stop(m.group(1)))
            if nama:
                return nama

        # 2. Transfer antar bank: <KODEBANK>IDJA/<NAMA>  (~15%)
        m = re.search(r'[A-Z]{4}IDJA/(.+)', text)
        if m:
            nama = self._clean_nama(re.split(r'\s*\d{5,}', m.group(1))[0])
            if nama:
                return nama

        # 3. Transfer ATM: "DARI/KE <NAMA> Transfer ATM <kode terminal>"
        m = re.match(r'^(?:DARI|KE)\s+(.+?)\s+Transfer\s+ATM\b', text, re.IGNORECASE)
        if m:
            nama = self._clean_nama(m.group(1))
            if nama:
                return nama

        # 4. Kliring keluar: MCM Outw CN <NAMA> ... Clearing Fee
        m = re.search(r'Outw\s+(?:CN|DN)\s+(.+)', text, re.IGNORECASE)
        if m:
            nama = self._clean_nama(self._cut_at_stop(m.group(1)))
            if nama:
                return nama

        # Nomor referensi transaksi sebelumnya kadang tersisa sebagai pecahan
        # pendek di awal remark (mis. "02 Clearing Fee ..."), karena Kopra
        # mencetak ekor nomor referensi di kolom Remark. Untuk pencocokan
        # kategori, pecahan itu diabaikan — teks aslinya tidak diubah.
        text = re.sub(r'^(?:\d{1,4}\s+)+', '', text) or text
        upper = text.upper()

        # 5. Pembayaran tagihan (UBP). Remark UBP hanya berisi kode biller,
        #    tidak memuat nama — jadi dipakai label kategori.
        if re.match(r'^UBP\d', text.strip(), re.IGNORECASE):
            return 'Pembayaran Tagihan (UBP)'

        # 6. Kategori tetap.
        if re.match(r'^DARI\s+\d+\s+KE\s+\d+', text.strip(), re.IGNORECASE):
            return 'Pindah Buku / Sweep'
        if 'MONTHLY CARD CHARGE' in upper:
            return 'Biaya Kartu Bulanan'
        # Tarik/Setor tunai mencantumkan nama pemegang rekening setelah labelnya
        # ("PEMBAYARAN TPP Tarik Tunai JUMA BERLIAN EXIM 12124"). Nama itu yang
        # dipakai; label hanya jadi cadangan kalau tidak ada nama menyusul.
        # ".*" di depan memaksa kecocokan TERAKHIR — remark kadang mengulang
        # labelnya ("TARIK TUNAI Tarik Tunai <NAMA> 12124").
        m = re.match(r'.*\b(?:Tarik|Setor)\s+Tunai\s+(.+)', text, re.IGNORECASE)
        if m:
            nama = self._clean_nama(self._cut_at_stop(m.group(1)))
            if nama:
                return nama
        if 'TARIK TUNAI' in upper or 'PENARIKAN TUNAI' in upper:
            return 'Tarik Tunai'
        if 'SETOR TUNAI' in upper or 'SETORAN TUNAI' in upper:
            return 'Setor Tunai'
        if re.match(r'^CLEARING\s+FEE\b', text.strip(), re.IGNORECASE):
            return 'Biaya Kliring'

        # 7. Biaya/bunga/pajak — PALING AKHIR, dan hanya kalau remark memang
        #    berdiri sendiri sebagai transaksi biaya (bukan sekadar memuat
        #    kata "Fee" sebagai pelengkap transfer).
        if re.match(r'^BUNGA\b', upper):
            return 'Bunga'
        if re.match(r'^PAJAK\b', upper):
            return 'Pajak'
        if re.match(r'^(?:BIAYA\s+ADM|ADM)\b', upper):
            return 'Biaya Administrasi'
        if re.match(r'^BIAYA\s+MATERAI\b', upper) or re.match(r'^MATERAI\b', upper):
            return 'Biaya Materai'

        # 8. Fallback: remark yang sudah dibersihkan, apa adanya.
        #    Bukan "-" (buang informasi) dan bukan "Biaya Admin" (salah label).
        fallback = self._clean_nama(text)
        return fallback if fallback else '-'
