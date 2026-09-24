"""
campuran.py — Satu PDF yang memuat lebih dari satu format laporan.

Kasus nyatanya: PDF rekening BNI yang disusun dari Account Statement Juni,
Transaction Inquiry Juli, lalu Account Statement Agustus — satu rekening,
tiga potongan, dua tata letak. Dispatcher bank dulu memilih SATU format
untuk seluruh PDF (dari halaman-halaman awal), sehingga halaman Inquiry
diurai dengan tata letak Statement: tidak satu pun transaksinya terbaca,
puluhan baris dilaporkan "tanpa tanggal posting", dan Juni → Agustus
dilaporkan tidak bersambung padahal Juli ada di dokumen yang sama.

Penanganannya di sini, bank-agnostik:
  1. pecah_segmen() mengenali format TIAP halaman lewat fungsi pengenal
     milik dispatcher bank; halaman yang tidak memuat penanda format
     (halaman lanjutan) ikut format halaman sebelumnya. Halaman berurutan
     yang formatnya sama menjadi satu segmen.
  2. Tiap segmen diurai extractor formatnya sendiri, dibatasi ke
     halaman-halaman segmen itu (BaseExtractor.halaman). Nomor halaman
     tetap nomor asli PDF, jadi jejak cetak & peringatan menunjuk halaman
     yang benar.
  3. DokumenCampuran menggabungkan hasil segmen-segmen itu, dan memeriksa
     SAMBUNGAN ANTAR SEGMEN (saldo & tanggal) — pemeriksaan yang di dalam
     tiap extractor hanya berlaku antar blok di segmennya sendiri.

PDF yang hanya memuat satu format TIDAK melewati modul ini sama sekali:
dispatcher tetap memakai extractor formatnya langsung, persis seperti
sebelumnya.

Aturannya disamakan dengan penggabungan beberapa berkas
(engine/multi_pdf_merger.py): seluruh segmen harus satu nomor rekening.
Bulan yang sama boleh muncul di dua segmen (mis. Statement 1–15 Juli +
Inquiry 16–31 Juli) selama tahunnya sama — hari-harinya digabung.
"""

from datetime import date

import pandas as pd
import pdfplumber

from engine.multi_pdf_merger import MergeValidationError
from extractors.base import BaseExtractor
from extractors.peringatan import pencatat_laporan

BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]

# Metadata yang digabung dengan aturan khusus di DokumenCampuran, bukan
# lewat _gabung() generik.
_KHUSUS = ('_no_rekening', '_nama_pemilik', '_jenis_rekening',
           '_peringatan', '_checksum', '_provenance')


def pecah_segmen(pdf, kenali) -> list:
    """
    Kelompokkan halaman PDF menurut formatnya.

    Args:
        pdf:     objek pdfplumber yang sudah dibuka.
        kenali:  fungsi teks_halaman -> kode format, atau None kalau halaman
                 itu tidak memuat penanda format apa pun.

    Returns:
        list of {'format': str, 'halaman': [int, ...]} menurut urutan cetak.
        Kosong kalau tidak satu halaman pun dikenali.
    """
    segmen = []
    menunggu = []   # halaman sebelum penanda format pertama
    for page in pdf.pages:
        fmt = kenali(page.extract_text() or '')
        if fmt is None:
            if segmen:
                segmen[-1]['halaman'].append(page.page_number)
            else:
                menunggu.append(page.page_number)
            continue
        if segmen and segmen[-1]['format'] == fmt:
            segmen[-1]['halaman'].append(page.page_number)
        else:
            segmen.append({'format': fmt, 'halaman': [page.page_number]})
    if segmen and menunggu:
        segmen[0]['halaman'] = menunggu + segmen[0]['halaman']
    return segmen


