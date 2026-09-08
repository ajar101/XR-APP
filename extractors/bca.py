"""
bca.py — Extractor khusus rekening koran Bank BCA.

Mengimplementasikan BaseExtractor dengan logika parsing format PDF BCA:
  - Rekening Giro & Tabungan BCA
  - Support multi-bulan dalam satu PDF
  - Deteksi nama pengirim/penerima dari berbagai format transaksi BCA
    (BI-FAST, RTGS, LLG, SWITCHING CR, KR OTOMATIS, TRSF E-BANKING, dll.)
"""

import re
import calendar
import datetime
import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor

BULAN_TO_NUM = {
    'Januari': 1, 'Februari': 2, 'Maret': 3, 'April': 4, 'Mei': 5, 'Juni': 6,
    'Juli': 7, 'Agustus': 8, 'September': 9, 'Oktober': 10, 'November': 11,
    'Desember': 12,
}

# Ketentuan jadwal pendebetan BIAYA ADM di BCA. Sebelumnya konstanta ini
# tinggal di engine/anomaly_detector.py, yang membuat engine "tahu" aturan
# satu bank tertentu dan diam-diam memberlakukannya juga ke bank lain.
# Tempatnya di sini: aturan BCA dimiliki extractor BCA.
#
# Mulai periode Juni 2026, SEMUA jenis rekening BCA (Giro maupun Tabungan)
# didebet tanggal 1. Sebelum itu jadwalnya beda per jenis rekening
# (dikonfirmasi dari data riil):
#   - GIRO    : tanggal terakhir bulan berjalan
#   - TAHAPAN : Jumat minggu ke-3 bulan berjalan
BIAYA_ADM_CUTOVER = (2026, 6)   # (tahun, bulan) mulai berlaku aturan baru

