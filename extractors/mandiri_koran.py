"""
mandiri_koran.py — Extractor rekening Bank Mandiri format "Laporan Rekening
Koran" (Account Statement Report).

Format ini BEDA dari Kopra maupun e-Statement, walau sama-sama Mandiri:
tata letaknya memakai TABEL BERGARIS (setiap sel digambar sebagai kotak),
tanggalnya berformat "05/06/2025 08:32:47", dan ringkasan resminya dipecah
menjadi kepala laporan (Account No / Period / Opening Balance) dan kaki
laporan (No of Debit ... Closing Balance).

Pendekatan parsing:
  1. Baris tabel diambil dari GARIS SELNYA, bukan dari koordinat kata.
     PDF format ini menggambar setiap sel sebagai kotak (page.rects), jadi
     batas antar-transaksi sudah tersedia pasti dari dokumennya sendiri —
     tidak perlu menebak lewat titik tengah antar tanggal seperti pada
     Kopra. Ini penting karena satu transaksi bisa memakan 1–6 baris teks
     dan tinggi barisnya berubah-ubah, sementara kolom Remark rutin memuat
     teks yang menjorok ke atas melewati baris tanggalnya.
  2. Satu PDF bisa memuat beberapa laporan bulanan yang digabung. Setiap
     laporan punya kepala sendiri; baris transaksi dimiliki oleh kepala
     laporan TERAKHIR sebelum halaman itu — bukan ditebak dari bulannya.
  3. Halaman yang tidak punya tabel bergaris Rekening Koran (mis. PDF bank
     lain yang ikut tergabung dalam satu file) dilewati dan DILAPORKAN
     sebagai peringatan — bukan dibuang diam-diam.

Nama lawan transaksi memakai pipeline bersama di extractors/mandiri_nama.py:
kolom Remark format ini berasal dari mesin pembukuan yang sama dengan Kopra,
sehingga tata bahasanya identik ("MCM InhouseTrf KE <NAMA>", "Biaya Adm
<cabang>", dst).

Extractor ini hanya menghasilkan data mentah sesuai kontrak BaseExtractor —
tidak tahu apa pun soal Excel/styling.
"""

import re
from datetime import date, timedelta

import pdfplumber
import pandas as pd

from extractors.base import BaseExtractor
from extractors.mandiri_nama import extract_nama
from extractors.mandiri_kopra import (
    BUNGA_PAJAK_MANDIRI,
    jadwal_biaya_admin_mandiri,
)

BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]

# Singkatan bulan Inggris (dipakai kepala laporan Rekening Koran) -> nomor bulan
BULAN_EN = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}

# Nominal Rekening Koran berformat Inggris: "1,234,567.89".
AMOUNT_RE = re.compile(r'^-?[\d,]+\.\d{2}$')

# Tanggal posting: "05/06/2025" (jamnya menyusul di baris berikutnya dalam
# sel yang sama, jadi tidak ikut jadi syarat kecocokan).
TANGGAL_RE = re.compile(r'\b(\d{2})/(\d{2})/(\d{4})\b')

# Urutan kolom tabel — tetap secara struktur: setiap baris digambar sebagai
# enam sel berurutan dari kiri ke kanan.
KOLOM = ('tanggal', 'remark', 'reference', 'debit', 'credit', 'balance')

# Penanda halaman milik format ini.
PENANDA_KORAN = 'Laporan Rekening Koran'


