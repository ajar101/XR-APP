"""
bni.py — Extractor rekening koran Bank BNI (e-Statement PDF)

Format tabel PDF: Posting Date | Effective Date | Branch | Journal |
                  Transaction Description | Amount | DB/CR | Balance

Strategi:
  - extract_words() dengan batas kolom koordinat (x0)
  - Anchor tiap transaksi pada Journal number (6 digit)
  - Amount dihitung dari selisih Balance (Amount di PDF bold = double-char, tidak reliable)
  - Description disambung lintas halaman via pending buffer
  - Saldo awal = Ledger Balance dari halaman 1
  - Transaksi terakhir (BIAYA ADM REK) balance-nya kosong di PDF:
    amount = |last_known_balance - ending_balance| dari summary halaman terakhir
  - Verifikasi sum(D) dan sum(K) harus cocok dengan summary PDF
"""

import re
import warnings
import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor


# ─────────────────────────────────────────────────────────────────────────────
# KONSTANTA
# ─────────────────────────────────────────────────────────────────────────────

BULAN_MAP_EN = {
    'Jan': 'Januari',  'Feb': 'Februari', 'Mar': 'Maret',
    'Apr': 'April',    'May': 'Mei',       'Jun': 'Juni',
    'Jul': 'Juli',     'Aug': 'Agustus',   'Sep': 'September',
    'Oct': 'Oktober',  'Nov': 'November',  'Dec': 'Desember',
}