# Nilai kolom 'Nama Pengirim/Penerima' yang dipakai extractor ini untuk baris
# BIAYA ADM. Harus sama persis dengan yang dihasilkan _extract_nama().
LABEL_BIAYA_ADMIN = 'Biaya Admin'

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

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Hasil pemindaian transaksi + jejak cetaknya, di-cache supaya PDF
        # tidak dibaca ulang oleh extract_transaksi() dan _provenance().
        self._cache_tx = None

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
        # Jenis rekening ("REKENING GIRO" / "REKENING TAHAPAN") — baris
        # pertama halaman pertama. Dipakai anomaly_detector untuk aturan
        # yang beda antar jenis rekening, mis. jadwal BIAYA ADM.
        jenis_rekening = '-'
        with pdfplumber.open(self.pdf_path) as pdf:
            first_text = pdf.pages[0].extract_text() or ''
            first_lines = first_text.split('\n')
            if first_lines:
                match_jenis = re.match(r'^REKENING\s+(\w+)', first_lines[0].strip().upper())
                if match_jenis:
                    jenis_rekening = match_jenis.group(1)
            for line in first_lines:
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
        result['_nama_pemilik']   = nama_pemilik
        result['_no_rekening']    = no_rekening
        result['_jenis_rekening'] = jenis_rekening
        result['_provenance'] = self._provenance()
        result['_checksum'] = self._checksum()
        for bulan, saldo_awal in saldo_awal_bulan.items():
            result[f'_saldo_awal_{bulan}'] = saldo_awal

        # Ketentuan BCA yang tidak boleh diketahui engine (lihat base.py).
        jadwal = {}
        for bulan, info in result.items():
            if bulan.startswith('_') or not isinstance(info, dict):
                continue
            entri = self._jadwal_biaya_admin(jenis_rekening, bulan, info.get('tahun'))
            if entri:
                jadwal[bulan] = entri
        if jadwal:
            result['_biaya_admin'] = {'label_nama': LABEL_BIAYA_ADMIN,
                                      'jadwal': jadwal}

        # Baris bunga & pajak bunga BCA dicetak dengan keterangan persis ini.
        # Dicocokkan persis (bukan "mengandung") supaya transaksi tak terkait
        # seperti "KARANGAN BUNGA" tidak ikut terhitung.
        result['_bunga_pajak'] = {'kolom': 'Keterangan Transaksi',
                                  'bunga': ['BUNGA'], 'pajak': ['PAJAK BUNGA']}

        return result

    # ------------------------------------------------------------------ #
    #  KETENTUAN BCA — JADWAL BIAYA ADMIN                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hari_jumat_minggu_ke3(tahun: int, bulan: int):
        """Tanggal Jumat ke-3 di bulan itu, atau None kalau bulan tidak valid."""
        try:
            _, ndays = calendar.monthrange(tahun, bulan)
        except (calendar.IllegalMonthError, ValueError):
            return None
        jumat = [d for d in range(1, ndays + 1)
                 if datetime.date(tahun, bulan, d).weekday() == 4]
        return jumat[2] if len(jumat) >= 3 else None

    def _jadwal_biaya_admin(self, jenis_rekening: str, bulan: str, tahun) -> dict | None:
        """
        Tanggal seharusnya BIAYA ADM didebet pada bulan itu + penjelasannya.

        Mengembalikan None kalau aturannya tidak bisa ditentukan (jenis
        rekening tak dikenal atau tahun/bulan tidak valid) — dengan begitu
        engine melewatkan pemeriksaannya alih-alih menebak.
        """
        bulan_num = BULAN_TO_NUM.get(bulan)
        try:
            tahun = int(tahun)
        except (TypeError, ValueError):
            return None
        if not bulan_num:
            return None

        if jenis_rekening not in ('GIRO', 'TAHAPAN'):
            # Aturan cutover pun tidak diberlakukan di sini: kalau jenis
            # rekeningnya sendiri tidak terbaca, kita tidak tahu ini rekening
            # BCA jenis apa — menebaknya justru menghasilkan temuan palsu.
            return None

        if (tahun, bulan_num) >= BIAYA_ADM_CUTOVER:
            return {'tanggal': 1,
                    'aturan': 'tanggal 1 (ketentuan BCA per Juni 2026)'}

        if jenis_rekening == 'GIRO':
            try:
                _, ndays = calendar.monthrange(tahun, bulan_num)
            except (calendar.IllegalMonthError, ValueError):
                return None
            return {'tanggal': ndays,
                    'aturan': f'akhir bulan/tanggal {ndays} (ketentuan BCA GIRO)'}

        tanggal = self._hari_jumat_minggu_ke3(tahun, bulan_num)
        if tanggal is None:
            return None
        return {'tanggal': tanggal,
                'aturan': f'Jumat minggu ke-3/tanggal {tanggal} (ketentuan BCA TAHAPAN)'}

    # ------------------------------------------------------------------ #
    #  DETAIL TRANSAKSI                                                    #
    # ------------------------------------------------------------------ #

    def extract_transaksi(self) -> dict:
        hasil = self._scan_transaksi()['per_bulan']
        return {bulan: pd.DataFrame(rows) for bulan, rows in hasil.items() if rows}

    def _provenance(self) -> dict:
        """
        Jejak cetak dokumen (lihat kontrak '_provenance' di extractors/base.py).

        'teks_mentah' DIKIRIM untuk BCA karena baris keterangannya adalah teks
        cetak mesin — angka berformat Eropa di situ memang artefak, bukan
        berita bebas yang ditulis nasabah.
        """
        d = self._scan_transaksi()
        return {'halaman': d['halaman'], 'baris': d['baris']}

    def _scan_transaksi(self) -> dict:
        if self._cache_tx is not None:
            return self._cache_tx

        transaksi_per_bulan = {}
        current_periode     = None
        current_tahun       = None
        transaksi_list      = []
        halaman             = []     # jejak cetak per halaman
        prov_baris          = []     # jejak cetak per baris
        ringkasan           = {}     # angka resmi per periode, dari kaki laporan

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

                # Kaki laporan tiap periode memuat angka resmi bank:
                #   SALDO AWAL : ... | MUTASI CR : ... 59 | MUTASI DB : ... 504
                #   SALDO AKHIR : ...
                # Dipakai sebagai checksum — sebelumnya angka ini dibaca ulang
                # oleh engine dengan parser keduanya sendiri.
                if current_periode:
                    r = ringkasan.setdefault(current_periode, {})
                    for kunci, pola in (
                        ('opening', r'SALDO AWAL\s*:\s*(-?[\d,]+\.\d{2})'),
                        ('closing', r'SALDO AKHIR\s*:\s*(-?[\d,]+\.\d{2})'),
                    ):
                        m = re.search(pola, text)
                        if m and kunci not in r:
                            r[kunci] = float(m.group(1).replace(',', ''))
                    for kunci_rp, kunci_n, pola in (
                        ('total_credit', 'n_credit',
                         r'MUTASI CR\s*:\s*(-?[\d,]+\.\d{2})\s+(\d+)'),
                        ('total_debit', 'n_debit',
                         r'MUTASI DB\s*:\s*(-?[\d,]+\.\d{2})\s+(\d+)'),
                    ):
                        m = re.search(pola, text)
                        if m and kunci_rp not in r:
                            r[kunci_rp] = float(m.group(1).replace(',', ''))
                            r[kunci_n] = int(m.group(2))

                # "HALAMAN : 2 /42" di kepala halaman.
                m_hal = re.search(r'HALAMAN\s*:\s*(\d+)\s*/\s*(\d+)', text)
                halaman.append({
                    'urut': page.page_number,
                    'no_tercetak': int(m_hal.group(1)) if m_hal else None,
                    'total_tercetak': int(m_hal.group(2)) if m_hal else None,
                    'periode': current_periode,
                    # BCA mencetak baris header kolom di setiap halaman.
                    'ada_header_kolom': any(
                        'TANGGAL' in l and 'KETERANGAN' in l and 'SALDO' in l
                        for l in lines
                    ),
                    'jumlah_baris': 0,    # diisi setelah baris halaman ini selesai
                })
                urut_baris = 0

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
                            jejak = transaksi.pop('_jejak', None)
                            transaksi_list.append(transaksi)
                            if jejak is not None:
                                prov_baris.append({
                                    'bulan': current_periode,
                                    'tanggal': tanggal,
                                    'halaman': page.page_number,
                                    'urut': urut_baris,
                                    'periode': current_periode,
                                    'mutasi': jejak['mutasi'],
                                    'saldo_tercetak': jejak['saldo_tercetak'],
                                    'teks_mentah': line,
                                })
                                urut_baris += 1
                                halaman[-1]['jumlah_baris'] += 1

                        i = j
                    else:
                        i += 1

            if current_periode and transaksi_list:
                transaksi_per_bulan[current_periode] = list(transaksi_list)

        self._cache_tx = {'per_bulan': transaksi_per_bulan, 'halaman': halaman,
                          'baris': prov_baris, 'ringkasan': ringkasan}
        return self._cache_tx

    def _checksum(self) -> list:
        """
        Cocokkan hasil parsing dengan angka resmi di kaki laporan tiap periode
        (SALDO AWAL / MUTASI CR / MUTASI DB / SALDO AKHIR).

        Bentuknya mengikuti metadata '_checksum' di extractors/base.py, sama
        seperti yang dikirim extractor Mandiri, supaya engine memeriksanya
        dengan cara yang sama untuk semua bank.
        """
        d = self._scan_transaksi()
        out = []
        for bulan, rows in d['per_bulan'].items():
            resmi = d['ringkasan'].get(bulan)
            if not resmi:
                continue
            debit = [r for r in rows if r['Jenis Mutasi'] == 'Debit']
            kredit = [r for r in rows if r['Jenis Mutasi'] == 'Kredit']
            saldo = [b['saldo_tercetak'] for b in d['baris']
                     if b['bulan'] == bulan and b['saldo_tercetak'] is not None]
            out.append({
                'label': bulan,
                'bulan': bulan,
                # Nominal BCA disimpan dibulatkan ke rupiah penuh
                # (int(...) di _parse_transaction), jadi TOTAL-nya bisa
                # melenceng sampai Rp1 per transaksi dari angka resmi yang
                # bersen. Toleransinya dinyatakan eksplisit sebesar jumlah
                # transaksi periode itu — bukan angka karangan, melainkan
                # batas atas selisih pembulatan. Jumlah transaksinya sendiri
                # tetap dicocokkan persis.
                'toleransi': max(100.0, float(len(rows))),
                'expected': {
                    'n_debit': resmi.get('n_debit'),
                    'n_credit': resmi.get('n_credit'),
                    'total_debit': resmi.get('total_debit'),
                    'total_credit': resmi.get('total_credit'),
                    'closing': resmi.get('closing'),
                },
                'actual': {
                    'n_debit': len(debit),
                    'n_credit': len(kredit),
                    'total_debit': float(sum(r['Mutasi'] for r in debit)),
                    'total_credit': float(sum(r['Mutasi'] for r in kredit)),
                    'closing': saldo[-1] if saldo else None,
                },
            })
        return out

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

        # Saldo berjalan yang TERCETAK di baris ini. BCA hanya mencetaknya di
        # sebagian baris (nominal kedua), jadi ketiadaannya normal — None,
        # bukan nol. Dipakai lewat metadata '_provenance' untuk memeriksa
        # rantai saldo antar baris.
        #
        # Diambil dari BARIS PERTAMA saja, bukan dari teks gabungan seluruh
        # baris transaksi: baris lanjutan (nama, keterangan) bisa memuat
        # token yang berbentuk nominal — mis. "... TGL: 09/06 ... 34.42" —
        # dan kalau ikut terbaca, angka itu dikira saldo berjalan lalu
        # seluruh rantai saldo dilaporkan meleset.
        baris_pertama = re.findall(
            r'(?<![\d.])\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)', lines[0]
        )
        saldo_tercetak = None
        if len(baris_pertama) > 1:
            try:
                saldo_tercetak = float(baris_pertama[1].replace(',', ''))
            except ValueError:
                saldo_tercetak = None

        return {
            'Bulan': bulan,
            'Tanggal': tanggal,
            'Jenis Mutasi': jenis_mutasi,
            'Mutasi': nominal,
            'Nama Pengirim/Penerima': nama,
            'Keterangan Transaksi': keterangan,
            # Dikeluarkan pemanggil sebelum masuk DataFrame — bukan kolom
            # laporan, melainkan bahan metadata '_provenance'.
            '_jejak': {
                'mutasi': (-nominal if is_debit else nominal),
                'saldo_tercetak': saldo_tercetak,
            },
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