class MandiriKoranExtractor(BaseExtractor):

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Peringatan yang terkumpul selama parsing (dibaca app.py / pemanggil).
        self.warnings: list[str] = []
        self._cache = None

    def get_file_prefix(self) -> str:
        return 'MANDIRI'

    # ------------------------------------------------------------------ #
    #  PEMBACAAN BARIS DARI GARIS TABEL                                  #
    # ------------------------------------------------------------------ #

    def _kolom_halaman(self, page) -> list | None:
        """
        Enam rentang-x kolom tabel pada halaman ini, urut kiri ke kanan.

        Diambil dari susunan kotak yang PALING SERING muncul utuh, bukan dari
        semua kotak yang ada: halaman awal laporan juga menggambar bingkai
        kotak Opening Balance yang bukan bagian tabel.

        Mengembalikan None kalau halaman ini tidak punya satu pun baris tabel
        yang utuh (mis. halaman dari PDF bank lain yang ikut tergabung).
        """
        bands: dict[tuple, set] = {}
        for r in page.rects:
            kunci = (round(r['top'], 1), round(r['bottom'], 1))
            # Sebagian PDF menggambar bingkai sel dua kali (isi + garis);
            # kotak kembar dibuang supaya jumlah sel per baris tetap enam.
            bands.setdefault(kunci, set()).add(
                (round(r['x0'], 1), round(r['x1'], 1))
            )

        hitung: dict[frozenset, int] = {}
        for kotak in bands.values():
            if len(kotak) == len(KOLOM):
                hitung[frozenset(kotak)] = hitung.get(frozenset(kotak), 0) + 1
        if not hitung:
            return None
        return sorted(max(hitung.items(), key=lambda kv: kv[1])[0])

    def _page_fragments(self, page) -> dict:
        """
        Pecah satu halaman menjadi baris tabel + potongan baris yang terpenggal
        batas halaman.

        Batas baris diambil dari kotak KOLOM TANGGAL, bukan dari kesamaan tepi
        atas-bawah seluruh sel. Satu transaksi yang kena batas halaman dicetak
        sebagai sel-sel dengan tepi bawah berbeda (sel tanggal ikut terpotong
        garis bawah halaman, sel Remark tidak), sehingga pengelompokan lewat
        (atas, bawah) memecah baris itu jadi dua dan salah satunya — berikut
        nominalnya — hilang dari hasil. Sisa selnya muncul sebagai sel tanpa
        kotak tanggal di halaman sebelah; itu yang dikembalikan sebagai
        'atas'/'bawah' untuk disambung pemanggil.

        Mengembalikan {'rows': [...], 'atas': {kolom: teks}, 'bawah': {...}}.
        """
        kosong = {'rows': [], 'atas': {}, 'bawah': {}}
        kolom = self._kolom_halaman(page)
        if not kolom:
            return kosong

        words = page.extract_words()
        if not words:
            return kosong

        # Kata ditempatkan ke sel lewat TITIK TENGAHnya, bukan tepi kiri/atas:
        # glyph bisa sedikit melewati garis sel tanpa berarti pindah kolom.
        titik = [
            (w, (w['x0'] + w['x1']) / 2, (w['top'] + w['bottom']) / 2)
            for w in words
        ]

        def teks_pada(y0, y1, hanya_kolom=None) -> dict:
            isi = {k: [] for k in KOLOM}
            dalam = [(w, cx) for (w, cx, cy) in titik if y0 <= cy < y1]
            for w, cx in sorted(dalam, key=lambda t: (round(t[0]['top'], 1),
                                                      t[0]['x0'])):
                for nama, (x0, x1) in zip(KOLOM, kolom):
                    if hanya_kolom is not None and nama != hanya_kolom:
                        continue
                    if x0 <= cx < x1:
                        isi[nama].append(w['text'])
                        break
            return {k: ' '.join(v).strip() for k, v in isi.items() if v}

        # Baris = kotak pada kolom tanggal (kolom paling kiri).
        x_tanggal = kolom[0]
        pita = sorted({
            (round(r['top'], 1), round(r['bottom'], 1)) for r in page.rects
            if (round(r['x0'], 1), round(r['x1'], 1)) == x_tanggal
        })
        if not pita:
            return kosong

        rows = []
        for urut, (top, bottom) in enumerate(pita):
            teks = teks_pada(top, bottom)
            rows.append({'teks': teks, 'urut': urut, 'top': top})

        # Sel yatim: kotak kolom tabel yang tidak terpayungi kotak tanggal mana
        # pun — sisa transaksi yang terpenggal batas halaman.
        atas, bawah = {}, {}
        batas_atas, batas_bawah = pita[0][0], pita[-1][1]
        for r in page.rects:
            xr = (round(r['x0'], 1), round(r['x1'], 1))
            if xr not in kolom:
                continue        # bukan kolom tabel (mis. kotak Opening Balance)
            nama = KOLOM[kolom.index(xr)]
            top, bottom = round(r['top'], 1), round(r['bottom'], 1)
            pusat = (top + bottom) / 2
            if any(a <= pusat < b for a, b in pita):
                continue
            sasaran = atas if pusat < batas_atas else (bawah if pusat >= batas_bawah else None)
            if sasaran is None:
                continue
            for k, v in teks_pada(top, bottom, hanya_kolom=nama).items():
                sasaran[k] = f'{sasaran[k]} {v}'.strip() if k in sasaran else v

        return {'rows': rows, 'atas': atas, 'bawah': bawah}

    def _jadikan_baris(self, mentah: dict) -> dict | None:
        """
        Ubah teks per kolom menjadi satu transaksi.

        Mengembalikan None untuk baris yang bukan transaksi — baris kepala
        tabel ("Posting Date | Remark | ...") ikut punya kotak sel, tapi sel
        tanggalnya tidak memuat tanggal.
        """
        teks = mentah['teks']
        m = TANGGAL_RE.search(teks.get('tanggal', ''))
        if not m:
            return None
        hari, bulan, tahun = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= bulan <= 12 and 1 <= hari <= 31):
            return None

        return {
            'day': hari,
            'month': bulan,
            'year': tahun,
            'remark': ' '.join(teks.get('remark', '').split()),
            'reference': teks.get('reference', ''),
            'debit': self._parse_amount(teks.get('debit')) or 0,
            'credit': self._parse_amount(teks.get('credit')) or 0,
            'balance': self._parse_amount(teks.get('balance')),
            'urut': mentah['urut'],
            'page': mentah['page'],
            'period_idx': mentah['period_idx'],
        }

    @staticmethod
    def _sambung(mentah: dict, potongan: dict, di_depan: bool) -> None:
        """Sambungkan potongan baris yang terpenggal batas halaman."""
        for k, v in potongan.items():
            lama = mentah['teks'].get(k, '')
            if not lama:
                mentah['teks'][k] = v
            else:
                mentah['teks'][k] = f'{v} {lama}' if di_depan else f'{lama} {v}'

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

        periods = []    # {'start','end','opening','closing','n_debit',...}
        mentah = []     # baris tabel dalam bentuk teks per kolom
        meta = {'no_rekening': 'unknown', 'nama_pemilik': '-'}
        halaman_asing = []
        # Potongan baris di kaki halaman sebelumnya, menunggu baris pertama
        # halaman berikutnya (transaksi yang terpenggal batas halaman).
        tertunda = {}

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''

                kepala = self._parse_kepala(text)
                kaki = self._parse_kaki(text)
                ada_kaki = kaki is not None

                # Kepala laporan menandai awal satu periode. Kaki laporan
                # (Closing Balance dst) menutup periode yang SEDANG berjalan
                # — kecuali kalau kaki itu tercetak di ATAS kepala pada
                # halaman yang sama, yang berarti ia menutup periode
                # sebelumnya.
                kaki_untuk_sebelumnya = (
                    kaki and kepala
                    and self._y_teks(page, 'Closing') is not None
                    and self._y_teks(page, 'Period') is not None
                    and self._y_teks(page, 'Closing') < self._y_teks(page, 'Period')
                )
                if kaki and kaki_untuk_sebelumnya and periods:
                    periods[-1].update(kaki)
                    kaki = None

                if kepala:
                    periods.append({
                        'start': kepala['start'], 'end': kepala['end'],
                        'opening': kepala['opening'],
                        'closing': None, 'n_debit': None, 'n_credit': None,
                        'total_debit': None, 'total_credit': None,
                        'page': page.page_number,
                    })
                    if meta['no_rekening'] == 'unknown' and kepala.get('no_rekening'):
                        meta['no_rekening'] = kepala['no_rekening']
                        meta['nama_pemilik'] = kepala.get('nama_pemilik') or '-'

                frag = self._page_fragments(page)
                page_rows = frag['rows']
                for r in page_rows:
                    r['page'] = page.page_number
                    r['period_idx'] = len(periods) - 1

                # Potongan di kepala halaman menyambung baris terakhir halaman
                # sebelumnya; kalau halaman sebelumnya tidak menghasilkan baris
                # apa pun, ia justru pembuka baris pertama halaman ini.
                if frag['atas']:
                    if mentah:
                        self._sambung(mentah[-1], frag['atas'], di_depan=False)
                    elif page_rows:
                        self._sambung(page_rows[0], frag['atas'], di_depan=True)
                if tertunda:
                    if page_rows:
                        self._sambung(page_rows[0], tertunda, di_depan=True)
                    elif mentah:
                        self._sambung(mentah[-1], tertunda, di_depan=False)
                    tertunda = {}

                mentah.extend(page_rows)
                tertunda = frag['bawah']

                if kaki and periods:
                    periods[-1].update(kaki)

                # Halaman berisi teks tapi bukan bagian laporan ini: PDF
                # gabungan yang keliru (mis. rekening bank lain ikut
                # ter-merge). Dicatat, bukan dibuang diam-diam.
                #
                # Halaman yang hanya memuat kaki laporan (ringkasan yang
                # tumpah ke halaman sendiri) TIDAK termasuk: isinya terpakai
                # penuh sebagai angka checksum walau tanpa satu pun baris.
                if not page_rows and not kepala and not ada_kaki and text.strip() \
                        and PENANDA_KORAN not in text:
                    halaman_asing.append(page.page_number)

        rows = []
        for m in mentah:
            r = self._jadikan_baris(m)
            if r is None:
                continue          # baris kepala tabel
            r['date'] = self._tanggal_aman(r)
            rows.append(r)

        if halaman_asing:
            self.warnings.append(
                f"{len(halaman_asing)} halaman tidak dikenali sebagai Laporan "
                f"Rekening Koran Mandiri dan tidak ikut diekstrak "
                f"(halaman {self._ringkas_halaman(halaman_asing)}). Pastikan "
                f"PDF yang diunggah hanya berisi rekening yang diperiksa."
            )

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
                if prev is not None:
                    k = k + (r['page'], len(seen))
                seen[k] = r
            else:
                # Blok berbeda dengan isi identik: laporan terbaru menang.
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

    # ------------------------------------------------------------------ #
    #  KEPALA & KAKI LAPORAN                                             #
    # ------------------------------------------------------------------ #

    def _parse_kepala(self, text: str) -> dict | None:
        """
        Ambil identitas + rentang periode + Opening Balance dari kepala laporan:

            Account No 1820015423288 IDR PT KARO NUSANTARA LO PT KARO NUSANTARA LO
            Period 01 Jun 2025 - 30 Jun 2025
            Currency IDR
            Branch AREA BANDUNG ASIA-AFRIKA
            Opening Balance
            160,718,073.35

        Mengembalikan None kalau halaman ini bukan awal sebuah laporan.
        """
        if 'Period' not in text:
            return None
        m = re.search(
            r'Period\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s*-\s*'
            r'(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})',
            text,
        )
        if not m or m.group(2) not in BULAN_EN or m.group(5) not in BULAN_EN:
            return None

        out = {
            'start': date(int(m.group(3)), BULAN_EN[m.group(2)], int(m.group(1))),
            'end':   date(int(m.group(6)), BULAN_EN[m.group(5)], int(m.group(4))),
            'opening': None,
        }

        # Opening Balance dicetak di baris SETELAH labelnya (kotak tersendiri).
        m = re.search(r'Opening Balance\s*\n?\s*(-?[\d,]+\.\d{2})', text)
        if m:
            out['opening'] = self._parse_amount(m.group(1))

        # "Account No <no> <mata uang> <nama> <alias>" — nama dan aliasnya
        # sering identik, jadi bagian yang berulang dibuang.
        m = re.search(r'Account No\s+(\d{6,})\s+([A-Z]{3})\s+(.+)', text)
        if m:
            out['no_rekening'] = m.group(1)
            kata = ' '.join(m.group(3).split()).split()
            separuh = len(kata) // 2
            if separuh and kata[:separuh] == kata[separuh:]:
                kata = kata[:separuh]
            out['nama_pemilik'] = ' '.join(kata).strip() or '-'
        return out

    def _parse_kaki(self, text: str) -> dict | None:
        """
        Ambil angka resmi dari kaki laporan:

            No of Debit 5
            Total Amount Debited 27,003,772.71
            No of Credit 2
            Total Amount Credited 42,269,501.54
            Closing Balance 175,983,802.18
        """
        if 'Closing Balance' not in text:
            return None

        def angka(pola, ubah):
            m = re.search(pola, text)
            return ubah(m.group(1)) if m else None

        out = {
            'n_debit':      angka(r'No of Debit\s+(\d+)', int),
            'n_credit':     angka(r'No of Credit\s+(\d+)', int),
            'total_debit':  angka(r'Total Amount Debited\s+(-?[\d,]+\.\d{2})',
                                  self._parse_amount),
            'total_credit': angka(r'Total Amount Credited\s+(-?[\d,]+\.\d{2})',
                                  self._parse_amount),
            'closing':      angka(r'Closing Balance\s+(-?[\d,]+\.\d{2})',
                                  self._parse_amount),
        }
        return out if any(v is not None for v in out.values()) else None

    def _y_teks(self, page, kata: str):
        """Posisi Y kemunculan pertama sebuah kata di halaman (None kalau tidak ada)."""
        for w in page.extract_words():
            if w['text'] == kata:
                return w['top']
        return None

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
                    '_no_rekening': doc['meta']['no_rekening']}

        buckets = {}
        awal_bulan = {}     # (tahun, bulan) -> saldo awal bulan itu
        prev = None
        for d in sorted(covered):
            # Opening Balance yang tercetak di kepala laporan bersifat
            # otoritatif untuk hari itu — dipakai juga di tengah dokumen,
            # bukan hanya di hari pertama. Kalau PDF gabungan memuat celah
            # tanggal antar laporan, saldo laporan berikutnya tidak boleh
            # ditimpa saldo berjalan yang terbawa dari laporan sebelumnya.
            if d in opening_on:
                prev = opening_on[d]
            # Saldo awal sebuah bulan = saldo sebelum transaksi hari pertama.
            if (d.year, d.month) not in awal_bulan:
                awal_bulan[(d.year, d.month)] = prev
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
        # menampilkannya sebagai indikator tanpa tahu format Rekening Koran.
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
                'Nama Pengirim/Penerima': extract_nama(keterangan),
                'Keterangan Transaksi': keterangan,
            })

        return {b: pd.DataFrame(v) for b, v in buckets.items() if v}

    # ------------------------------------------------------------------ #
    #  VALIDASI OTOMATIS (CHECKSUM)                                      #
    # ------------------------------------------------------------------ #

    def validate(self) -> dict:
        """
        Cocokkan hasil parsing dengan angka resmi yang tercetak di tiap
        laporan: No of Debit/Credit, Total Amount Debited/Credited, Opening
        Balance, dan Closing Balance.

        Pencocokan dilakukan PER LAPORAN (bukan per bulan), karena satu PDF
        bisa memuat beberapa laporan bulanan yang digabung.

        Mengembalikan {'ok': bool, 'periods': [...], 'warnings': [...]}.
        """
        doc = self._parse_document()
        report = {'ok': True, 'periods': [], 'warnings': list(self.warnings)}

        if not doc['periods']:
            report['ok'] = False
            report['warnings'].append(
                'Tidak ada kepala laporan (Account No / Period) yang terbaca — '
                'PDF kemungkinan bukan format Laporan Rekening Koran Mandiri.'
            )
            return report

        if not doc['rows']:
            report['ok'] = False
            report['warnings'].append(
                'Kepala laporan terbaca tetapi tidak ada baris transaksi yang '
                'terdeteksi.'
            )

        report['duplikat_digabung'] = len(doc['rows']) - len(self._merged_rows())
        overlap = self._overlap_warning()
        if overlap:
            report['warnings'].append(overlap)

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
                     if per.get('start') else f"laporan {idx + 1}")

            checks = {}
            for key in ('n_debit', 'n_credit', 'total_debit', 'total_credit', 'closing'):
                expected, actual = per[key], got[key]
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
                report['warnings'].append(
                    f"Periode {label}: Opening Balance tidak terbaca dari PDF."
                )
            else:
                # Rantai saldo berjalan: saldo tiap baris harus sama dengan
                # saldo sebelumnya + kredit - debit. Ini pemeriksaan bebas
                # yang menangkap baris terlewat atau nominal salah kolom,
                # dan hanya berlaku DI DALAM satu laporan — antar laporan
                # bisa ada celah tanggal yang sah.
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
                    report['ok'] = False
                    report['warnings'].append(
                        f"Periode {label}: rantai saldo berjalan putus di "
                        f"{putus} baris — ada transaksi terlewat atau salah baca."
                    )

            report['periods'].append({
                'label': label,
                'bulan': BULAN_ORDER[per['start'].month - 1] if per.get('start') else '-',
                'tahun': per['start'].year if per.get('start') else '-',
                'expected': per,
                'actual': got,
                'checks': checks,
            })

        report['warnings'].extend(self._peringatan_sambungan(doc['periods'], report))
        return report

    def _peringatan_sambungan(self, periods: list, report: dict) -> list:
        """
        Periksa sambungan antar laporan yang berurutan dalam satu PDF.

        Dua hal yang berbeda sifatnya:
          - Laporan berikutnya mulai lebih dari sehari setelah laporan
            sebelumnya berakhir → ada rentang tanggal yang TIDAK tercakup.
            Ini bisa sah (mis. sengaja hanya mengunggah dua bulan terpisah),
            jadi cukup jadi peringatan.
          - Laporan yang bersambung persis (mulai sehari setelahnya) tapi
            Closing Balance-nya tidak sama dengan Opening Balance laporan
            berikutnya → kedua dokumen saling bertentangan. Itu kejanggalan
            data, jadi menggagalkan checksum.
        """
        pesan = []
        urut = sorted([p for p in periods if p.get('start') and p.get('end')],
                      key=lambda p: p['start'])
        for a, b in zip(urut, urut[1:]):
            if b['start'] <= a['end']:
                continue                     # tumpang tindih, sudah dilaporkan
            if b['start'] > a['end'] + timedelta(days=1):
                pesan.append(
                    f"Rentang {a['end'] + timedelta(days=1)}.."
                    f"{b['start'] - timedelta(days=1)} tidak tercakup laporan "
                    f"mana pun — mutasi pada rentang itu tidak ikut terhitung."
                )
                continue
            if a['closing'] is None or b['opening'] is None:
                continue
            if abs(a['closing'] - b['opening']) > 0.005:
                report['ok'] = False
                pesan.append(
                    f"Closing Balance periode {a['start']}..{a['end']} "
                    f"({a['closing']:,.2f}) tidak sama dengan Opening Balance "
                    f"periode {b['start']}..{b['end']} ({b['opening']:,.2f}) "
                    f"padahal keduanya bersambung."
                )
                for per in report['periods']:
                    if per['label'] == f"{b['start']}..{b['end']}":
                        per['checks']['sambungan_saldo'] = False
        return pesan

    # ------------------------------------------------------------------ #
    #  HELPER                                                            #
    # ------------------------------------------------------------------ #

    def _parse_amount(self, s: str):
        s = (s or '').strip()
        if not s or s == '-' or not AMOUNT_RE.match(s):
            return None
        try:
            return float(s.replace(',', ''))
        except ValueError:
            return None

    def _tanggal_aman(self, r: dict):
        """
        date() dari komponen baris; None kalau tanggalnya tidak masuk akal
        (mis. 31 April hasil salah baca) supaya parsing tidak berhenti total.
        """
        try:
            return date(r['year'], r['month'], r['day'])
        except ValueError:
            self.warnings.append(
                f"Tanggal tidak valid pada halaman {r.get('page', '?')}: "
                f"{r['day']:02d}/{r['month']:02d}/{r['year']}."
            )
            return None

    def _ringkas_halaman(self, halaman: list, maks: int = 8) -> str:
        """Daftar nomor halaman yang dipotong supaya pesan tetap terbaca."""
        teks = ', '.join(str(h) for h in halaman[:maks])
        return teks if len(halaman) <= maks else f'{teks}, ... (+{len(halaman) - maks})'
