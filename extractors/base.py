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

            # Opsional — JEJAK CETAK: di mana tiap baris muncul di dokumen dan
            # apa yang tercetak di sebelahnya. Bukan hasil pemeriksaan, hanya
            # fakta mentah.
            #
            # Gunanya: beberapa indikasi hanya bisa dilihat dari susunan
            # dokumen, bukan dari angka mutasinya — saldo berjalan antar baris,
            # nomor halaman yang meloncat, halaman yang kehilangan header
            # kolom. Sebelum kunci ini ada, engine membaca ULANG PDF dengan
            # parser keduanya sendiri; parser kedua itu hanya mengenali tata
            # letak satu bank dan pernah SALAH menemukan (membaca ringkasan
            # bank lain pada PDF gabungan, lalu membandingkannya dengan data
            # rekening yang diperiksa).
            #
            # Extractor sudah mengetahui semua ini saat parsing. Yang
            # diserahkan FAKTA, bukan pola/regex: begitu yang diserahkan pola,
            # parser kedua itu cuma pindah tempat, tidak hilang.
            #
            # Nilai None berarti "dokumen ini memang tidak memuatnya" — bukan
            # nol, bukan tidak ada masalah. Pemeriksaan terkait dilewati,
            # persis seperti perlakuan '_biaya_admin' yang tidak dikirim.
            '_provenance': {
                'halaman': [
                    {
                        'urut': int,              # halaman ke-berapa di PDF (1-based)
                        'no_tercetak': int|None,  # nomor halaman yang TERCETAK
                        'total_tercetak': int|None,
                        'periode': str|None,      # penanda blok laporan halaman ini
                        'ada_header_kolom': bool|None,  # None = format ini memang
                                                        # tidak mencetak header
                                                        # kolom di tiap halaman
                        'jumlah_baris': int,
                    },
                ],
                'baris': [
                    {
                        'bulan': str,
                        'tanggal': int,
                        'halaman': int,           # 'urut' halaman tempat baris ini
                        'urut': int,              # posisi baris di halaman itu
                        'periode': str|None,      # blok laporan; saldo berjalan
                                                  # hanya menyambung DI DALAM blok
                        'mutasi': float,          # bertanda: + kredit, − debit
                        'saldo_tercetak': float|None,
                        'teks_mentah': str|None,  # baris apa adanya — HANYA kalau
                                                  # isinya teks cetak mesin. Kolom
                                                  # keterangan yang memuat berita
                                                  # bebas dari nasabah TIDAK boleh
                                                  # dikirim di sini: angka bergaya
                                                  # Indonesia yang ditulis nasabah
                                                  # di berita transfer bukan
                                                  # artefak dokumen, dan akan jadi
                                                  # temuan palsu.
                    },
                ],
            },
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
