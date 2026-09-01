"""
mandiri.py — Extractor khusus rekening koran Bank Mandiri (Kopra).

Mengimplementasikan BaseExtractor dengan logika parsing format PDF Mandiri Kopra:
  - Rekening Giro Mandiri (Kopra by Mandiri)
  - Support multi-bulan dalam satu PDF
  - Deteksi nama pengirim/penerima dari berbagai format transaksi Mandiri
    (MCM InhouseTrf, BNINIDJA, BRINIDJA, CENAIDJA, MVCBMRI, dll.)
"""

import re
import calendar
import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor

BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'
]

BULAN_MAP = {
    'Jan': 'Januari', 'Feb': 'Februari', 'Mar': 'Maret',
    'Apr': 'April',   'May': 'Mei',      'Mei': 'Mei',
    'Jun': 'Juni',    'Jul': 'Juli',     'Aug': 'Agustus',
    'Agu': 'Agustus', 'Sep': 'September','Oct': 'Oktober',
    'Okt': 'Oktober', 'Nov': 'November', 'Dec': 'Desember',
    'Des': 'Desember'
}


class MandiriKopraExtractor(BaseExtractor):

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Regex for amount line (3 amounts: debit credit balance)
        self.AMOUNT_LINE_RE = re.compile(
            r'^-?\s*([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$'
        )

    def get_file_prefix(self) -> str:
        return 'MANDIRI'

    def extract_no_rekening(self) -> str:
        """
        Ekstrak nomor rekening dari header Mandiri.
        Format: baris 'Account No. Account Name Alias'
                diikuti baris '0310077891078 LOGISTIK BANGUN BORN ...'
        """
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages[:5]:
                text = page.extract_text() or ''
                lines = text.split('\n')
                for i, line in enumerate(lines):
                    if re.search(r'Account\s+No\.', line, re.IGNORECASE):
                        # Baris berikutnya berisi nomor rekening
                        if i + 1 < len(lines):
                            next_line = lines[i + 1].strip()
                            m = re.match(r'^(\d{10,16})', next_line)
                            if m:
                                return m.group(1)
                        # Atau langsung di baris yang sama
                        m = re.search(r'Account\s+No\.?\s*[:\-]\s*([\d]+)', line, re.IGNORECASE)
                        if m:
                            return m.group(1).strip()
        return 'unknown'

    # ------------------------------------------------------------------ #
    #  SALDO HARIAN                                                        #
    # ------------------------------------------------------------------ #

    def extract_saldo(self) -> dict:
        saldo_per_bulan   = {}   # bulan_id -> {tanggal(int): saldo_akhir}
        opening_per_bulan = {}   # bulan_id -> int opening balance
        meta_tahun        = {}   # bulan_id -> tahun str

        current_bulan = None
        current_tahun = None
        current_date  = None     # int hari

        # Regex tanggal transaksi: harus diikuti koma (bukan baris periode)
        DATE_LINE_RE = re.compile(
            r'^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Mei|Jun|Jul|Aug|Agu|Sep|Oct|Okt|Nov|Dec|Des)\s+(\d{4}),',
            re.IGNORECASE
        )
        BALANCE_RE = re.compile(r'([\d,]+\.\d{2})\s*$')

        SKIP_KEYWORDS = [
            'OPENING BALANCE', 'CLOSING BALANCE', 'BEGINNING BALANCE',
            'POSTING DATE', 'REFERENCE', 'REMARK',
            'NO. OF DEBIT', 'NO. OF CREDIT',
            'TOTAL AMOUNT DEBIT', 'TOTAL AMOUNT CREDIT',
            'TOTAL DEBIT', 'TOTAL CREDIT',
            'SALDO AWAL', 'SALDO AKHIR',
            'PAGE', 'HALAMAN', 'PERIOD', 'ACCOUNT',
            'ACCOUNT STATEMENT', 'CREATED',
            'CURRENCY', 'BRANCH',
        ]

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split('\n')

                # --- Deteksi periode ---
                for line in lines:
                    result = self._extract_periode_mandiri(line)
                    if result:
                        bulan_id, tahun = result
                        if bulan_id not in saldo_per_bulan:
                            saldo_per_bulan[bulan_id] = {}
                            meta_tahun[bulan_id]       = tahun
                        current_bulan = bulan_id
                        current_tahun = tahun
                        break

                if not current_bulan:
                    continue

                # --- Deteksi opening balance (hanya sekali per bulan) ---
                if current_bulan not in opening_per_bulan:
                    ob = self._extract_opening_balance(text)
                    if ob is not None:
                        opening_per_bulan[current_bulan] = ob

                # --- Scan baris transaksi ---
                for line in lines:
                    line_upper = line.upper()

                    # Skip baris header/summary
                    if any(kw in line_upper for kw in SKIP_KEYWORDS):
                        continue

                    # Cek apakah baris ini diawali tanggal baru
                    dm = DATE_LINE_RE.match(line)
                    if dm:
                        bulan_raw   = dm.group(2).capitalize()
                        bulan_check = BULAN_MAP.get(bulan_raw, bulan_raw)
                        if bulan_check == current_bulan:
                            current_date = int(dm.group(1))

                    # Ambil balance dari baris ini
                    bm = BALANCE_RE.search(line)
                    if bm and current_date is not None:
                        saldo_val = self._parse_mandiri_amount(bm.group(1))
                        if saldo_val is not None:
                            saldo_per_bulan[current_bulan][current_date] = saldo_val

        # --- Build result dengan carry-forward ---
        result = {}
        for bulan_id, saldo_dict in saldo_per_bulan.items():
            if not saldo_dict:
                continue

            tahun     = meta_tahun.get(bulan_id, '2025')
            bulan_num = BULAN_ORDER.index(bulan_id) + 1 if bulan_id in BULAN_ORDER else 1
            total_hari = calendar.monthrange(int(tahun), bulan_num)[1]

            ob         = opening_per_bulan.get(bulan_id)
            prev_saldo = ob

            complete_saldo = {}
            for day in range(1, total_hari + 1):
                if day in saldo_dict:
                    complete_saldo[day] = saldo_dict[day]
                    prev_saldo = saldo_dict[day]
                elif prev_saldo is not None:
                    complete_saldo[day] = prev_saldo

            data_rows = [
                {'Bulan': bulan_id, 'Tanggal': day, 'Saldo Akhir Harian': saldo}
                for day, saldo in complete_saldo.items()
            ]

            if data_rows:
                result[bulan_id] = {
                    'df': pd.DataFrame(data_rows),
                    'tahun': tahun
                }

        # Metadata
        result['_no_rekening'] = self.extract_no_rekening()
        result['_nama_pemilik'] = self._extract_nama_pemilik()
        for bulan_id, ob in opening_per_bulan.items():
            result[f'_saldo_awal_{bulan_id}'] = ob

        return result

    # ------------------------------------------------------------------ #
    #  DETAIL TRANSAKSI                                                    #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  CORE COORDINATE-BASED PARSING                                     #
    # ------------------------------------------------------------------ #

    def _get_coordinated_rows(self, page) -> list:
        """
        Groups words on a page into logical transaction rows using Date Anchors.
        Returns a list of dicts: {date, remark, debit, credit, balance, y_top}
        """
        words = page.extract_words()
        if not words:
            return []

        # 1. Identify "Date Anchors"
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Mei', 'Jun', 'Jul', 'Aug', 'Agu', 'Sep', 'Oct', 'Okt', 'Nov', 'Dec', 'Des']
        anchors = []
        for i, w in enumerate(words):
            if w['x0'] < 60 and re.match(r'^\d{1,2}$', w['text']):
                if i + 2 < len(words):
                    next1 = words[i+1]['text'].capitalize()[:3]
                    next2 = words[i+2]['text']
                    if any(m.startswith(next1) for m in months) and re.match(r'^\d{4},?$', next2):
                        anchors.append({
                            'y': w['top'],
                            'day': int(w['text']),
                            'full_date': f"{w['text']} {words[i+1]['text']} {words[i+2]['text']}"
                        })

        if not anchors:
            return []

        # 2. Extract Row Clusters
        rows = []
        # Find page footer bound
        footer_y = 760
        for w in words:
            if 'koprabymandiri.com' in w['text'] or 'Page' == w['text']:
                if w['top'] > 600: footer_y = min(footer_y, w['top'])

        for i, anchor in enumerate(anchors):
            y_start = anchor['y'] - 3
            y_end = anchors[i+1]['y'] - 3 if i+1 < len(anchors) else footer_y

            cluster = [w for w in words if y_start <= w['top'] < y_end]
            
            # Refined Boundaries for Kopra:
            # Date/Ref ends ~150
            # Remark ends ~415
            # Amounts start ~415
            remark_words = sorted([w for w in cluster if 150 <= w['x0'] < 418], key=lambda x: (x['top'], x['x0']))
            amt_words = sorted([w for w in cluster if w['x0'] >= 418], key=lambda x: (x['top'], x['x0']))
            
            remark_text = " ".join([w['text'] for w in remark_words])
            # Remove technical artifacts that often leak into the remark column
            remark_text = re.sub(r'\b0\.00\b', '', remark_text)
            remark_text = re.sub(r' +', ' ', remark_text).strip()
            
            # Parse Amounts (Debit:420+, Credit:460+, Balance:505+)
            debit = 0
            credit = 0
            balance = 0
            for aw in amt_words:
                val = self._parse_mandiri_amount(aw['text']) or 0
                if 418 <= aw['x0'] < 458:
                    debit = val
                elif 458 <= aw['x0'] < 503:
                    credit = val
                elif aw['x0'] >= 503:
                    balance = val

            rows.append({
                'day': anchor['day'],
                'date_raw': anchor['full_date'],
                'remark_raw': remark_text,
                'debit': debit,
                'credit': credit,
                'balance': balance
            })

        return rows

    def extract_no_rekening(self) -> str:
        """Ekstrak nomor rekening dari header."""
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages[:1]:
                text = page.extract_text() or ''
                # Pattern: "Account No. Account Name Alias" followed by digits
                m = re.search(r'No\.\s+(?:Account\s+Name\s+)?(\d{10,16})', text, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
                # Try specific coordinate/word search
                words = page.extract_words()
                for i, w in enumerate(words):
                    if 'Account' == w['text'] and i+2 < len(words) and 'No.' == words[i+1]['text']:
                        # The account number is usually some words ahead
                        for j in range(i+2, min(i+10, len(words))):
                            if re.match(r'^\d{10,16}$', words[j]['text']):
                                return words[j]['text']
        return 'unknown'

    def extract_saldo(self) -> dict:
        result_map = {} # bulan_id -> {day: balance}
        meta = {} 

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                period_match = self._extract_periode_mandiri(text)
                if period_match:
                    bulan_id, tahun = period_match
                    if bulan_id not in result_map:
                        result_map[bulan_id] = {}
                        meta[bulan_id] = {'tahun': tahun, 'opening': self._extract_opening_balance(text)}
                
                current_bulan = list(result_map.keys())[-1] if result_map else None
                if not current_bulan: continue

                rows = self._get_coordinated_rows(page)
                for r in rows:
                    # Update daily balance
                    result_map[current_bulan][r['day']] = r['balance']

        final_result = {}
        for bulan_id, days_dict in result_map.items():
            if not days_dict: continue
            tahun = meta[bulan_id]['tahun']
            bulan_num = BULAN_ORDER.index(bulan_id) + 1 if bulan_id in BULAN_ORDER else 1
            total_hari = calendar.monthrange(int(tahun), bulan_num)[1]
            
            ob = meta[bulan_id]['opening']
            prev = ob
            data = []
            for day in range(1, total_hari + 1):
                if day in days_dict:
                    prev = days_dict[day]
                data.append({'Bulan': bulan_id, 'Tanggal': day, 'Saldo Akhir Harian': prev})
            
            final_result[bulan_id] = {'df': pd.DataFrame(data), 'tahun': tahun}
        
        final_result['_no_rekening'] = self.extract_no_rekening()
        final_result['_nama_pemilik'] = self._extract_nama_pemilik()
        return final_result

    def extract_transaksi(self) -> dict:
        transaksi_per_bulan = {}
        
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                period_match = self._extract_periode_mandiri(text)
                if period_match:
                    bulan_id, _ = period_match
                    if bulan_id not in transaksi_per_bulan:
                        transaksi_per_bulan[bulan_id] = []
                
                current_bulan = list(transaksi_per_bulan.keys())[-1] if transaksi_per_bulan else None
                if not current_bulan: continue

                rows = self._get_coordinated_rows(page)
                for r in rows:
                    jenis = 'Debit' if r['debit'] > 0 else 'Kredit'
                    nominal = r['debit'] if r['debit'] > 0 else r['credit']
                    if nominal == 0: continue # Skip if no mutation
                    
                    desc = re.sub(r'\s+', ' ', r['remark_raw']).strip()
                    nama = self._extract_nama_mandiri(desc, jenis, nominal)

                    transaksi_per_bulan[current_bulan].append({
                        'Bulan': current_bulan,
                        'Tanggal': r['day'],
                        'Jenis Mutasi': jenis,
                        'Mutasi': nominal,
                        'Nama Pengirim/Penerima': nama,
                        'Keterangan Transaksi': desc
                    })

        result = {}
        for bulan, items in transaksi_per_bulan.items():
            if items:
                result[bulan] = pd.DataFrame(items)
        return result

    # ------------------------------------------------------------------ #
    #  HELPER INTERNAL                                                     #
    # ------------------------------------------------------------------ #

    def _parse_mandiri_amount(self, s: str) -> int | None:
        if not s or s == '-': return 0
        clean = s.replace(',', '').strip()
        try:
            return int(float(clean))
        except:
            return 0

    def _extract_periode_mandiri(self, text: str) -> tuple | None:
        m = re.search(
            r'Period\s*:\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})',
            text, re.IGNORECASE
        )
        if not m:
            # Try without "Period :"
            m = re.search(r'(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})', text)
        
        if m:
            bulan_raw = m.group(2).capitalize()
            tahun     = m.group(3)
            bulan_id  = BULAN_MAP.get(bulan_raw, bulan_raw)
            return bulan_id, tahun
        return None

    def _extract_opening_balance(self, text: str) -> int:
        # Looking for "Opening Balance" followed by a number
        m = re.search(r'Opening\s+Balance\s+([\d,]+\.\d{2})', text, re.IGNORECASE)
        if m:
            return self._parse_mandiri_amount(m.group(1))
        
        # Try finding in lines
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if 'Opening Balance' in line:
                if i+1 < len(lines):
                    m = re.match(r'^([\d,]+\.\d{2})', lines[i+1].strip())
                    if m: return self._parse_mandiri_amount(m.group(1))
        return 0

    def _extract_nama_pemilik(self) -> str:
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages[:2]:
                text = page.extract_text() or ''
                m = re.search(r'Account\s+Name\s*(.+)', text, re.IGNORECASE)
                if m:
                    # Clean up
                    name = m.group(1).split('Alias')[0].strip()
                    return name
        return '-'

    def _extract_nama_mandiri(self, keterangan: str, jenis_mutasi: str, nominal: int) -> str:
        """Extract neat nama pengirim/penerima."""
        # Convert to uppercase for matching
        full_text = keterangan.upper()
        
        # 1. Biaya Bank (High Priority)
        if nominal == 2500 and jenis_mutasi == 'Debit':
            return 'Biaya Pelayanan' if 'FEE' in full_text else 'Biaya Transfer'
        
        # Specific Biaya/Admin keywords
        if any(kw in full_text for kw in ['ADM', 'FEE', 'RTGS', 'BUNGA', 'PAJAK', 'CHRG', 'MATERAI']):
             if 'BUNGA' in full_text: return 'Bunga'
             if 'PAJAK' in full_text: return 'Pajak'
             if 'MATERAI' in full_text: return 'Biaya Materai'
             return 'Biaya Admin'

        # 2. MCM Inhouse Transfer
        if 'INHOUSETRF' in full_text:
            # Pattern: "KE [NAME] - ..." or "DARI [NAME] ..."
            m = re.search(r'(?:KE|DARI)\s+([A-Z\s\.]+?)(?:\s+TRANSFER|\s+DEPOSIT|\s+\d{2}:|\s+FEE|\-|$)', full_text)
            if m:
                nama = m.group(1).strip()
                # Clean nested keywords
                nama = re.sub(r'\b(TRANSFER|MCM|INHOUSETRF)\b', '', nama).strip()
                return " ".join(nama.split()[:4])

        # 3. Inter-bank Transfer with Codes (CENAIDJA, BNINIDJA, etc.)
        # Pattern: "CENAIDJA/NAME" or "BMRIIDJA/NAME"
        bank_match = re.search(r'[A-Z]{8}/([A-Z\s\.]+?)(?:\d{5,}|$)', full_text)
        if bank_match:
            nama = bank_match.group(1).strip()
            # Stop if reached a technical ID (digits)
            nama = re.split(r'\d+', nama)[0].strip()
            return " ".join(nama.split()[:4])

        # 4. MCM Transfer / Payroll
        m = re.search(r'MCM\s+(?:TRANSFER|PAYROLL|PAYMENT)\s+(?:KE|DARI)\s+([A-Z\s\.]+)', full_text)
        if m:
            nama = m.group(1).strip()
            return " ".join(nama.split()[:4])

        # 5. DARI/KE Generic Pattern (Common in Mandiri)
        m = re.search(r'(?:DARI|KE)\s+([A-Z]{3,}(?:\s+[A-Z]{3,})+)', full_text)
        if m:
            nama = m.group(1).strip()
            # Filter technical junk
            if not any(kw in nama for kw in ['TRANSFER', 'BRANCH', 'IDR', 'BANK', 'KOPRA']):
                return " ".join(nama.split()[:4])

        # 6. Fallback clean name (multiple uppercase words)
        # Look for sequences of long uppercase words
        # e.g., "PT RIDHO SRIBUMI"
        candidates = re.findall(r'\b([A-Z]{3,}(?:\s+[A-Z\.\s]{3,})+)\b', full_text)
        for cand in candidates:
            cand_clean = cand.strip()
            # Ignore technical segments
            if any(kw in cand_clean for kw in ['MCM', 'TRF', 'BRANCH', 'IDR', 'BANK', 'KOPRA', 'FEE', 'AUTO', 'COLL']):
                continue
            if len(cand_clean.split()) >= 2:
                return " ".join(cand_clean.split()[:4])

        # 7. Last Resort: Owner Name for internal moves
        if any(kw in full_text for kw in ['PINDAH BUKU', 'OVERBOOKING', 'ISI ATM']):
            owner = self._extract_nama_pemilik()
            if owner and owner != '-': return " ".join(owner.split()[:4])

        return '-'


