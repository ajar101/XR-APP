"""
base.py — Kontrak (abstract class) untuk semua extractor bank.

Setiap extractor bank baru WAJIB mewarisi BaseExtractor dan mengimplementasikan:
  - extract_saldo()      → dict struktur saldo_per_bulan
  - extract_transaksi()  → dict struktur transaksi_per_bulan

Kontrak output harus diikuti persis agar engine (Sheet 3–8) bisa bekerja
tanpa modifikasi apapun, tidak peduli bank apa yang diproses.
"""

from abc import ABC, abstractmethod


class BaseExtractor(ABC):
    """
    Abstract base class untuk semua extractor rekening koran.

    Output yang dihasilkan harus mengikuti kontrak berikut:

    extract_saldo() → dict:
        {
            'NamaBulan': {
                'df': pd.DataFrame(columns=['Bulan', 'Tanggal', 'Saldo Akhir Harian']),
                'tahun': str
            },
            ...
            # Metadata (prefix _):
            '_nama_pemilik': str,
            '_no_rekening':  str,
            '_jenis_rekening': str,        # opsional
            '_saldo_awal_NamaBulan': int,  # opsional, satu per bulan

            # Opsional — ketentuan bank yang hanya diketahui extractor-nya.
            # Engine tidak tahu bank apa pun; ia hanya membaca struktur ini.
            # Extractor yang tidak mengirimnya membuat pemeriksaan terkait
            # dilewati (bukan menebak, bukan false-positive).
            '_biaya_admin': {
                'label_nama': str,   # nilai kolom 'Nama Pengirim/Penerima' yang
                                     # menandai baris biaya admin rekening.
                                     # Dicocokkan PERSIS SAMA, supaya biaya lain
                                     # yang namanya mirip tidak ikut terperiksa.
                'jadwal': {          # satu entri per bulan yang dicakup laporan
                    'NamaBulan': {
                        'tanggal': int,   # tanggal seharusnya biaya itu didebet
                        'aturan':  str,   # penjelasan singkat untuk pesan temuan
                    },
                },
            },
            '_bunga_pajak': {
                # Kolom mana yang dipakai untuk mengenali baris bunga & pajak
                # bunga, plus daftar nilainya yang dicocokkan PERSIS SAMA.
                # Kolomnya bisa dipilih karena tiap bank punya kolom yang
                # andal berbeda: keterangan BCA sudah berupa label bersih
                # ('BUNGA'), sedangkan keterangan Mandiri selalu berekor kode
                # cabang sehingga yang andal adalah kolom nama.
                'kolom': "Keterangan Transaksi" | "Nama Pengirim/Penerima",
                'bunga': [str, ...],
                'pajak': [str, ...],
            },
            '_checksum': [   # opsional — hasil pencocokan dengan ringkasan resmi PDF
                {'label': str, 'bulan': str,
                 'expected': {...}, 'actual': {...}},
            ],

            # Opsional — hal-hal yang DIKETAHUI EXTRACTOR saat membaca PDF dan
            # perlu dilihat pemeriksa, tapi bukan hasil pencocokan angka
            # ringkasan (itu lewat '_checksum').
            #
            # Contoh nyata yang harus sampai ke pemeriksa: satu PDF ternyata
            # memuat 60 halaman rekening bank LAIN yang ikut ter-merge, atau
            # ada rentang tanggal yang tidak dicakup laporan mana pun.
            # Sebelum kunci ini ada, temuan seperti itu berhenti di dalam
            # extractor — laporan Excel-nya terlihat bersih padahal dokumen
            # sumbernya bermasalah. Diam bukan pilihan yang aman untuk
            # laporan yang dipakai menilai rekening.
            #
            # Engine hanya membaca strukturnya, tidak tahu bank apa pun.
            # Extractor yang tidak mengirimnya cukup dilewati.
            '_peringatan': [
                {
                    'tingkat': 'Tinggi' | 'Sedang' | 'Rendah',
                    'ringkas': str,   # satu kalimat, jadi "Deskripsi Temuan"
                    'detail':  str,   # bukti/angka pendukung, boleh kosong
                    'bulan':   str,   # opsional, default '-'
                    'halaman': str,   # opsional, default '-'
                },
            ],
        }

    extract_transaksi() → dict:
        {
            'NamaBulan': pd.DataFrame(columns=[
                'Bulan', 'Tanggal', 'Jenis Mutasi',
                'Mutasi', 'Nama Pengirim/Penerima', 'Keterangan Transaksi'
            ]),
            ...
        }
    """

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path

    @abstractmethod
    def extract_saldo(self) -> dict:
        """
        Ekstrak saldo akhir harian dari PDF rekening koran.
        Wajib mengembalikan dict sesuai kontrak di atas.
        """
        raise NotImplementedError

    @abstractmethod
    def extract_transaksi(self) -> dict:
        """
        Ekstrak detail transaksi dari PDF rekening koran.
        Wajib mengembalikan dict sesuai kontrak di atas.
        """
        raise NotImplementedError

    def extract_no_rekening(self) -> str:
        """
        Ekstrak nomor rekening untuk penamaan file output.
        Default: kembalikan 'unknown'. Override di subclass jika perlu.
        """
        return 'unknown'

    def get_file_prefix(self) -> str:
        """
        Prefix nama file output Excel, misal 'BCA', 'MANDIRI', dll.
        Override di subclass.
        """
        return 'BANK'
