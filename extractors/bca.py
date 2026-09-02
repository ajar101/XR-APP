"""
bca.py — Extractor khusus rekening koran Bank BCA.

Mengimplementasikan BaseExtractor dengan logika parsing format PDF BCA:
  - Rekening Giro & Tabungan BCA
  - Support multi-bulan dalam satu PDF
  - Deteksi nama pengirim/penerima dari berbagai format transaksi BCA
    (BI-FAST, RTGS, LLG, SWITCHING CR, KR OTOMATIS, TRSF E-BANKING, dll.)
"""

import re
import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor

ROUTING_CODES = {
    'HRDAIDJ1', 'BKKBIDJA', 'AKTBIDJ1', 'NETBIDJA', 'LOMAIDJ1', 'ARTGIDJA', 'PDJBIDJA', 'BNINIDJA', 'BBAIIDJA', 'BCIAIDJA',
    'CTCBIDJA', 'BICNIDJA', 'BDINIDJA', 'SYBDIDJ1', 'BDKIIDJ1', 'GNESIDJA', 'HNBNIDJA', 'HBNIIDJA', 'MAYOIDJA', 'IAPTIDJA',
    'BIDXIDJA', 'SYJBIDJ1', 'SYATIDJ1', 'JSABIDJ1', 'PDJTIDJ1', 'CICTIDJA', 'SDOBIDJ1', 'BMRIIDJA', 'SIHBIDJ1', 'MASDIDJ1',
    'MAYAIDJA', 'MEGAIDJA', 'MEDHIDS1', 'MHCCIDJA', 'BUMIIDJA', 'MUABIDJA', 'BMSEIDJA', 'LFIBIDJ1', 'YUDBIDJ1', 'BOFAID2X',
    'BKCHIDJA', 'BKIDIDJA', 'LMANIDJ1', 'PINBIDJA', 'BBBAIDJA', 'PUBAIDJ1', 'BRINIDJA', 'AGTBIDJA', 'BDIPIDJ1', 'IDMOIDJ1',
    'SBJKIDJA', 'SYTBIDJ1', 'BSMDIDJA', 'VICTIDJ1', 'SWAGIDJ1', 'BSDRIDJA', 'CENAIDJA', 'SYCAIDJ1', 'SYBKIDJ1', 'BNPAIDJA',
    'ABALIDBS', 'PDBBIDJ1', 'PDBKIDJ1', 'PDYKIDJ1', 'SYYKIDJ1', 'PDJMIDJ1', 'PDJGIDJ1', 'SYJGIDJ1', 'SYJTIDJ1', 'PDKBIDJ1',
    'SYKBIDJ1', 'PDKSIDJ1', 'SYKSIDJ1', 'PDKGIDJ1', 'PDKTIDJ1', 'SYKTIDJ1', 'PDLPIDJ1', 'PDMLIDJ1', 'PDNBIDJ1', 'PDNTIDJA',
    'PDIJIDJ1', 'PDRIIDJA', 'PDWGIDJ1', 'PDWRIDJ1', 'PDWSIDJA', 'PDWUIDJ1', 'PDSBIDJ1', 'BSSPIDSP', 'SYSSIDJ1', 'PDSUIDJ1',
    'SYSUIDJ1', 'PMASIDJ1', 'BTANIDJA', 'SYBTIDJ1', 'SUNIIDJA', 'PUBAIDJ1', 'MCORIDJA', 'CITIIDJX', 'DBSBIDJA', 'DEUTIDJA',
    'SYDKIDJ1', 'IBKOIDJA', 'ICBKIDJA', 'INDIIDJA', 'LPEIIDJ1', 'CHASIDJX', 'BBUKIDJA', 'BUSTIDJ1', 'KSEIIDJ1', 'IBBKIDJA',
    'BOTKIDJX', 'SYSBIDJ1', 'NISPIDJA', 'SYONIDJ1', 'ARFAIDJ1', 'SYBBIDJ1', 'ATJSIDJ2', 'SYACIDJ1', 'ANZBIDJX', 'BBLUIDJA',
    'HSBCIDJA', 'ATOSIDJ1', 'NANOIDJ1', 'MEEKIDJ1', 'SYWSIDJ1', 'AWANIDJA', 'BPIAIDJA', 'SSPIIDJA', 'SCBLIDJX', 'FAMAIDJ1',
    'BUTGIDJ1', 'BBIJIDJA'
}


