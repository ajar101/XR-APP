"""
bri.py — Dispatcher untuk berbagai format rekening BRI.

Auto-detect format PDF BRI dan delegate ke extractor yang sesuai:
  - 'statement' : LAPORAN TRANSAKSI FINANSIAL (e-statement BRImo/Internet
                  Banking, tabel bergaris Tanggal Transaksi … Saldo)
                                                             → didukung
  - lainnya     : belum ada extractor-nya

Satu format ini mencakup SEMUA jenis rekening BRI (Giro Umum, BritAma,
BritAma Bisnis/X, Simpedes, beserta varian SME-nya): yang berbeda hanya isi
baris "Nama Produk", bukan tata letaknya. Jadi tidak ada cabang per jenis
rekening di sini — jenis rekening dibaca sebagai data oleh extractor-nya.

Format BRI lain (mis. cetakan teller di kantor cabang) ditolak di sini
dengan pesan yang menyebut format apa yang didukung. Menyerahkannya ke
extractor yang tata letaknya berbeda tidak menghasilkan penolakan, melainkan
ekstraksi kosong yang baru meledak di pembuat Excel — kegagalan yang jauh
dari sebabnya dan tidak memberi tahu apa pun ke pemakainya.
"""

import pdfplumber

from extractors.base import BaseExtractor
from extractors.bri_statement import BRIStatementExtractor


class BRIExtractor(BaseExtractor):
    """Extractor BRI dengan deteksi format otomatis."""

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        self.format_type = self._detect_format()

        if self.format_type == 'statement':
            self.extractor = BRIStatementExtractor(pdf_path)
        else:
            raise NotImplementedError(
                "PDF ini tidak terbaca sebagai laporan transaksi BRI yang "
                "didukung saat ini, yaitu \"LAPORAN TRANSAKSI FINANSIAL\" "
                "(Statement of Financial Transaction) dengan kolom Tanggal "
                "Transaksi, Uraian Transaksi, Teller User ID, Debet, Kredit, "
                "dan Saldo. Pastikan PDF-nya diunduh langsung dari BRImo atau "
                "BRI Internet Banking."
            )

    def _detect_format(self) -> str:
        """
        Kenali format dari halaman-halaman awal.

        Yang dicari judul laporannya SEKALIGUS nama-nama kolom tabelnya.
        Judul saja tidak cukup: berkas gabungan bisa dibuka dengan halaman
        pengantar tanpa tabel, dan nama kolom saja tidak cukup karena kata
        "Uraian Transaksi" juga dipakai bank lain.
        """
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                for page in pdf.pages[:5]:
                    teks = (page.extract_text() or '').upper()
                    if ('LAPORAN TRANSAKSI FINANSIAL' in teks
                            and 'URAIAN TRANSAKSI' in teks
                            and 'TELLER' in teks):
                        return 'statement'
        except Exception:
            # PDF-nya sendiri tidak bisa dibuka: biarkan alur ekstraksi
            # normal yang melaporkan errornya, jangan salah diagnosis di sini.
            return 'tidak dikenal'
        return 'tidak dikenal'

    def get_file_prefix(self) -> str:
        return 'BRI'

    def extract_saldo(self) -> dict:
        return self.extractor.extract_saldo()

    def extract_transaksi(self) -> dict:
        return self.extractor.extract_transaksi()

    def extract_no_rekening(self) -> str:
        return self.extractor.extract_no_rekening()

    def validate(self) -> dict:
        """
        Teruskan checksum sub-extractor.

        Tanpa delegasi ini app.py hanya melihat dispatcher, yang tidak punya
        validate(), sehingga pemeriksaan checksum diam-diam tidak pernah jalan.
        """
        return self.extractor.validate()