def bangun_extractor(pdf_path: str, format_awal: str, kelas_format: dict,
                     nama_format: dict, kenali, prefix: str,
                     toleransi: float) -> tuple:
    """
    Pilih extractor untuk sebuah PDF: satu format → extractor formatnya
    langsung; beberapa format → DokumenCampuran atas segmen-segmennya.

    PDF-nya dibuka SEKALI: halaman yang sudah diurai untuk mengenali
    formatnya dipakai ulang oleh extractor (BaseExtractor.urai_dari), jadi
    pengenalan per halaman tidak menggandakan waktu ekstraksi.

    Args:
        format_awal:  format hasil deteksi dispatcher dari halaman-halaman
                      awal — tetap dipakai kalau PDF-nya satu format, supaya
                      perilaku PDF biasa tidak berubah sama sekali.
        kelas_format: kode format -> kelas extractor.
        nama_format:  kode format -> nama untuk pesan & peringatan.
        kenali:       teks halaman -> kode format / None (lihat pecah_segmen).

    Returns:
        (format_type, extractor) — format_type 'campuran' untuk PDF campuran.
    """
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception:
        # PDF tak terbuka: biarkan extractor formatnya yang melaporkan.
        return format_awal, kelas_format[format_awal](pdf_path)

    with pdf:
        segmen = pecah_segmen(pdf, kenali)
        if len(segmen) > 1:
            bagian = [(nama_format[s['format']],
                       kelas_format[s['format']](pdf_path, halaman=s['halaman']))
                      for s in segmen]
            for _, ex in bagian:
                ex.urai_dari(pdf)
            return 'campuran', DokumenCampuran(pdf_path, bagian, prefix, toleransi)

        ex = kelas_format[format_awal](pdf_path)
        ex.urai_dari(pdf)
        return format_awal, ex


def _rentang(halaman: list) -> str:
    a, b = min(halaman), max(halaman)
    return f'halaman {a}' if a == b else f'halaman {a}–{b}'


def _gabung(lama, baru):
    """Metadata generik: dict digabung per kunci, list disambung, skalar
    diambil dari segmen yang lebih akhir (kronologis)."""
    if isinstance(lama, dict) and isinstance(baru, dict):
        hasil = dict(lama)
        for k, v in baru.items():
            hasil[k] = _gabung(hasil[k], v) if k in hasil else v
        return hasil
    if isinstance(lama, list) and isinstance(baru, list):
        return lama + [x for x in baru if x not in lama]
    return baru