class BCAExtractor(BaseExtractor):

    def get_file_prefix(self) -> str:
        return 'BCA'

    def extract_no_rekening(self) -> str:
        with pdfplumber.open(self.pdf_path) as pdf:
            page = pdf.pages[0]
            text = page.extract_text()
            if text:
                match = re.search(r'NO\.?\s*REKENING\s*:\s*(\d+)', text)
                if match:
                    return match.group(1)
        return 'unknown'

    # ------------------------------------------------------------------ #
    #  SALDO HARIAN                                                        #
    # ------------------------------------------------------------------ #

    def extract_saldo(self) -> dict:
        saldo_per_bulan  = {}
        current_periode  = None
        current_tahun    = None
        saldo_harian     = {}
        saldo_awal_bulan = {}

        # Identitas pemilik rekening (dari halaman pertama)
        nama_pemilik = '-'
        no_rekening  = '-'
        with pdfplumber.open(self.pdf_path) as pdf:
            first_text = pdf.pages[0].extract_text() or ''
            for line in first_text.split('\n'):
                if 'NO. REKENING' in line or 'NO REKENING' in line:
                    match_nama = re.match(
                        r'^(.+?)\s+NO\.?\s*REKENING\s*:\s*(\d+)', line
                    )
                    if match_nama:
                        nama_pemilik = match_nama.group(1).strip()
                        no_rekening  = match_nama.group(2).strip()
                    break

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split('\n')

                # Deteksi periode/bulan
                for line in lines:
                    if 'PERIODE' in line:
                        match = re.search(r'PERIODE\s*:\s*(\w+)\s+(\d{4})', line)
                        if match:
                            new_periode = match.group(1).capitalize()
                            new_tahun   = match.group(2)
                            if (current_periode
                                    and new_periode != current_periode
                                    and saldo_harian):
                                saldo_per_bulan[current_periode] = {
                                    'data': dict(saldo_harian),
                                    'tahun': current_tahun
                                }
                                saldo_harian = {}
                            current_periode = new_periode
                            current_tahun   = new_tahun
                        break

                for line in lines:
                    # Saldo awal bulan
                    if 'SALDO AWAL' in line and current_periode:
                        match_awal = re.search(r'(-?[\d,]+\.\d{2})\s*$', line)
                        if match_awal and current_periode not in saldo_awal_bulan:
                            try:
                                saldo_awal_bulan[current_periode] = int(float(
                                    match_awal.group(1).replace(',', '')
                                ))
                            except Exception:
                                pass

                    # Baris transaksi harian: dd/mm ...
                    date_match = re.match(r'^(\d{2})/(\d{2})\s+', line)
                    if date_match:
                        current_date = f"{date_match.group(1)}/{date_match.group(2)}"
                        saldo_pattern = re.search(r'(-?[\d,]+\.\d{2})\s*$', line)
                        if saldo_pattern:
                            try:
                                saldo_int = int(float(
                                    saldo_pattern.group(1).replace(',', '')
                                ))
                                saldo_harian[current_date] = saldo_int
                            except Exception:
                                pass

            if current_periode and saldo_harian:
                saldo_per_bulan[current_periode] = {
                    'data': dict(saldo_harian),
                    'tahun': current_tahun
                }

        if not saldo_per_bulan:
            return {}

        result = {}
        for bulan, info in saldo_per_bulan.items():
            saldo_dict  = info['data']
            tahun       = info['tahun']
            sorted_dates = sorted(saldo_dict.keys(),
                                  key=lambda x: int(x.split('/')[0]))
            if not sorted_dates:
                continue

            first_day = int(sorted_dates[0].split('/')[0])
            last_day  = int(sorted_dates[-1].split('/')[0])
            month     = sorted_dates[0].split('/')[1]

            # Forward-fill saldo hari libur/weekend
            complete_saldo = {}
            previous_saldo = None
            for day in range(first_day, last_day + 1):
                date_key = f"{day:02d}/{month}"
                if date_key in saldo_dict:
                    complete_saldo[date_key] = saldo_dict[date_key]
                    previous_saldo = saldo_dict[date_key]
                elif previous_saldo is not None:
                    complete_saldo[date_key] = previous_saldo

            data = []
            for date_key in sorted(complete_saldo.keys(),
                                   key=lambda x: int(x.split('/')[0])):
                day, month_num = date_key.split('/')
                data.append({
                    'Bulan': bulan,
                    'Tanggal': int(day),
                    'Saldo Akhir Harian': complete_saldo[date_key]
                })

            result[bulan] = {
                'df': pd.DataFrame(data),
                'tahun': tahun
            }

        # Metadata
        result['_nama_pemilik'] = nama_pemilik
        result['_no_rekening']  = no_rekening
        for bulan, saldo_awal in saldo_awal_bulan.items():
            result[f'_saldo_awal_{bulan}'] = saldo_awal

        return result

    # ------------------------------------------------------------------ #
    #  DETAIL TRANSAKSI                                                    #
    # ------------------------------------------------------------------ #

    def extract_transaksi(self) -> dict:
        transaksi_per_bulan = {}
        current_periode     = None
        current_tahun       = None
        transaksi_list      = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split('\n')

                # Deteksi periode/bulan
                for line in lines:
                    if 'PERIODE' in line:
                        match = re.search(r'PERIODE\s*:\s*(\w+)\s+(\d{4})', line)
                        if match:
                            new_periode = match.group(1).capitalize()
                            new_tahun   = match.group(2)
                            if (current_periode
                                    and new_periode != current_periode
                                    and transaksi_list):
                                transaksi_per_bulan[current_periode] = list(transaksi_list)
                                transaksi_list = []
                            current_periode = new_periode
                            current_tahun   = new_tahun
                        break

                i = 0
                while i < len(lines):
                    line = lines[i]

                    # Lewati baris header/footer/noise
                    if any(skip in line for skip in [
                        'KETERANGAN', 'CBG', 'MUTASI', 'SALDO',
                        'Bersambung', 'HALAMAN', 'CATATAN'
                    ]):
                        i += 1
                        continue

                    if line.strip() == 'TANGGAL' or (
                        line.startswith('TANGGAL') and ':' not in line
                    ):
                        i += 1
                        continue

                    date_match = re.match(r'^(\d{2})/(\d{2})\s+(.+)', line)
                    if date_match and current_periode:
                        tanggal      = int(date_match.group(1))
                        rest_of_line = date_match.group(3).strip()

                        keterangan_lines = [rest_of_line]

                        j = i + 1
                        while j < len(lines):
                            next_line = lines[j].strip()

                            # Artifact page-break: kadang muncul fragmen "TANGGAL :dd/mm"
                            # menempel di depan baris nama/nominal lanjutan (noise dari
                            # header halaman berikutnya yang ikut ter-extract). Buang
                            # fragmennya saja, sisa baris (nama/nominal asli) tetap dipakai.
                            next_line = re.sub(r'^TANGGAL\s*:?\s*\d{2}/\d{2}\s*', '', next_line).strip()

                            # Stop jika baris baru dimulai dengan tanggal
                            if re.match(r'^\d{2}/\d{2}\s+', next_line):
                                break
                            
                            # Stop jika footer/header
                            if any(kw in next_line for kw in [
                                'Bersambung', 'HALAMAN', 'CATATAN',
                                'SALDO AWAL :', 'MUTASI CR :', 'MUTASI DB :', 'SALDO AKHIR :'
                            ]):
                                break
                            
                            # CRITICAL FIX: Cek jika di TENGAH baris ada pattern tanggal (edge case)
                            # Pattern: "NAMA 03/11 TRSF..." → split dan stop
                            if re.search(r'\s+\d{2}/\d{2}\s+(TRSF|KR\s|BI-FAST|SETORAN|TARIKAN|BIAYA)', next_line):
                                # Ada transaksi baru di tengah baris, stop di sini
                                break
                            
                            if next_line:
                                keterangan_lines.append(next_line)
                            j += 1

                        transaksi = self._parse_transaction(
                            keterangan_lines, tanggal, current_periode
                        )
                        if transaksi:
                            transaksi_list.append(transaksi)

                        i = j
                    else:
                        i += 1

            if current_periode and transaksi_list:
                transaksi_per_bulan[current_periode] = list(transaksi_list)

        result = {}
        for bulan, transaksi in transaksi_per_bulan.items():
            if transaksi:
                result[bulan] = pd.DataFrame(transaksi)

        return result

    # ------------------------------------------------------------------ #
    #  HELPER INTERNAL                                                     #
    # ------------------------------------------------------------------ #

    def _parse_transaction(self, lines: list, tanggal: int, bulan: str) -> dict | None:
        if not lines:
            return None

        full_text = ' '.join(lines)
        if 'SALDO AWAL' in full_text.upper():
            return None

        # NOTE: pattern mengharuskan grup digit lengkap (word boundary di kedua sisi)
        # supaya tidak "nyangkut" ke pecahan angka format Eropa (titik ribuan, koma desimal)
        # yang kadang muncul akibat noise/OCR artifact di PDF, mis. "15.840.000,B" —
        # tanpa boundary ini, regex lama bisa salah menangkap "15.84" sebagai nominal.
        nominal_matches = re.findall(r'(?<![\d.])\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)', full_text)
        if not nominal_matches:
            return None

        nominal_str = nominal_matches[0]

        # Tentukan jenis mutasi — HANYA dari baris pertama (header transaksi), bukan
        # dari full_text gabungan. full_text ikut memuat baris nama/keterangan lanjutan,
        # dan scan " DB" di situ bisa salah kena nama seperti "DBS" (Bank DBS Indonesia)
        # atau "S WIDYANINGSIH" -> transaksi Kredit jadi salah tercatat sebagai Debit.
        first_line = lines[0] if lines else ''
        first_line_upper = first_line.upper()

        if 'KR OTOMATIS' in full_text or re.match(r'^KR\b', first_line_upper):
            is_debit = False
        elif re.search(r'\bCR\b', first_line_upper):
            # "TRSF E-BANKING CR", "BI-FAST CR ... DR 002", "SWITCHING CR DR 008", dst.
            # "DR" di baris ini adalah kode bank pengirim, bukan penanda Debit — CR menang.
            is_debit = False
        elif re.search(r'\bDB\b', first_line_upper):
            # "TRSF E-BANKING DB", "SWITCHING DB KE", "DB OTOMATIS B.ADM KLIRING", dst.
            is_debit = True
        else:
            has_tarikan = 'TARIKAN' in first_line_upper
            has_biaya = ('BIAYA ADM' in first_line_upper or 'BIAYA TRANSFER' in first_line_upper or
                         'BIAYA ADMINISTRASI' in first_line_upper or 'BIAYA TXN' in first_line_upper)
            has_pajak = 'PAJAK' in first_line_upper
            is_debit = has_tarikan or has_biaya or has_pajak

        try:
            nominal = int(nominal_str.replace(',', '').split('.')[0])
        except Exception:
            return None

        jenis_mutasi = 'Debit' if is_debit else 'Kredit'
        nama         = self._clean_nama(self._extract_nama(lines))

        keterangan = full_text
        for nom in nominal_matches:
            keterangan = keterangan.replace(nom, '')
        # Hapus token "DB" berdiri sendiri saja (word boundary) — bukan setiap
        # kemunculan substring "DB" (yang bisa memakan nama seperti "DBS" jadi "S").
        keterangan = re.sub(r'\bDB\b', '', keterangan).strip()
        keterangan = ' '.join(keterangan.split())

        return {
            'Bulan': bulan,
            'Tanggal': tanggal,
            'Jenis Mutasi': jenis_mutasi,
            'Mutasi': nominal,
            'Nama Pengirim/Penerima': nama,
            'Keterangan Transaksi': keterangan
        }

    def _clean_nama(self, nama: str) -> str:
        """
        Bersihkan sisa kode/nomor referensi yang kadang ikut terbawa di depan nama,
        misal hasil VA/FTFVA "20239/TIKET KERETA" -> "TIKET KERETA".
        """
        if not nama or nama == '-':
            return nama
        # Buang prefix nomor referensi + slash, mis. "20239/TIKET KERETA"
        cleaned = re.sub(r'^\d{3,}/', '', nama).strip()
        # Buang prefix nominal duplikat "00000.00" yang nempel tanpa spasi ke nama
        # merchant pada transaksi QRIS/kartu debit, mis. "00000.00SPBU 34.42" ->
        # "SPBU 34.42", "00000.006487 HERO" -> "6487 HERO".
        cleaned = re.sub(r'^0+\.00(?=\S)', '', cleaned).strip()
        # Buang sisa pecahan nominal lain (bukan nol) yang nempel langsung ke huruf
        # tanpa spasi, mis. "13917.60WATSONS" -> "WATSONS". Dibatasi ke huruf (bukan
        # digit) di sisi kanan supaya kode toko murni angka seperti "6487 HERO"
        # (sudah dipisah spasi) tidak ikut kepotong.
        cleaned = re.sub(r'^\d+\.\d{2}(?=[A-Za-z])', '', cleaned).strip()
        return cleaned if cleaned else nama

    def _extract_nama(self, lines: list) -> str:
        """Extract nama pengirim/penerima dari lines."""
        if not lines:
            return '-'
        
        full_text = ' '.join(lines)
        
        # === KATA KHUSUS (prioritas tertinggi) ===
        special_keywords = {
            'SHOPEE': 'SHOPEE',
            'TOKOPEDIA': 'TOKOPEDIA',
            'MITSUI': 'MITSUI',
            'BIAYA ADM': 'Biaya Admin',
            'BIAYA ADMINISTRASI': 'Biaya Admin',
            'PAJAK BUNGA': 'Pajak Bunga',
            'BIAYA TRANSFER': 'Biaya Transfer',
            'PENERIMAAN NEGARA': 'Penerimaan Negara',
            'BPJS': 'BPJS',
        }
        
        for kw, result in special_keywords.items():
            if kw in full_text.upper():
                return result

        if 'BUNGA' in full_text.upper() and 'PAJAK' not in full_text.upper():
            return 'Bunga'
        
        # === ROUTING CODE RULE (High Priority) ===
        # Jika mengandung salah satu routing code, nama adalah baris setelahnya
        for i in range(len(lines) - 1):
            line_upper = lines[i].strip().upper()
            if any(code in line_upper for code in ROUTING_CODES):
                # Ambil baris berikutnya sebagai nama
                next_line = lines[i+1].strip()
                if next_line:
                    return ' '.join(next_line.split()[:4])
        
        first_line = lines[0]
        
        # --- Type: SETORAN ---
        if 'SETORAN' in first_line:
            if 'KLIRING' in first_line and len(lines) > 1:
                nama_line = lines[1].strip()
                if not self._is_junk_line(nama_line):
                    return ' '.join(nama_line.split()[:4])
                return 'Kliring Masuk'
            return 'Setoran'
        
        # --- Type: TARIKAN ---
        if 'TARIKAN' in first_line:
            if 'TUNAI' in first_line:
                return 'Tarikan Tunai'
            # Look for name in subsequent lines (last non-junk)
            for line in reversed(lines[1:]):
                if not self._is_junk_line(line):
                    return ' '.join(line.strip().split()[:4])
            return 'Tarikan'
        
        # --- Type: SWITCHING / RTGS / LLG ---
        if 'SWITCHING CR' in first_line:
            if len(lines) > 1:
                for line in lines[1:]:
                    if not self._is_junk_line(line):
                        return ' '.join(line.strip().split()[:4])
        
        if 'KR OTOMATIS' in first_line:
            # RTGS (usually line 3), LLG (usually line 2)
            candidates = [
                line.strip() for line in lines[1:]
                if not self._is_junk_line(line) and 'Clearing' not in line
            ]
            if candidates:
                # Format "NTRF@..." menyusun baris sebagai [catatan, ..., kode
                # pengirim singkat] — biasanya semua baris berawalan "@", tapi
                # kadang baris terbungkus (word-wrap) menyisakan awalan huruf
                # nyasar sebelum "@", mis. "i @AFR" (dari ".../7 Jul" + "i").
                # Kalau baris PERTAMA berupa catatan ("@Lunas...", "@Pengeras
                # beton"), kode pengirim singkat ada di baris TERAKHIR — ambil
                # token setelah "@" di baris itu saja, buang awalan nyasarnya.
                if candidates[0].startswith('@'):
                    last = candidates[-1]
                    at_token = re.search(r'@(\S+)\s*$', last)
                    kode = at_token.group(1) if at_token else last.lstrip('@')
                    return ' '.join(kode.split()[:4])
                return ' '.join(candidates[0].split()[:4])

        # --- Type: TRSF / BI-FAST (The most complex) ---
        # User guideline: Name is usually on the LAST or 2nd to LAST line.
        # We search from the bottom and skip "junk" (numbers, platform markers)
        candidate_lines = []
        for line in reversed(lines):
            line_clean = line.strip()
            
            # Skip noise
            if self._is_junk_line(line_clean):
                continue
            
            # Additional check for generic descriptions that start the transaction block
            # (Don't pick the header line even if it's not strictly "junk")
            if any(x in line_clean.upper() for x in ['TRSF E-BANKING', 'BI-FAST', 'BIF TRANSFER']):
                continue

            candidate_lines.append(line_clean)
            if len(candidate_lines) >= 1: # We usually just need the first non-junk from the bottom
                break
        
        if candidate_lines:
            # Always respect the 4-word rule as requested
            return ' '.join(candidate_lines[0].split()[:4])

        # Fallback for E-BANKING specific markers
        if 'PYBCA' in full_text or '/PYBCA/' in full_text:
            return 'Pembayaran'

        return '-'

    def _is_junk_line(self, line: str) -> bool:
        """Identify if a line is a transaction code, number, or platform marker rather than a name."""
        lc = line.strip()
        if not lc or len(lc) < 3:
            return True
        
        # User requested exclusions
        if lc in ['KBB']:
            return True

        # Nama channel/aplikasi mobile banking BCA — selalu baris penutup
        # transaksi (mis. setelah nama pengirim/penerima "PRIHANTARA"),
        # bukan bagian dari nama. Dicek case-insensitive & exact match
        # karena beberapa varian ejaan pernah dipakai BCA dari waktu ke
        # waktu: "MyBCA", "myBCA", "M-BCA" (versi lama), "KlikBCA".
        if lc.upper() in ['MYBCA', 'M-BCA', 'MBCA', 'KLIKBCA']:
            return True


        # Common technical markers in BCA PDFs
        # Added more patterns based on analysis (WSID, FTFVA, FTSCY, etc.)
        junk_markers = [
            'CBG:', 'REG:', 'WSID:', 'FTFVA/', 'FTSCY/', 'ADSCY/', 'ZDW',
            'WS95', 'Clearing', 'TANGGAL:', 'TANGGAL :', 'Hal:', '/Web', '/NEW BRI',
            '#WARKAT',
        ]
        if any(marker in lc for marker in junk_markers):
            return True

        # Referensi akun/VA style "12345678@BCA26060822196" atau "lA0@BCA26051765988"
        # (prefix bisa digit/huruf, kadang salah baca OCR) — kode notifikasi
        # transfer otomatis, bukan nama.
        if re.match(r'^\w+@[A-Z]{2,}\d+$', lc.upper()):
            return True

        # Kode channel/cabang berawalan "/", mis. "/KBB", "/Web", "/NEW BRI",
        # "/BTNMobile", "/SMB" — selalu baris penutup transaksi SWITCHING, bukan
        # nama. Nama sebenarnya ada di baris SEBELUM kode channel ini.
        if lc.startswith('/'):
            return True

        # Check against ROUTING_CODES as well
        if any(code in lc.upper() for code in ROUTING_CODES):
            return True

        # Pure numeric strings (Phone numbers, VA numbers, policy numbers, or amounts)
        # Matches strings with only digits, spaces, dots, commas, or dashes
        if re.match(r'^[0-9\s\.\,\-]+$', lc):
            return True
            
        # Dates (dd/mm or dd/mm/yy)
        if re.match(r'^\d{2}/\d{2}(/\d{2,4})?$', lc):
            return True
            
        return False