# Batas kolom x0 (piksel) berdasarkan analisis koordinat PDF BNI
COL_BOUNDS = {
    'posting':     (23,  132),
    'journal':     (296, 337),
    'description': (337, 514),
    'amount':      (514, 575),
    'dbcr':        (575, 625),
    'balance':     (625, 710),
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPER INTERNAL
# ─────────────────────────────────────────────────────────────────────────────

def _get_col(x0: float) -> str:
    for col, (lo, hi) in COL_BOUNDS.items():
        if lo <= x0 < hi:
            return col
    return 'other'


def _clean_balance(s: str):
    """Parse '1,213,932.00' -> 1213932."""
    if not s:
        return None
    try:
        return int(float(s.replace(',', '')))
    except Exception:
        return None


def _parse_periode(page_text: str):
    """
    Ekstrak bulan & tahun dari header halaman BNI.
    Format: 'Period : 01-Oct-25 - 31-Oct-25'
    Return: ('Oktober', '2025') atau (None, None)
    """
    m = re.search(r'Period\s*:\s*\d{2}-(\w{3})-(\d{2})', page_text)
    if m:
        bulan = BULAN_MAP_EN.get(m.group(1))
        tahun = '20' + m.group(2)
        return bulan, tahun
    return None, None


def _parse_summary(pdf_path: str) -> dict:
    """
    Baca Ledger Balance (hal.1) dan summary (hal.terakhir).
    Return: {ledger, ending, total_debet, cnt_debet, total_kredit, cnt_kredit}
    """
    with pdfplumber.open(pdf_path) as pdf:
        t1 = pdf.pages[0].extract_text() or ''
        tl = pdf.pages[-1].extract_text() or ''

    def _to_int(m, grp=1):
        if not m:
            return None
        try:
            return int(float(m.group(grp).replace(',', '')))
        except Exception:
            return None

    m_l = re.search(r'Ledger\s+Balance:\s*([\d,]+\.\d{2})', t1)
    m_e = re.search(r'Ending Balance\s*:\s*([\d,]+\.\d{2})', tl)
    m_d = re.search(r'Total Debet\s*:\s*(\d+)\s*([\d,]+\.\d{2})', tl)
    m_k = re.search(r'Total Credit\s*:\s*(\d+)\s*([\d,]+\.\d{2})', tl)

    return {
        'ledger':       _to_int(m_l),
        'ending':       _to_int(m_e),
        'total_debet':  _to_int(m_d, 2),
        'cnt_debet':    _to_int(m_d, 1),
        'total_kredit': _to_int(m_k, 2),
        'cnt_kredit':   _to_int(m_k, 1),
    }


def _parse_raw_transactions(pdf_path: str, ledger_balance: int, ending_balance: int) -> list:
    """
    Baca semua transaksi dari PDF BNI dengan strategi word-coordinate.
    Return: list of dict {posting, journal, description, dbcr, balance, amount}
    """
    raw_rows  = []
    pending   = None
    prev_balance = ledger_balance   # inisialisasi dari Ledger Balance

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(
                keep_blank_chars=False, x_tolerance=3, y_tolerance=3
            )
            # Hanya area data (lewati header/footer setiap halaman)
            data_words = [w for w in words if w['top'] > 315]

            # Kelompokkan per baris (toleransi y 4pt)
            rows_by_y: dict = {}
            for w in data_words:
                y_key = round(w['top'] / 4) * 4
                rows_by_y.setdefault(y_key, []).append(w)

            for y_key in sorted(rows_by_y.keys()):
                row_words = rows_by_y[y_key]

                # Anchor transaksi baru: baris yang punya Journal (6 digit)
                journal_words = [
                    w for w in row_words
                    if 296 <= w['x0'] < 337 and re.match(r'^\d{6}$', w['text'])
                ]

                if journal_words:
                    if pending is not None:
                        raw_rows.append(pending)

                    by_col: dict = {}
                    for w in row_words:
                        by_col.setdefault(_get_col(w['x0']), []).append(w['text'])

                    posting_text = ' '.join(by_col.get('posting', []))
                    desc_text    = ' '.join(by_col.get('description', []))
                    dbcr_text    = ' '.join(by_col.get('dbcr', [])).strip()
                    balance_val  = _clean_balance(' '.join(by_col.get('balance', [])))

                    # Amount = |delta balance| (lebih reliable dari string Amount bold)
                    amount_val = (
                        abs(balance_val - prev_balance)
                        if (balance_val is not None and prev_balance is not None)
                        else None
                    )
                    if balance_val is not None:
                        prev_balance = balance_val

                    pending = {
                        'posting':     posting_text,
                        'journal':     journal_words[0]['text'],
                        'description': desc_text,
                        'dbcr':        dbcr_text,
                        'balance':     balance_val,
                        'amount':      amount_val,
                    }

                elif pending is not None:
                    # Baris lanjutan deskripsi (termasuk lintas halaman)
                    desc_words = [w for w in row_words if 337 <= w['x0'] < 514]
                    if desc_words:
                        pending['description'] += ' ' + ' '.join(
                            w['text'] for w in desc_words
                        )

        if pending is not None:
            raw_rows.append(pending)

    # ── Fix baris yang balance-nya kosong di PDF (misal: BIAYA ADM REK) ──
    for row in raw_rows:
        if row['balance'] is None and row['amount'] is None:
            last_known = next(
                (r['balance'] for r in reversed(raw_rows)
                 if r is not row and r['balance'] is not None),
                None
            )
            if last_known is not None and ending_balance is not None:
                row['amount']  = abs(last_known - ending_balance)
                row['balance'] = ending_balance

    return raw_rows


# ─────────────────────────────────────────────────────────────────────────────
# EKSTRAKSI NAMA PENGIRIM / PENERIMA
# ─────────────────────────────────────────────────────────────────────────────

def _extract_nama(desc_raw: str) -> str:
    """
    Ekstrak nama pengirim/penerima dari raw description.

    Pola yang ditangani:
    A. TRANSFER KE | PEMINDAHAN KE [NO_REK] [NAMA] | [KET]     → nama penerima
    B. TRF ECHANNEL | PEMINDAHAN KE ...                         → '-'
    C. TRANSFER DARI | PEMINDAHAN DARI [NO_REK] [NAMA] | ...    → nama inline sumber
    D. [NAMA] TRANSFER DARI | PEMINDAHAN DARI ...               → nama sebelum TRANSFER DARI
    E. TRF ECHANNEL | PEMINDAHAN DARI ... | [NAMA]              → nama di pipe akhir
    F. [NO_REK] | 0000... | [NAMA] TRF/PAY...                   → nama di segmen ke-3
    G. BERSAUDARA | ... (sambungan MUSRIYADI BERSAUDARA)        → 'MUSRIYADI BERSAUDARA'
    H. SETOR TUNAI | [NAMA]                                     → nama
    I. BY TRX BIFAST                                            → '-'
    J. JASA GIRO/BUNGA                                          → 'Bunga'
    K. PPH / BIAYA ADM REK                                      → '-'
    """
    d = desc_raw.upper().strip()

    # ── Kasus khusus ────────────────────────────────────────────────────
    if 'BY TRX BIFAST' in d:
        return 'Biaya Rekening'
    if 'JASA GIRO' in d or ('BUNGA' in d and 'PPH' not in d and 'PAJAK' not in d):
        return 'Bunga'
    if re.match(r'^\s*PPH\b', d):
        return '-'
    if 'BIAYA ADM REK' in d:
        return '-'

    # ── SETOR TUNAI ─────────────────────────────────────────────────────
    if 'SETOR TUNAI' in d:
        parts = desc_raw.split('|')
        if len(parts) >= 2:
            nama = parts[-1].strip()
            nama = re.sub(r'\s+(?:TRF|Transfer|Lainnya|BIFAST|BI FAST)\b.*',
                          '', nama, flags=re.IGNORECASE).strip()
            if nama:
                return ' '.join(nama.split()[:5])
        return 'Setoran Tunai'

    # ── Pola G: BERSAUDARA di awal → sambungan MUSRIYADI BERSAUDARA ────
    if d.startswith('BERSAUDARA'):
        return 'MUSRIYADI BERSAUDARA'

    # ── Pola D: [NAMA] TRANSFER DARI | PEMINDAHAN DARI ...  ─────────────
    # Nama pengirim ke rekening kita ada SEBELUM keyword "TRANSFER DARI"
    # Contoh: "APRIYANI TRANSFER DARI | ..." atau "BAHRIN SIREGAR Lainnya TRANSFER DARI |"
    if 'TRANSFER DARI' in d and 'PEMINDAHAN DARI' in d:
        m_before = re.search(
            r'([A-Z][A-Z\s]+?)\s+(?:Lainnya\s+)?TRANSFER DARI\s*\|',
            desc_raw, re.IGNORECASE
        )
        if m_before:
            nama = m_before.group(1).strip()
            # Hapus nomor rekening dan nol
            nama = re.sub(r'\b\d+\b', '', nama).strip()
            nama = re.sub(r'\s+', ' ', nama).strip()
            if nama and len(nama) > 2:
                return ' '.join(nama.split()[:5])

    # ── Pola C: TRANSFER DARI inline (nama setelah no_rek di PEMINDAHAN DARI) ──
    if 'TRANSFER DARI' in d:
        m = re.search(
            r'PEMINDAHAN DARI\s+\d+\s+(.+?)(?:\s*\||\s*TRF TO:|$)',
            desc_raw, re.IGNORECASE
        )
        if m:
            nama = m.group(1).strip()
            nama = re.sub(r'\s*\|.*', '', nama).strip()
            nama = re.sub(r'\s*TRF TO:.*', '', nama, flags=re.IGNORECASE).strip()
            nama = re.sub(r'\s+', ' ', nama).strip()
            if nama and not re.match(r'^[\d\s]+$', nama):
                return ' '.join(nama.split()[:5])

    # ── Pola E+F: PEMINDAHAN DARI → cari nama di segmen pipe ────────────
    if 'PEMINDAHAN DARI' in d:
        parts = [p.strip() for p in desc_raw.split('|')]
        for part in parts:
            # Hapus amount double-char, no_rek, nol
            clean = re.sub(r'[\d,]+,\d{4}\.\.\d{4}', '', part).strip()
            clean = re.sub(r'\b\d{8,16}\b', '', clean).strip()
            clean = re.sub(r'\b0{6,}\b', '', clean).strip()
            clean = re.sub(
                r'\b(Lainnya|Transfer|BI FAST|BIFAST|INTERNET BANKING)\b',
                '', clean, flags=re.IGNORECASE
            ).strip()
            clean = re.sub(
                r'\b(PEMINDAHAN|DARI|TRF/PAY/TOP-UP|ECHANNEL|TRANSFER)\b',
                '', clean, flags=re.IGNORECASE
            ).strip()
            clean = re.sub(r'\s+', ' ', clean).strip()
            if clean and not re.match(r'^[\d\s,\.]+$', clean) and len(clean) > 2:
                words = [w for w in clean.split()[:5]
                         if not re.match(r'^\d+$', w) and len(w) > 1]
                if words:
                    return ' '.join(words)

    # ── Pola B: semua PEMINDAHAN KE (TRF ECHANNEL maupun TRANSFER KE) ───
    # Format BNI tidak mencantumkan nama penerima di PEMINDAHAN KE biasa
    if 'PEMINDAHAN KE' in d:
        return '-'

    # ── Pola F generik: untuk kredit yang masuk lewat jalur lain ────────
    # (misal: SETOR TUNAI tanpa pipe, atau format tidak dikenal)
    parts = [p.strip() for p in desc_raw.split('|')]
    for part in parts:
        clean = re.sub(r'[\d,]+,\d{4}\.\.\d{4}', '', part).strip()
        clean = re.sub(r'\b\d{8,16}\b', '', clean).strip()
        clean = re.sub(r'\b0{6,}\b', '', clean).strip()
        clean = re.sub(
            r'\b(TRF/PAY/TOP-UP ECHANNEL|PEMINDAHAN KE|PEMINDAHAN DARI|'
            r'TRANSFER KE|TRANSFER DARI|BI FAST|BIFAST|LAINNYA|'
            r'INTERNET BANKING)\b',
            '', clean, flags=re.IGNORECASE
        ).strip()
        clean = re.sub(r'\s+', ' ', clean).strip()
        if clean and not re.match(r'^[\d\s,\.]+$', clean) and len(clean) > 2:
            words = [w for w in clean.split()[:5]
                     if not re.match(r'^\d+$', w) and len(w) > 1]
            if words:
                return ' '.join(words)

    return '-'


# ─────────────────────────────────────────────────────────────────────────────
# EKSTRAKSI KETERANGAN TRANSAKSI
# ─────────────────────────────────────────────────────────────────────────────

def _extract_keterangan(desc_raw: str) -> str:
    """Ekstrak keterangan bermakna, buang nomor rekening, nol, boilerplate."""
    d = desc_raw.upper()

    if 'BY TRX BIFAST' in d:
        return 'BY TRX BIFAST'
    if 'JASA GIRO' in d or ('BUNGA' in d and 'PPH' not in d and 'PAJAK' not in d):
        return 'JASA GIRO/BUNGA'
    if re.match(r'^\s*PPH\b', d):
        return 'PPH'
    if 'BIAYA ADM REK' in d:
        return 'BIAYA ADM REK'

    if 'SETOR TUNAI' in d:
        parts = desc_raw.split('|')
        return ('SETOR TUNAI | ' + parts[-1].strip())[:120] if len(parts) >= 2 else 'SETOR TUNAI'

    # Pola sambungan MUSRIYADI BERSAUDARA: ambil keterangan yang bermakna
    if d.startswith('BERSAUDARA'):
        m = re.search(r'PEMINJAMAN\s+(.+?)(?:\s+TRF TO:|$)', desc_raw, re.IGNORECASE)
        if m:
            ket = re.sub(r'[\d,]+,\d{4}\.\.\d{4}', '', m.group(1)).strip()
            ket = re.sub(r'\s+', ' ', ket).strip()
            if ket:
                return f'PEMINJAMAN {ket}'[:120]
        return 'TRANSFER DARI MUSRIYADI BERSAUDARA'

    BOILERPLATE = {
        'TRF/PAY/TOP-UP ECHANNEL', 'PEMINDAHAN KE', 'PEMINDAHAN DARI',
        'TRANSFER KE', 'TRANSFER DARI', 'BI FAST', 'BIFAST',
        'INTERNET BANKING', 'LAINNYA', 'TRF TO:',
        # Nama rekening sendiri yang muncul di kredit internal
        'PANTAI SUBUR',
    }

    # Tambahan: kata teknis yang perlu di-strip dari keterangan
    STRIP_WORDS = re.compile(
        r'\b(?:TRF/PAY/TOP-UP ECHANNEL|PEMINDAHAN KE|PEMINDAHAN DARI|'
        r'TRANSFER KE|TRANSFER DARI|BI FAST|BIFAST|LAINNYA|'
        r'INTERNET BANKING|PANTAI SUBUR)\b',
        re.IGNORECASE
    )

    parts = [p.strip() for p in desc_raw.split('|')]
    ket_parts = []
    for part in parts:
        # Hapus amount double-char pattern (artefak bold PDF)
        clean = re.sub(r'[\d,]+,\d{4}\.\.\d{4}', '', part).strip()
        clean = re.sub(r'\b\d{10,16}\b', '', clean).strip()
        clean = re.sub(r'\b0{6,}\b', '', clean).strip()
        clean = re.sub(r'TRF\s+TO:\S+', '', clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r'\s+', ' ', clean).strip()
        if not clean:
            continue
        upper = clean.upper()
        # Skip boilerplate teknis
        if any(b in upper for b in BOILERPLATE):
            continue
        # Skip jika hanya berisi angka/simbol
        if re.match(r'^[\d\s,\.]+$', clean):
            continue
        ket_parts.append(clean)

    if ket_parts:
        result = ' '.join(' '.join(ket_parts[-2:]).split())
        result = re.sub(r'^[\s|]+|[\s|]+$', '', result).strip()
        return result[:120]

    # Fallback: bersihkan raw description dari noise teknis
    # Hapus: amount double-char, no_rek panjang, deretan nol, TRF TO:
    # Pertahankan: nama, keterangan, nama teknis yang bermakna
    fb = desc_raw
    fb = re.sub(r'[\d,]+,\d{4}\.\.\d{4}', '', fb)   # amount double-char
    fb = re.sub(r'\b\d{10,16}\b', '', fb)              # no rekening panjang
    fb = re.sub(r'\b0{6,}\b', '', fb)                  # deretan nol
    fb = re.sub(r'TRF\s+TO:\S+', '', fb, flags=re.IGNORECASE)
    # Bersihkan pipe berlebih
    fb = re.sub(r'\|\s*\|', '|', fb)
    fb = re.sub(r'^\s*\|\s*', '', fb)
    fb = re.sub(r'\s*\|\s*$', '', fb)
    fb = re.sub(r'\s+', ' ', fb).strip()
    return fb[:120]


# ─────────────────────────────────────────────────────────────────────────────
# KELAS UTAMA
# ─────────────────────────────────────────────────────────────────────────────

class BNIExtractor(BaseExtractor):
    """Extractor rekening koran BNI."""

    def get_file_prefix(self) -> str:
        return 'BNI'

    def _get_identity(self) -> dict:
        with pdfplumber.open(self.pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ''

        nama_pemilik = '-'
        for line in text.split('\n'):
            line = line.strip()
            if (line
                    and re.match(r'^[A-Z][A-Z\s\.]+$', line)
                    and len(line) > 3
                    and not any(x in line for x in [
                        'ACCOUNT', 'PERIOD', 'PAGE', 'JL ', 'KALI',
                        'BONTANG', 'STATEMENT', 'CURRENT', 'TIMUR'
                    ])):
                nama_pemilik = line
                break

        m_rek    = re.search(r'Account\s+No\.\s*:\s*(\d+)', text)
        bulan, tahun = _parse_periode(text)

        return {
            'nama':        nama_pemilik,
            'no_rekening': m_rek.group(1) if m_rek else '-',
            'bulan':       bulan,
            'tahun':       tahun,
        }

    # ─── Sheet 1: Saldo Harian ────────────────────────────────────────────

    def extract_saldo(self) -> dict:
        """
        Return: {bulan: {'df': DataFrame, 'tahun': str}, '_nama_pemilik', '_no_rekening', ...}
        DataFrame: Bulan | Tanggal | Saldo Akhir Harian
        """
        identity = self._get_identity()
        summary  = _parse_summary(self.pdf_path)
        bulan    = identity['bulan']
        tahun    = identity['tahun']

        raw_rows = _parse_raw_transactions(
            self.pdf_path, summary['ledger'], summary['ending']
        )

        # Saldo terakhir per hari
        saldo_per_hari: dict = {}
        for row in raw_rows:
            m = re.match(r'(\d{2})/\d{2}/\d{4}', row['posting'])
            if m and row['balance'] is not None:
                saldo_per_hari[m.group(1)] = row['balance']

        if not saldo_per_hari:
            return {}

        all_days  = sorted(saldo_per_hari.keys(), key=int)
        first_day = int(all_days[0])
        last_day  = int(all_days[-1])

        # Forward-fill hari tanpa transaksi
        complete: dict = {}
        prev = None
        for d in range(first_day, last_day + 1):
            ds = f'{d:02d}'
            if ds in saldo_per_hari:
                complete[ds] = saldo_per_hari[ds]
                prev = saldo_per_hari[ds]
            elif prev is not None:
                complete[ds] = prev

        data = [
            {
                'Bulan':              bulan,
                'Tanggal':            int(ds),
                'Saldo Akhir Harian': complete[ds],
            }
            for ds in sorted(complete.keys(), key=int)
        ]

        return {
            bulan: {'df': pd.DataFrame(data), 'tahun': tahun},
            '_nama_pemilik':        identity['nama'],
            '_no_rekening':         identity['no_rekening'],
            f'_saldo_awal_{bulan}': summary['ledger'],
        }

    # ─── Sheet 2: Detail Transaksi ────────────────────────────────────────

    def extract_transaksi(self) -> dict:
        """
        Return: {bulan: DataFrame}
        DataFrame: Bulan | Tanggal | Jenis Mutasi | Mutasi |
                   Nama Pengirim/Penerima | Keterangan Transaksi
        """
        identity = self._get_identity()
        summary  = _parse_summary(self.pdf_path)
        bulan    = identity['bulan']

        raw_rows = _parse_raw_transactions(
            self.pdf_path, summary['ledger'], summary['ending']
        )

        # Verifikasi total vs summary PDF
        total_d = sum(r['amount'] for r in raw_rows
                      if r['dbcr'] == 'D' and r['amount'] is not None)
        total_k = sum(r['amount'] for r in raw_rows
                      if r['dbcr'] == 'K' and r['amount'] is not None)

        if summary['total_debet'] and total_d != summary['total_debet']:
            warnings.warn(
                f"BNI [{bulan}]: Total Debet tidak cocok! "
                f"Hitung={total_d:,} Target={summary['total_debet']:,}"
            )
        if summary['total_kredit'] and total_k != summary['total_kredit']:
            warnings.warn(
                f"BNI [{bulan}]: Total Kredit tidak cocok! "
                f"Hitung={total_k:,} Target={summary['total_kredit']:,}"
            )

        records = []
        for row in raw_rows:
            m = re.match(r'(\d{2})/\d{2}/\d{4}', row['posting'])
            if not m:
                continue
            amount = row['amount']
            if amount is None or amount == 0:
                continue

            records.append({
                'Bulan':                  bulan,
                'Tanggal':                int(m.group(1)),
                'Jenis Mutasi':           'Debit' if row['dbcr'] == 'D' else 'Kredit',
                'Mutasi':                 amount,
                'Nama Pengirim/Penerima': _extract_nama(row['description']),
                'Keterangan Transaksi':   _extract_keterangan(row['description']),
            })

        return {bulan: pd.DataFrame(records)} if records else {}