class DokumenCampuran(BaseExtractor):
    """
    Extractor gabungan untuk satu PDF yang memuat beberapa format.

    Args:
        pdf_path:   PDF utuhnya.
        segmen:     list of (nama_format, extractor_segmen) menurut urutan
                    cetak; extractor_segmen sudah dibatasi ke halamannya.
        prefix:     awalan nama berkas Excel (mis. 'BNI').
        toleransi:  selisih saldo yang masih dianggap bersambung.
    """

    def __init__(self, pdf_path: str, segmen: list, prefix: str,
                 toleransi: float = 0.005):
        super().__init__(pdf_path)
        self.segmen = [
            {'format': nama, 'extractor': ex,
             'label': f'{_rentang(ex.halaman)} ({nama})'}
            for nama, ex in segmen
        ]
        self.prefix = prefix
        self.toleransi = toleransi
        self._cache = None

    def get_file_prefix(self) -> str:
        return self.prefix

    # ------------------------------------------------------------------ #
    #  HASIL PER SEGMEN                                                  #
    # ------------------------------------------------------------------ #

    def _bagian(self) -> list:
        """Hasil ekstraksi tiap segmen + batas periodenya (di-cache)."""
        if self._cache is not None:
            return self._cache
        for seg in self.segmen:
            ex = seg['extractor']
            seg['saldo'] = ex.extract_saldo()
            seg['transaksi'] = ex.extract_transaksi()
            seg.update(self._batas(seg['saldo']))

        nomor = {}
        for seg in self.segmen:
            no = seg['saldo'].get('_no_rekening')
            if no and no != 'unknown':
                nomor.setdefault(no, []).append(seg['label'])
        if len(nomor) > 1:
            rincian = '; '.join(f'{no} di {", ".join(lbl)}'
                                for no, lbl in nomor.items())
            raise MergeValidationError(
                f'PDF ini memuat lebih dari satu format laporan, dan nomor '
                f'rekeningnya tidak sama: {rincian}. Pastikan seluruh halaman '
                f'PDF berasal dari rekening yang sama.')

        self._cache = self.segmen
        return self._cache

    @staticmethod
    def _batas(saldo: dict) -> dict:
        """Hari pertama & terakhir yang dicakup segmen, beserta saldonya."""
        bulan = sorted(
            (int(v['tahun']), BULAN_ORDER.index(k) + 1, k)
            for k, v in saldo.items()
            if not k.startswith('_') and k in BULAN_ORDER
        )
        if not bulan:
            return {'mulai': None, 'selesai': None, 'awal': None, 'akhir': None}
        tahun, bl, nama = bulan[0]
        mulai = date(tahun, bl, int(saldo[nama]['df']['Tanggal'].min()))
        awal = saldo.get(f'_saldo_awal_{nama}')

        tahun, bl, nama = bulan[-1]
        terakhir = saldo[nama]['df'].sort_values('Tanggal').iloc[-1]
        selesai = date(tahun, bl, int(terakhir['Tanggal']))
        akhir = terakhir['Saldo Akhir Harian']
        if akhir is not None and pd.isna(akhir):
            akhir = None
        return {'mulai': mulai, 'selesai': selesai, 'awal': awal, 'akhir': akhir}

    def _kronologis(self) -> list:
        """Segmen berperiode, diurutkan menurut tanggal mulainya."""
        return sorted((s for s in self._bagian() if s['mulai'] is not None),
                      key=lambda s: s['mulai'])

    # ------------------------------------------------------------------ #
    #  KONTRAK BaseExtractor                                             #
    # ------------------------------------------------------------------ #

    def extract_no_rekening(self) -> str:
        return self._bagian()[0]['saldo'].get('_no_rekening', 'unknown')

    def extract_saldo(self) -> dict:
        bagian = self._bagian()
        pertama = bagian[0]['saldo']
        hasil = {
            '_nama_pemilik': pertama.get('_nama_pemilik', '-'),
            '_no_rekening': pertama.get('_no_rekening', 'unknown'),
        }
        # Tidak semua format mencetak jenis rekening (mis. Inquiry BNI):
        # ambil dari segmen pertama yang memuatnya.
        jenis = next((s['saldo'].get('_jenis_rekening') for s in bagian
                      if s['saldo'].get('_jenis_rekening') not in (None, '-')),
                     None)
        if jenis is not None:
            hasil['_jenis_rekening'] = jenis

        for seg in self._kronologis():
            for kunci, nilai in seg['saldo'].items():
                if kunci in _KHUSUS:
                    continue
                if kunci.startswith('_saldo_awal_'):
                    # Saldo awal bulan milik segmen yang paling awal.
                    hasil.setdefault(kunci, nilai)
                elif kunci.startswith('_'):
                    hasil[kunci] = (_gabung(hasil[kunci], nilai)
                                    if kunci in hasil else nilai)
                elif kunci not in hasil:
                    hasil[kunci] = nilai
                elif hasil[kunci]['tahun'] != nilai['tahun']:
                    raise MergeValidationError(
                        f"Bulan '{kunci}' muncul dengan tahun berbeda "
                        f"({hasil[kunci]['tahun']} dan {nilai['tahun']}) di "
                        f"bagian-bagian PDF ini. Satu laporan hanya bisa "
                        f"memuat satu {kunci}.")
                else:
                    # Bulan yang terbelah dua segmen: gabung harinya. Hari
                    # yang dicakup keduanya (periode tumpang tindih — ikut
                    # dilaporkan validate()) memakai saldo segmen terakhir.
                    df = (pd.concat([hasil[kunci]['df'], nilai['df']])
                          .drop_duplicates('Tanggal', keep='last')
                          .sort_values('Tanggal')
                          .reset_index(drop=True))
                    hasil[kunci] = {**hasil[kunci], 'df': df}

        laporan = self.validate()
        if laporan.get('peringatan'):
            hasil['_peringatan'] = laporan['peringatan']

        # Jejak cetak & checksum menurut urutan cetak: nomor halamannya
        # nomor asli PDF, jadi tidak ada yang bertabrakan antar segmen.
        halaman, baris, checksum = [], [], []
        for seg in bagian:
            prov = seg['saldo'].get('_provenance') or {}
            halaman += prov.get('halaman', [])
            baris += prov.get('baris', [])
            checksum += seg['saldo'].get('_checksum', [])
        hasil['_provenance'] = {'halaman': halaman, 'baris': baris}
        if checksum:
            hasil['_checksum'] = checksum
        return hasil

    def extract_transaksi(self) -> dict:
        hasil = {}
        for seg in self._kronologis():
            for bulan, df in seg['transaksi'].items():
                hasil[bulan] = (pd.concat([hasil[bulan], df], ignore_index=True)
                                if bulan in hasil else df)
        return hasil

    def validate(self) -> dict:
        bagian = self._bagian()
        laporan = {'ok': True, 'periods': [], 'warnings': [], 'peringatan': []}
        digabung = 0
        for seg in bagian:
            lap = seg['extractor'].validate()
            laporan['ok'] = laporan['ok'] and lap.get('ok', True)
            laporan['periods'] += lap.get('periods', [])
            laporan['warnings'] += lap.get('warnings', [])
            laporan['peringatan'] += lap.get('peringatan', [])
            digabung += lap.get('duplikat_digabung') or 0
        if digabung:
            laporan['duplikat_digabung'] = digabung

        catat = pencatat_laporan(laporan)
        catat('Rendah', 'Dokumen memuat lebih dari satu format laporan',
              '; '.join(s['label'] for s in bagian) + '. Tiap bagian diurai '
              'dengan tata letak formatnya masing-masing, lalu digabung; '
              'sambungan saldo & tanggal antar bagian ikut diperiksa.')

        urut = self._kronologis()
        for a, b in zip(urut, urut[1:]):
            self._periksa_sambungan(a, b, catat)
        return laporan

    def _periksa_sambungan(self, a: dict, b: dict, catat) -> None:
        """Sambungan antar segmen (kronologis): saldo dan tanggalnya."""
        if (a['akhir'] is not None and b['awal'] is not None
                and abs(float(a['akhir']) - float(b['awal'])) > self.toleransi):
            catat('Tinggi', 'Saldo antar periode laporan tidak bersambung',
                  f'Saldo akhir {a["label"]} per {a["selesai"]:%d-%m-%Y} '
                  f'{float(a["akhir"]):,.2f} tidak sama dengan saldo awal '
                  f'{b["label"]} per {b["mulai"]:%d-%m-%Y} '
                  f'{float(b["awal"]):,.2f} — ada periode yang tidak '
                  f'disertakan atau angkanya tidak konsisten.')

        jarak = (b['mulai'] - a['selesai']).days
        if jarak > 1:
            catat('Tinggi', 'Ada rentang tanggal yang tidak dicakup laporan',
                  f'{a["label"]} berakhir {a["selesai"]:%d-%m-%Y}, '
                  f'{b["label"]} mulai {b["mulai"]:%d-%m-%Y} — '
                  f'{jarak - 1} hari tidak dicakup laporan mana pun.')
        elif jarak < 1:
            catat('Sedang', 'Periode laporan tumpang tindih',
                  f'{a["label"]} ({a["mulai"]:%d-%m-%Y} s.d. '
                  f'{a["selesai"]:%d-%m-%Y}) dan {b["label"]} '
                  f'({b["mulai"]:%d-%m-%Y} s.d. {b["selesai"]:%d-%m-%Y}) '
                  f'beririsan tanggal — transaksi yang sama bisa terhitung '
                  f'dua kali.')
